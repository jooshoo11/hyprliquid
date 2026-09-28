"""
Production-Grade Trade Manager & Risk Execution Engine (src/risk/trade_manager.py).

Enforces real-time position-level risk management rules:
1. Peak / Highest Water Mark Tracking:
   - Continuously tracks highest favorable price and peak ROI per position.
2. Breakeven Ratchet:
   - When ROI >= +1.0%, sets stop at +0.1% (covering taker fee).
3. Trailing Stop:
   - When ROI >= +2.0%, trails 0.75% behind peak mark price.
4. Max Adverse Excursion (MAE) Hard Cut:
   - Immediately closes if ROI <= -2.5% or loss <= -$2.00.
5. Stagnant Trade Exit:
   - Closes position if open > 4 hours without moving > 0.3% ROI.
6. Automated Trade Journaling:
   - Appends closed trade details to reports/daily_pnl.md and reports/session_trades.json.
"""

import os
import json
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

REPO_ROOT = str(Path(__file__).resolve().parents[2])


@dataclass
class PositionTracker:
    """Tracks state and risk watermarks for an active open position."""
    coin: str
    side: str  # "LONG" or "SHORT"
    size: float
    entry_price: float
    entry_time: float  # Unix timestamp in seconds
    strategy: str = "Unknown"

    # Watermark tracking
    peak_price: float = 0.0  # Most favorable price reached (max for LONG, min for SHORT)
    highest_mark_price: float = 0.0  # Absolute highest price observed
    lowest_mark_price: float = 0.0   # Absolute lowest price observed
    peak_roi: float = 0.0   # Highest ROI % achieved
    trough_roi: float = 0.0  # Lowest ROI % observed

    # Ratchet stops
    breakeven_triggered: bool = False
    trailing_stop_triggered: bool = False
    stop_price: Optional[float] = None
    atr_pct: float = 0.0  # Coin-specific volatility / ATR percentage
    dynamic_trailing_distance_pct: Optional[float] = None
    take_profit_roi_pct: Optional[float] = None  # Profit target %
    breakeven_roi_pct: Optional[float] = None    # Dynamic breakeven threshold %

    # Current snapshot
    current_price: float = 0.0
    current_roi: float = 0.0
    current_pnl: float = 0.0
    last_update_time: float = 0.0

    def __post_init__(self):
        self.side = self._normalize_side(self.side)
        self.coin = self._normalize_coin(self.coin)
        self.size = abs(float(self.size))
        self.entry_price = float(self.entry_price)
        if self.peak_price == 0.0:
            self.peak_price = self.entry_price
        if self.highest_mark_price == 0.0:
            self.highest_mark_price = self.entry_price
        if self.lowest_mark_price == 0.0:
            self.lowest_mark_price = self.entry_price
        if self.current_price == 0.0:
            self.current_price = self.entry_price

    @staticmethod
    def _normalize_side(side: str) -> str:
        s = str(side).upper()
        if s in ("BUY", "LONG"):
            return "LONG"
        elif s in ("SELL", "SHORT"):
            return "SHORT"
        return s

    @staticmethod
    def _normalize_coin(coin: str) -> str:
        return str(coin).upper().split("-")[0].split(".")[0]


@dataclass
class TradeAction:
    """Evaluation result for a managed position."""
    action: str  # "HOLD", "CLOSE"
    should_close: bool
    reason: str
    coin: str
    side: str
    roi: float
    pnl: float
    current_price: float
    peak_price: float
    stop_price: Optional[float] = None


class TradeManager:
    """
    Production-grade trade manager handling peak water mark tracking,
    breakeven ratchets, trailing stops, MAE hard cuts, stagnant exits,
    and automated trade journaling.
    """

    def __init__(
        self,
        reports_dir: Optional[str] = None,
        daily_pnl_file: Optional[str] = None,
        session_trades_file: Optional[str] = None,
        missed_opportunities_file: Optional[str] = None,
        decision_journal_file: Optional[str] = None,
        breakeven_roi_pct: float = 1.0,         # Breakeven ratchet trigger: +1.0%
        breakeven_stop_roi_pct: float = 0.1,    # Stop set at +0.1% ROI (covers taker fee)
        trailing_roi_pct: float = 2.0,          # Trailing stop trigger: +2.0%
        trailing_distance_pct: float = 0.75,    # Trails 0.75% behind peak mark price
        mae_roi_pct: float = -2.5,              # MAE hard cut ROI: <= -2.5%
        mae_loss_usd: float = -2.00,            # MAE hard cut loss: <= -$2.00
        stagnant_hours: float = 4.0,            # Stagnant duration: > 4.0 hours
        stagnant_roi_pct: float = 0.3,          # Stagnant max movement: <= 0.3% ROI
        taker_fee_pct: float = 0.035,           # Hyperliquid standard 3.5 bps per side
        min_holding_seconds: float = 90.0,      # Minimum holding duration before non-emergency exits
        reentry_cooldown_seconds: float = 180.0,# Cooldown after closing before re-entering (3 mins)
        max_positions: int = 10,                # Max portfolio positions for capital rotation
        take_profit_roi_pct: Optional[float] = None, # Hard Take-Profit target: e.g. +2.0% to +3.5% ROI
        extended_hold_hours: float = 2.0,       # Extended hold duration: > 2.0 hours
        extended_hold_roi_pct: float = 1.5,     # Extended hold profit lock: >= +1.5% ROI
    ) -> None:
        self.reports_dir = reports_dir or os.path.join(REPO_ROOT, "reports")
        self.daily_pnl_file = daily_pnl_file or os.path.join(self.reports_dir, "daily_pnl.md")
        self.session_trades_file = session_trades_file or os.path.join(self.reports_dir, "session_trades.json")
        self.missed_opportunities_file = missed_opportunities_file or os.path.join(self.reports_dir, "missed_opportunities.md")
        self.decision_journal_file = decision_journal_file or os.path.join(self.reports_dir, "decision_journal.jsonl")

        self.breakeven_roi_pct = breakeven_roi_pct
        self.breakeven_stop_roi_pct = breakeven_stop_roi_pct
        self.trailing_roi_pct = trailing_roi_pct
        self.trailing_distance_pct = trailing_distance_pct
        self.mae_roi_pct = mae_roi_pct
        self.mae_loss_usd = mae_loss_usd
        self.stagnant_hours = stagnant_hours
        self.stagnant_roi_pct = stagnant_roi_pct
        self.taker_fee_pct = taker_fee_pct
        self.min_holding_seconds = min_holding_seconds
        self.reentry_cooldown_seconds = reentry_cooldown_seconds
        self.max_positions = max_positions
        self.take_profit_roi_pct = take_profit_roi_pct
        self.extended_hold_hours = extended_hold_hours
        self.extended_hold_roi_pct = extended_hold_roi_pct

        self.active_positions: Dict[str, PositionTracker] = {}
        self._journaled_trade_keys: set = set()
        self._closed_trades_cache: List[Dict[str, Any]] = []
        self._missed_opportunities_cache: List[Dict[str, Any]] = []
        self.cumulative_fees: float = 0.0
        self.cooldown_tracker: Dict[str, float] = {}
        self._lock = threading.Lock()


        # Ensure reports directory exists
        os.makedirs(self.reports_dir, exist_ok=True)

        # Pre-populate in-memory cache from persisted files if present
        self._load_persisted_data()

    def _get_key(self, coin: str) -> str:
        return PositionTracker._normalize_coin(coin)

    def register_position(
        self,
        coin: str,
        side: str,
        size: float,
        entry_price: float,
        strategy: str = "Unknown",
        entry_time: Optional[float] = None,
        atr_pct: Optional[float] = None,
        take_profit_roi_pct: Optional[float] = None,
    ) -> PositionTracker:
        """Register or reset an active position for tracking."""
        key = self._get_key(coin)
        now = time.time() if entry_time is None else entry_time
        with self._lock:
            tracker = PositionTracker(
                coin=key,
                side=side,
                size=size,
                entry_price=entry_price,
                entry_time=now,
                strategy=strategy,
                last_update_time=now,
                take_profit_roi_pct=take_profit_roi_pct if take_profit_roi_pct is not None else self.take_profit_roi_pct,
            )
            if atr_pct is not None and atr_pct > 0:
                tracker.atr_pct = float(atr_pct)
                tracker.dynamic_trailing_distance_pct = round(max(0.5, min(2.5, 1.5 * tracker.atr_pct)), 2)
            self.active_positions[key] = tracker
            return tracker

    def sync_active_positions(self, current_open_coins: Any) -> List[str]:
        """Prune any phantom positions in trade_manager that are no longer open in engine."""
        normalized_current = {PositionTracker._normalize_coin(c) for c in current_open_coins}
        pruned = []
        with self._lock:
            for coin_key in list(self.active_positions.keys()):
                if coin_key not in normalized_current:
                    self.active_positions.pop(coin_key, None)
                    pruned.append(coin_key)
        return pruned

    def update_position(
        self,
        coin: str,
        side: str,
        size: float,
        entry_price: float,
        mark_price: float,
        strategy: str = "Unknown",
        entry_time: Optional[float] = None,
        current_time: Optional[float] = None,
        exit_signal: Optional[str] = None,
        atr_pct: Optional[float] = None,
        take_profit_roi_pct: Optional[float] = None,
        breakeven_roi_pct: Optional[float] = None,
    ) -> TradeAction:
        """
        Update position with the latest mark price, recalculate metrics and watermarks,
        and evaluate risk rules (MAE, Take Profit, trailing stop, breakeven ratchet, stagnant exit, min holding period).
        """
        key = self._get_key(coin)
        now = time.time() if current_time is None else current_time

        with self._lock:
            tracker = self.active_positions.get(key)
            if tracker is None:
                tracker = PositionTracker(
                    coin=key,
                    side=side,
                    size=size,
                    entry_price=entry_price,
                    entry_time=now if entry_time is None else entry_time,
                    strategy=strategy,
                    last_update_time=now,
                    take_profit_roi_pct=take_profit_roi_pct if take_profit_roi_pct is not None else self.take_profit_roi_pct,
                    breakeven_roi_pct=breakeven_roi_pct,
                )
                self.active_positions[key] = tracker
            else:
                # Update metadata if changed
                tracker.size = abs(float(size))
                if entry_price > 0:
                    tracker.entry_price = float(entry_price)
                if strategy and strategy != "Unknown":
                    tracker.strategy = strategy
                if entry_time is not None:
                    tracker.entry_time = entry_time

            if atr_pct is not None and atr_pct > 0:
                tracker.atr_pct = float(atr_pct)
                tracker.dynamic_trailing_distance_pct = round(max(0.5, min(2.5, 1.5 * tracker.atr_pct)), 2)

            if take_profit_roi_pct is not None and take_profit_roi_pct > 0:
                tracker.take_profit_roi_pct = float(take_profit_roi_pct)

            if breakeven_roi_pct is not None and breakeven_roi_pct > 0:
                tracker.breakeven_roi_pct = float(breakeven_roi_pct)

            # Update current price and timestamp
            mark_price = float(mark_price)
            tracker.current_price = mark_price
            tracker.last_update_time = now

            # Absolute price watermarks
            tracker.highest_mark_price = max(tracker.highest_mark_price, mark_price)
            tracker.lowest_mark_price = min(tracker.lowest_mark_price, mark_price)

            # Calculate ROI & PnL
            if tracker.entry_price > 0:
                if tracker.side == "LONG":
                    roi = ((mark_price - tracker.entry_price) / tracker.entry_price) * 100.0
                    pnl = (mark_price - tracker.entry_price) * tracker.size
                    tracker.peak_price = max(tracker.peak_price, mark_price)
                else:  # SHORT
                    roi = ((tracker.entry_price - mark_price) / tracker.entry_price) * 100.0
                    pnl = (tracker.entry_price - mark_price) * tracker.size
                    tracker.peak_price = min(tracker.peak_price, mark_price)
            else:
                roi = 0.0
                pnl = 0.0

            tracker.current_roi = roi
            tracker.current_pnl = pnl
            tracker.peak_roi = max(tracker.peak_roi, roi)
            tracker.trough_roi = min(tracker.trough_roi, roi)

            duration_seconds = max(0.0, now - tracker.entry_time)

            # --- RISK RULE EVALUATION ---

            # Rule 1: Max Adverse Excursion (MAE) hard cut
            # Closes immediately if ROI <= -2.5% or loss <= -$2.00 regardless of holding time
            if roi <= self.mae_roi_pct or pnl <= self.mae_loss_usd:
                reason = (
                    f"MAE hard cut: ROI {roi:+.2f}% <= {self.mae_roi_pct:.1f}% "
                    f"or loss ${pnl:+.2f} <= ${self.mae_loss_usd:.2f}"
                )
                return TradeAction(
                    action="CLOSE",
                    should_close=True,
                    reason=reason,
                    coin=tracker.coin,
                    side=tracker.side,
                    roi=roi,
                    pnl=pnl,
                    current_price=mark_price,
                    peak_price=tracker.peak_price,
                    stop_price=tracker.stop_price,
                )

            # Rule 2: Explicit exit signal (e.g. orderbook flicker)
            # If a non-emergency exit is signaled before min_holding_seconds, suppress it
            if exit_signal is not None:
                if duration_seconds < self.min_holding_seconds:
                    reason = f"HOLD (Min holding period active: {int(duration_seconds)}s / {int(self.min_holding_seconds)}s)"
                    return TradeAction(
                        action="HOLD",
                        should_close=False,
                        reason=reason,
                        coin=tracker.coin,
                        side=tracker.side,
                        roi=roi,
                        pnl=pnl,
                        current_price=mark_price,
                        peak_price=tracker.peak_price,
                        stop_price=tracker.stop_price,
                    )
                else:
                    return TradeAction(
                        action="CLOSE",
                        should_close=True,
                        reason=exit_signal,
                        coin=tracker.coin,
                        side=tracker.side,
                        roi=roi,
                        pnl=pnl,
                        current_price=mark_price,
                        peak_price=tracker.peak_price,
                        stop_price=tracker.stop_price,
                    )

            # Rule 2.5: Take Profit Target Lock & Extended Hold Profit Banking
            # Banks profit immediately when ROI >= take_profit_roi_pct or when position held > extended_hold_hours with >= extended_hold_roi_pct
            tp_target = getattr(tracker, "take_profit_roi_pct", None) or self.take_profit_roi_pct
            is_extended_profit = (duration_seconds >= (self.extended_hold_hours * 3600.0) and roi >= self.extended_hold_roi_pct)
            if (tp_target is not None and roi >= tp_target) or is_extended_profit:
                if duration_seconds >= self.min_holding_seconds:
                    if is_extended_profit and (tp_target is None or roi < tp_target):
                        reason = (
                            f"Extended hold profit lock: open {duration_seconds / 3600.0:.1f}h (> {self.extended_hold_hours}h) "
                            f"with ROI {roi:+.2f}% >= {self.extended_hold_roi_pct:.1f}% (+${pnl:+.2f} banked)"
                        )
                    else:
                        reason = (
                            f"Take Profit target reached: ROI {roi:+.2f}% >= {tp_target:.1f}% "
                            f"(+${pnl:+.2f} banked)"
                        )
                    return TradeAction(
                        action="CLOSE",
                        should_close=True,
                        reason=reason,
                        coin=tracker.coin,
                        side=tracker.side,
                        roi=roi,
                        pnl=pnl,
                        current_price=mark_price,
                        peak_price=tracker.peak_price,
                        stop_price=tracker.stop_price,
                    )

            # Rule 3: Trailing stop
            # When ROI >= +2.0%, trails behind peak mark price (dynamic ATR or configured default)
            if tracker.peak_roi >= self.trailing_roi_pct:
                tracker.trailing_stop_triggered = True
                trail_dist = tracker.dynamic_trailing_distance_pct if tracker.dynamic_trailing_distance_pct is not None else self.trailing_distance_pct
                if tracker.side == "LONG":
                    trail_stop = tracker.peak_price * (1.0 - (trail_dist / 100.0))
                    if tracker.stop_price is None:
                        tracker.stop_price = trail_stop
                    else:
                        tracker.stop_price = max(tracker.stop_price, trail_stop)

                    if mark_price <= tracker.stop_price:
                        if duration_seconds < self.min_holding_seconds:
                            reason = f"HOLD (Min holding period active: {int(duration_seconds)}s / {int(self.min_holding_seconds)}s)"
                            return TradeAction(
                                action="HOLD",
                                should_close=False,
                                reason=reason,
                                coin=tracker.coin,
                                side=tracker.side,
                                roi=roi,
                                pnl=pnl,
                                current_price=mark_price,
                                peak_price=tracker.peak_price,
                                stop_price=tracker.stop_price,
                            )
                        reason = (
                            f"Trailing stop triggered: price ${mark_price:,.2f} <= stop ${tracker.stop_price:,.2f} "
                            f"(Peak: ${tracker.peak_price:,.2f}, Trail: {trail_dist}%)"
                        )
                        return TradeAction(
                            action="CLOSE",
                            should_close=True,
                            reason=reason,
                            coin=tracker.coin,
                            side=tracker.side,
                            roi=roi,
                            pnl=pnl,
                            current_price=mark_price,
                            peak_price=tracker.peak_price,
                            stop_price=tracker.stop_price,
                        )
                else:  # SHORT
                    trail_stop = tracker.peak_price * (1.0 + (trail_dist / 100.0))
                    if tracker.stop_price is None:
                        tracker.stop_price = trail_stop
                    else:
                        tracker.stop_price = min(tracker.stop_price, trail_stop)

                    if mark_price >= tracker.stop_price:
                        if duration_seconds < self.min_holding_seconds:
                            reason = f"HOLD (Min holding period active: {int(duration_seconds)}s / {int(self.min_holding_seconds)}s)"
                            return TradeAction(
                                action="HOLD",
                                should_close=False,
                                reason=reason,
                                coin=tracker.coin,
                                side=tracker.side,
                                roi=roi,
                                pnl=pnl,
                                current_price=mark_price,
                                peak_price=tracker.peak_price,
                                stop_price=tracker.stop_price,
                            )
                        reason = (
                            f"Trailing stop triggered: price ${mark_price:,.2f} >= stop ${tracker.stop_price:,.2f} "
                            f"(Peak: ${tracker.peak_price:,.2f}, Trail: {trail_dist}%)"
                        )
                        return TradeAction(
                            action="CLOSE",
                            should_close=True,
                            reason=reason,
                            coin=tracker.coin,
                            side=tracker.side,
                            roi=roi,
                            pnl=pnl,
                            current_price=mark_price,
                            peak_price=tracker.peak_price,
                            stop_price=tracker.stop_price,
                        )

            # Rule 4: Breakeven ratchet
            # Dynamically triggers at +0.75% in chop/flush or +1.0% baseline, sets stop at +0.1% (covering taker fee)
            be_thresh = getattr(tracker, "breakeven_roi_pct", None) or self.breakeven_roi_pct
            if tracker.peak_roi >= be_thresh:
                tracker.breakeven_triggered = True
                if tracker.side == "LONG":
                    be_stop = tracker.entry_price * (1.0 + (self.breakeven_stop_roi_pct / 100.0))
                    if tracker.stop_price is None or be_stop > tracker.stop_price:
                        tracker.stop_price = be_stop

                    if mark_price <= tracker.stop_price:
                        if duration_seconds < self.min_holding_seconds:
                            reason = f"HOLD (Min holding period active: {int(duration_seconds)}s / {int(self.min_holding_seconds)}s)"
                            return TradeAction(
                                action="HOLD",
                                should_close=False,
                                reason=reason,
                                coin=tracker.coin,
                                side=tracker.side,
                                roi=roi,
                                pnl=pnl,
                                current_price=mark_price,
                                peak_price=tracker.peak_price,
                                stop_price=tracker.stop_price,
                            )
                        reason = (
                            f"Breakeven ratchet triggered: price ${mark_price:,.2f} <= stop ${tracker.stop_price:,.2f} "
                            f"(+0.1% ROI locked)"
                        )
                        return TradeAction(
                            action="CLOSE",
                            should_close=True,
                            reason=reason,
                            coin=tracker.coin,
                            side=tracker.side,
                            roi=roi,
                            pnl=pnl,
                            current_price=mark_price,
                            peak_price=tracker.peak_price,
                            stop_price=tracker.stop_price,
                        )
                else:  # SHORT
                    be_stop = tracker.entry_price * (1.0 - (self.breakeven_stop_roi_pct / 100.0))
                    if tracker.stop_price is None or be_stop < tracker.stop_price:
                        tracker.stop_price = be_stop

                    if mark_price >= tracker.stop_price:
                        if duration_seconds < self.min_holding_seconds:
                            reason = f"HOLD (Min holding period active: {int(duration_seconds)}s / {int(self.min_holding_seconds)}s)"
                            return TradeAction(
                                action="HOLD",
                                should_close=False,
                                reason=reason,
                                coin=tracker.coin,
                                side=tracker.side,
                                roi=roi,
                                pnl=pnl,
                                current_price=mark_price,
                                peak_price=tracker.peak_price,
                                stop_price=tracker.stop_price,
                            )
                        reason = (
                            f"Breakeven ratchet triggered: price ${mark_price:,.2f} >= stop ${tracker.stop_price:,.2f} "
                            f"(+0.1% ROI locked)"
                        )
                        return TradeAction(
                            action="CLOSE",
                            should_close=True,
                            reason=reason,
                            coin=tracker.coin,
                            side=tracker.side,
                            roi=roi,
                            pnl=pnl,
                            current_price=mark_price,
                            peak_price=tracker.peak_price,
                            stop_price=tracker.stop_price,
                        )

            # Rule 5: Stagnant trade exit
            # Closes position if open > 4 hours without moving > 0.3% ROI
            duration_hours = duration_seconds / 3600.0
            if duration_hours > self.stagnant_hours:
                if abs(tracker.current_roi) <= self.stagnant_roi_pct or (
                    tracker.peak_roi <= self.stagnant_roi_pct and tracker.current_roi <= self.stagnant_roi_pct
                ):
                    reason = (
                        f"Stagnant trade exit: open {duration_hours:.1f}h (> {self.stagnant_hours}h) "
                        f"without moving > {self.stagnant_roi_pct}% ROI "
                        f"(ROI: {tracker.current_roi:+.2f}%, Peak: {tracker.peak_roi:+.2f}%)"
                    )
                    return TradeAction(
                        action="CLOSE",
                        should_close=True,
                        reason=reason,
                        coin=tracker.coin,
                        side=tracker.side,
                        roi=roi,
                        pnl=pnl,
                        current_price=mark_price,
                        peak_price=tracker.peak_price,
                        stop_price=tracker.stop_price,
                    )

            # Normal hold
            return TradeAction(
                action="HOLD",
                should_close=False,
                reason="Normal",
                coin=tracker.coin,
                side=tracker.side,
                roi=roi,
                pnl=pnl,
                current_price=mark_price,
                peak_price=tracker.peak_price,
                stop_price=tracker.stop_price,
            )

    def check_exit_allowed(
        self,
        coin: str,
        is_emergency: bool = False,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Check whether a non-emergency exit is allowed or suppressed by min_holding_seconds.
        Emergency exits (like MAE) are always allowed immediately.
        """
        if is_emergency:
            return True, "Emergency exit allowed"
        key = self._get_key(coin)
        now = time.time() if current_time is None else current_time
        with self._lock:
            tracker = self.active_positions.get(key)
            if tracker is None:
                return False, "HOLD (Position initializing/not yet registered)"
            duration = max(0.0, now - tracker.entry_time)
            if duration < self.min_holding_seconds:
                return False, f"HOLD (Min holding period active: {int(duration)}s / {int(self.min_holding_seconds)}s)"
            return True, "Holding period satisfied"

    def is_in_cooldown(self, coin: str, current_time: Optional[float] = None) -> bool:
        """
        Check if a coin is in the re-entry cooldown period after being closed.
        Returns True if less than reentry_cooldown_seconds have elapsed since close.
        """
        key = self._get_key(coin)
        now = time.time() if current_time is None else current_time
        with self._lock:
            closed_at = self.cooldown_tracker.get(key)
            if closed_at is None:
                return False
            elapsed = now - closed_at
            return elapsed < self.reentry_cooldown_seconds

    def get_cooldown_remaining(self, coin: str, current_time: Optional[float] = None) -> float:
        """Return remaining seconds in cooldown for a coin (0.0 if not in cooldown)."""
        key = self._get_key(coin)
        now = time.time() if current_time is None else current_time
        with self._lock:
            closed_at = self.cooldown_tracker.get(key)
            if closed_at is None:
                return 0.0
            elapsed = now - closed_at
            return max(0.0, round(self.reentry_cooldown_seconds - elapsed, 1))

    def set_cooldown(self, coin: str, timestamp: Optional[float] = None) -> None:
        """Set or update cooldown timestamp for a coin."""
        key = self._get_key(coin)
        now = time.time() if timestamp is None else timestamp
        with self._lock:
            self.cooldown_tracker[key] = now

    def clear_cooldown(self, coin: str) -> None:
        """Clear cooldown for a coin."""
        key = self._get_key(coin)
        with self._lock:
            self.cooldown_tracker.pop(key, None)

    def find_capital_rotation_candidate(
        self,
        new_prospect_coin: str,
        new_prospect_conviction_score: float = 0.0,
        max_positions: Optional[int] = None,
        current_time: Optional[float] = None,
        min_duration_seconds: float = 300.0,
        roi_lower_pct: float = -0.3,
        roi_upper_pct: float = 0.2,
    ) -> Optional[Tuple[str, str]]:
        """
        Opportunity-Cost Capital Rotation ("Close for something better").
        If at or near max positions, identifies the weakest holding:
          - Open for > 300 seconds (5 mins).
          - Stagnant ROI (between -0.3% and +0.2%).
        Returns:
          (coin_to_close, reason) like ("SEI", "Capital rotation: reallocating stagnant capital to high-conviction prospect TAO")
        """
        key_prospect = PositionTracker._normalize_coin(new_prospect_coin)
        now = time.time() if current_time is None else current_time

        with self._lock:
            limit = max_positions if max_positions is not None else self.max_positions
            # Check if at or near max positions: at least (limit - 1) positions open
            if len(self.active_positions) < max(1, limit - 1):
                return None

            # Do not rotate into a coin already actively held
            if key_prospect in self.active_positions:
                return None

            candidates = []
            for coin_key, tracker in self.active_positions.items():
                duration = now - tracker.entry_time
                if duration > min_duration_seconds:
                    if roi_lower_pct <= tracker.current_roi <= roi_upper_pct:
                        candidates.append(tracker)

            if not candidates:
                return None

            # Weakest holding: lowest ROI first, then longest duration
            candidates.sort(key=lambda t: (t.current_roi, -(now - t.entry_time)))
            weakest = candidates[0]

            reason = f"Capital rotation: reallocating stagnant capital to high-conviction prospect {key_prospect}"
            return weakest.coin, reason

    def close_and_journal_position(
        self,
        coin: str,
        exit_price: float,
        reason: str,
        exit_time: Optional[float] = None,
        fallback_side: str = "LONG",
        fallback_size: float = 1.0,
        fallback_entry_price: Optional[float] = None,
        fallback_strategy: str = "Unknown",
    ) -> Dict[str, Any]:
        """
        Closes an active position, computes taker fees (0.035% standard), gross & net PnL,
        sets re-entry cooldown, and records the trade to daily_pnl.md and session_trades.json.
        """
        key = self._get_key(coin)
        now = time.time() if exit_time is None else exit_time

        with self._lock:
            tracker = self.active_positions.pop(key, None)
            self.cooldown_tracker[key] = now

        if tracker is not None:
            side = tracker.side
            size = tracker.size
            entry_price = tracker.entry_price
            strategy = tracker.strategy
            duration_secs = max(0.0, now - tracker.entry_time)
            if side == "LONG":
                gross_pnl = (exit_price - entry_price) * size
                roi = ((exit_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
            else:
                gross_pnl = (entry_price - exit_price) * size
                roi = ((entry_price - exit_price) / entry_price * 100.0) if entry_price > 0 else 0.0
        else:
            side = PositionTracker._normalize_side(fallback_side)
            size = abs(float(fallback_size))
            entry_price = float(fallback_entry_price or exit_price)
            strategy = fallback_strategy
            duration_secs = 0.0
            if side == "LONG":
                gross_pnl = (exit_price - entry_price) * size
                roi = ((exit_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
            else:
                gross_pnl = (entry_price - exit_price) * size
                roi = ((entry_price - exit_price) / entry_price * 100.0) if entry_price > 0 else 0.0

        fees = (entry_price * size + exit_price * size) * (self.taker_fee_pct / 100.0)
        net_pnl = gross_pnl - fees

        return self.record_closed_trade(
            coin=key,
            side=side,
            size=size,
            entry=entry_price,
            exit=exit_price,
            duration=duration_secs,
            pnl=net_pnl,
            roi=roi,
            strategy=strategy,
            reason=reason,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            fees=fees,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            exit_time=now,
        )

    def record_closed_trade(
        self,
        coin: str,
        side: str,
        size: float,
        entry: float,
        exit: float,
        duration: Any,
        pnl: Optional[float] = None,
        roi: Optional[float] = None,
        strategy: str = "Unknown",
        reason: str = "Unknown",
        timestamp: Optional[str] = None,
        fees: Optional[float] = None,
        gross_pnl: Optional[float] = None,
        net_pnl: Optional[float] = None,
        exit_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Appends trade details to reports/daily_pnl.md and reports/session_trades.json.
        Guarantees exact fields required:
        (coin, side, size, entry, exit, duration, pnl, roi, strategy, reason, gross_pnl, fees, net_pnl).
        """
        coin_clean = PositionTracker._normalize_coin(coin)
        side_clean = PositionTracker._normalize_side(side)
        size_clean = round(abs(float(size)), 4)
        entry_clean = round(float(entry), 4)
        exit_clean = round(float(exit), 4)

        # Gross PnL calculation
        if gross_pnl is not None:
            gross_clean = round(float(gross_pnl), 2)
        elif pnl is not None:
            gross_clean = round(float(pnl), 2)
        else:
            if side_clean == "LONG":
                gross_clean = round((exit_clean - entry_clean) * size_clean, 2)
            else:
                gross_clean = round((entry_clean - exit_clean) * size_clean, 2)

        # Taker fees calculation: (entry * size + exit * size) * (taker_fee_pct / 100.0)
        if fees is not None:
            fees_clean = round(float(fees), 2)
        else:
            fees_clean = round((entry_clean * size_clean + exit_clean * size_clean) * (self.taker_fee_pct / 100.0), 2)

        # Net PnL calculation: gross_pnl - fees
        if net_pnl is not None:
            net_clean = round(float(net_pnl), 2)
        else:
            net_clean = round(gross_clean - fees_clean, 2)

        # ROI calculation
        if roi is not None:
            roi_clean = round(float(roi), 2)
        else:
            if entry_clean > 0:
                if side_clean == "LONG":
                    roi_clean = round(((exit_clean - entry_clean) / entry_clean) * 100.0, 2)
                else:
                    roi_clean = round(((entry_clean - exit_clean) / entry_clean) * 100.0, 2)
            else:
                roi_clean = 0.0

        strategy_clean = str(strategy)
        reason_clean = str(reason)
        is_rot = ("rotation" in reason_clean.lower()) or ("capital rotation" in reason_clean.lower())

        # Format duration
        if isinstance(duration, (int, float)):
            total_secs = max(0, int(duration))
            hours = total_secs // 3600
            mins = (total_secs % 3600) // 60
            secs = total_secs % 60
            duration_str = f"{hours}h {mins}m {secs}s"
        else:
            duration_str = str(duration)

        now_utc = datetime.now(timezone.utc)
        ts_str = timestamp or now_utc.strftime("%Y-%m-%d %H:%M:%S")
        iso_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        trade_record = {
            "timestamp": iso_str,
            "coin": coin_clean,
            "side": side_clean,
            "size": size_clean,
            "entry": entry_clean,
            "exit": exit_clean,
            "duration": duration_str,
            "gross_pnl": gross_clean,
            "fees": fees_clean,
            "net_pnl": net_clean,
            "pnl": net_clean,
            "roi": roi_clean,
            "strategy": strategy_clean,
            "reason": reason_clean,
            "is_rotation": is_rot,
        }

        with self._lock:
            # Set post-close anti-churn cooldown for this coin (preserve if already set by caller)
            cooldown_ts = exit_time if exit_time is not None else time.time()
            if coin_clean not in self.cooldown_tracker:
                self.cooldown_tracker[coin_clean] = cooldown_ts

            self.cumulative_fees += fees_clean

            self._closed_trades_cache.append(trade_record)
            self._append_to_daily_pnl(ts_str, trade_record)
            self._append_to_session_trades(trade_record)

        return trade_record

    def _append_to_daily_pnl(self, ts_str: str, record: Dict[str, Any]) -> None:
        """Append formatted row to daily_pnl.md table with Gross PnL, Fees, and Net PnL."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.daily_pnl_file)), exist_ok=True)
            file_exists = os.path.exists(self.daily_pnl_file) and os.path.getsize(self.daily_pnl_file) > 0

            gross_pnl = record.get("gross_pnl", record.get("pnl", 0.0))
            gross_pnl_str = f"+${gross_pnl:,.2f}" if gross_pnl >= 0 else f"-${abs(gross_pnl):,.2f}"
            fees_val = record.get("fees", 0.0)
            fees_str = f"${fees_val:,.2f}"
            net_pnl = record.get("net_pnl", record.get("pnl", 0.0))
            net_pnl_str = f"+${net_pnl:,.2f}" if net_pnl >= 0 else f"-${abs(net_pnl):,.2f}"
            roi_str = f"+{record['roi']:.2f}%" if record["roi"] >= 0 else f"{record['roi']:.2f}%"

            row = (
                f"| {ts_str} | {record['coin']} | {record['side']} | {record['size']} | "
                f"${record['entry']:,.4f} | ${record['exit']:,.4f} | {record['duration']} | "
                f"{gross_pnl_str} | {fees_str} | {net_pnl_str} | {roi_str} | {record['strategy']} | {record['reason']} |\n"
            )

            with open(self.daily_pnl_file, "a", encoding="utf-8") as f:
                if not file_exists:
                    f.write("# Daily PnL & Trade Journal\n\n")
                    f.write(
                        "| Date/Time (UTC) | Coin | Side | Size | Entry Px | Exit Px | Duration | "
                        "Gross PnL ($) | Fees ($) | Net PnL ($) | ROI (%) | Strategy | Reason |\n"
                    )
                    f.write(
                        "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
                    )
                f.write(row)
        except Exception:
            pass

    def _append_to_session_trades(self, record: Dict[str, Any]) -> None:
        """Append trade object to session_trades.json atomically."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.session_trades_file)), exist_ok=True)
            existing_trades = []
            if os.path.exists(self.session_trades_file):
                try:
                    with open(self.session_trades_file, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            existing_trades = json.loads(content)
                            if not isinstance(existing_trades, list):
                                existing_trades = []
                except Exception:
                    existing_trades = []

            existing_trades.append(record)

            tmp_path = f"{self.session_trades_file}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(existing_trades, f, indent=2)
            os.replace(tmp_path, self.session_trades_file)
        except Exception:
            pass

    def get_position(self, coin: str) -> Optional[PositionTracker]:
        """Retrieve active position tracker by coin symbol."""
        key = self._get_key(coin)
        with self._lock:
            return self.active_positions.get(key)

    def remove_position(self, coin: str) -> Optional[PositionTracker]:
        """Remove active position from tracking without journaling."""
        key = self._get_key(coin)
        with self._lock:
            return self.active_positions.pop(key, None)

    def get_anti_churn_status(self, current_time: Optional[float] = None) -> Dict[str, Any]:
        """Return active cooldown coins and remaining hold/cooldown seconds."""
        now = time.time() if current_time is None else current_time
        status: Dict[str, Any] = {}

        # 1. Active positions in minimum hold
        for key, tracker in self.active_positions.items():
            elapsed = now - tracker.entry_time
            if elapsed < self.min_holding_seconds:
                rem = round(self.min_holding_seconds - elapsed, 1)
                status[tracker.coin] = {
                    "coin": tracker.coin,
                    "remaining_seconds": rem,
                    "min_hold_seconds": int(self.min_holding_seconds),
                    "elapsed_seconds": round(elapsed, 1),
                    "type": "MIN_HOLD",
                    "label": f"⏳ {int(rem)}s min hold",
                }

        # 2. Closed coins in post-trade cooldown
        for coin, closed_at in list(self.cooldown_tracker.items()):
            elapsed = now - closed_at
            if elapsed < self.reentry_cooldown_seconds:
                rem = round(self.reentry_cooldown_seconds - elapsed, 1)
                if coin not in status:
                    status[coin] = {
                        "coin": coin,
                        "remaining_seconds": rem,
                        "cooldown_seconds": int(self.reentry_cooldown_seconds),
                        "elapsed_seconds": round(elapsed, 1),
                        "type": "COOLDOWN",
                        "label": f"⏳ {int(rem)}s cooldown",
                    }
            else:
                self.cooldown_tracker.pop(coin, None)

        return status

    def get_summary(self) -> Dict[str, Any]:
        """Get summary snapshot of all actively managed positions, fees, and cooldowns."""
        with self._lock:
            positions_summary = []
            anti_churn = self.get_anti_churn_status()
            for key, tracker in self.active_positions.items():
                dur = round(time.time() - tracker.entry_time, 1)
                pos_info = {
                    "coin": tracker.coin,
                    "side": tracker.side,
                    "size": tracker.size,
                    "entry_price": tracker.entry_price,
                    "current_price": tracker.current_price,
                    "peak_price": tracker.peak_price,
                    "stop_price": tracker.stop_price,
                    "roi_pct": round(tracker.current_roi, 2),
                    "peak_roi_pct": round(tracker.peak_roi, 2),
                    "pnl": round(tracker.current_pnl, 2),
                    "breakeven_triggered": tracker.breakeven_triggered,
                    "trailing_stop_triggered": tracker.trailing_stop_triggered,
                    "trailing_distance_pct": tracker.dynamic_trailing_distance_pct or self.trailing_distance_pct,
                    "atr_pct": round(tracker.atr_pct, 2),
                    "take_profit_roi_pct": getattr(tracker, "take_profit_roi_pct", self.take_profit_roi_pct),
                    "duration_seconds": dur,
                    "entry_time": tracker.entry_time,
                }
                if tracker.coin in anti_churn:
                    pos_info["anti_churn"] = anti_churn[tracker.coin]
                    pos_info["min_hold_remaining"] = anti_churn[tracker.coin]["remaining_seconds"]
                    pos_info["anti_churn_label"] = anti_churn[tracker.coin]["label"]
                positions_summary.append(pos_info)

            return {
                "active_count": len(self.active_positions),
                "positions": positions_summary,
                "closed_trades_count": len(self._closed_trades_cache),
                "missed_opportunities_count": len(self._missed_opportunities_cache),
                "cumulative_fees": round(self.cumulative_fees, 2),
                "total_fees_paid": round(self.cumulative_fees, 2),
                "anti_churn_status": anti_churn,
            }

    def _load_persisted_data(self) -> None:
        """Safely pre-populate in-memory caches from disk if existing files are present."""
        if os.path.exists(self.session_trades_file):
            try:
                with open(self.session_trades_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
                        if isinstance(data, list):
                            for t in data:
                                if isinstance(t, dict):
                                    entry_val = float(t.get("entry") or t.get("entry_price") or 0.0)
                                    exit_val = float(t.get("exit") or t.get("exit_price") or 0.0)
                                    size_val = float(t.get("size", 0.0))
                                    side_val = str(t.get("side", "LONG")).upper()

                                    if "gross_pnl" in t and t["gross_pnl"] is not None:
                                        g = float(t["gross_pnl"])
                                    elif "pnl" in t and t["pnl"] is not None:
                                        g = float(t["pnl"])
                                    else:
                                        g = (exit_val - entry_val) * size_val if side_val == "LONG" else (entry_val - exit_val) * size_val
                                    g = round(g, 2)

                                    if "fees" in t and t["fees"] is not None:
                                        f_amt = float(t["fees"])
                                    else:
                                        f_amt = round((entry_val * size_val + exit_val * size_val) * (self.taker_fee_pct / 100.0), 2)

                                    if "net_pnl" in t and t["net_pnl"] is not None:
                                        n = float(t["net_pnl"])
                                    else:
                                        n = round(g - f_amt, 2)

                                    t["gross_pnl"] = g
                                    t["fees"] = f_amt
                                    t["net_pnl"] = n
                                    t["is_rotation"] = ("rotation" in str(t.get("reason", "")).lower()) or bool(t.get("is_rotation"))

                                    self.cumulative_fees += f_amt
                            self._closed_trades_cache.extend(data)
            except Exception:
                pass
            except Exception:
                pass

        if os.path.exists(self.decision_journal_file):
            try:
                with open(self.decision_journal_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                item = json.loads(line)
                                if isinstance(item, dict):
                                    self._missed_opportunities_cache.append(item)
                            except Exception:
                                continue
            except Exception:
                pass

    def log_missed_opportunity(
        self,
        coin: str,
        strategy: str,
        reason: str,
        metrics: Optional[Any] = None,
        retrospective_note: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Log a missed trading opportunity or rejected trade decision.

        Appends to:
        1. reports/missed_opportunities.md (Markdown table with Date/Time UTC, Coin,
           Strategy, Reason, Metrics/Conditions, Retrospective Note)
        2. reports/decision_journal.jsonl (JSONL format with full metadata)

        Also records into in-memory cache.
        """
        coin_clean = PositionTracker._normalize_coin(coin)
        strategy_clean = str(strategy)
        reason_clean = str(reason)

        # Extract retrospective note if passed or embedded in metrics
        if retrospective_note is not None:
            note_clean = str(retrospective_note)
        elif isinstance(metrics, dict) and ("retrospective_note" in metrics or "note" in metrics):
            note_clean = str(metrics.get("retrospective_note") or metrics.get("note") or "-")
        else:
            note_clean = "-"

        # Format metrics
        if metrics is None:
            metrics_dict: Dict[str, Any] = {}
            metrics_str = "-"
        elif isinstance(metrics, dict):
            metrics_dict = {k: v for k, v in metrics.items() if k not in ("retrospective_note", "note")}
            metrics_str = ", ".join(f"{k}={v}" for k, v in metrics_dict.items()) if metrics_dict else "-"
        else:
            metrics_dict = {"raw": metrics}
            metrics_str = str(metrics)

        now_utc = datetime.now(timezone.utc)
        ts_str = timestamp or now_utc.strftime("%Y-%m-%d %H:%M:%S")
        iso_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        record = {
            "timestamp": iso_str,
            "datetime_utc": ts_str,
            "type": "missed_opportunity",
            "coin": coin_clean,
            "strategy": strategy_clean,
            "reason": reason_clean,
            "metrics": metrics_dict,
            "retrospective_note": note_clean,
        }

        with self._lock:
            self._missed_opportunities_cache.append(record)
            self._append_to_missed_opportunities_md(ts_str, record, metrics_str)
            self._append_to_decision_journal(record)

        return record

    def _append_to_missed_opportunities_md(
        self,
        ts_str: str,
        record: Dict[str, Any],
        metrics_str: str,
    ) -> None:
        """Append formatted row to missed_opportunities.md table."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.missed_opportunities_file)), exist_ok=True)
            file_exists = (
                os.path.exists(self.missed_opportunities_file)
                and os.path.getsize(self.missed_opportunities_file) > 0
            )

            # Escape pipes and newlines for markdown table compatibility
            clean_coin = str(record["coin"]).replace("|", "\\|").replace("\n", " ")
            clean_strat = str(record["strategy"]).replace("|", "\\|").replace("\n", " ")
            clean_reason = str(record["reason"]).replace("|", "\\|").replace("\n", " ")
            clean_metrics = str(metrics_str).replace("|", "\\|").replace("\n", " ")
            clean_note = str(record["retrospective_note"]).replace("|", "\\|").replace("\n", " ")

            row = (
                f"| {ts_str} | {clean_coin} | {clean_strat} | {clean_reason} | "
                f"{clean_metrics} | {clean_note} |\n"
            )

            with open(self.missed_opportunities_file, "a", encoding="utf-8") as f:
                if not file_exists:
                    f.write("# Missed Opportunities & Decision Journal\n\n")
                    f.write(
                        "| Date/Time (UTC) | Coin | Strategy | Reason | Metrics/Conditions | Retrospective Note |\n"
                    )
                    f.write(
                        "|---|---|---|---|---|---|\n"
                    )
                f.write(row)
        except Exception:
            pass

    def _append_to_decision_journal(self, record: Dict[str, Any]) -> None:
        """Append JSONL line to decision_journal.jsonl."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.decision_journal_file)), exist_ok=True)
            line = json.dumps(record) + "\n"
            with open(self.decision_journal_file, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def record_missed_opportunity(
        self,
        coin: str,
        strategy: str,
        side: str = "",
        signal_price: float = 0.0,
        filter_reason: str = "",
        category: str = "[RISK_FILTER]",
        retrospective_outcome: str = "",
        pnl_saved_usd: float = 0.0,
        status: str = "FILTERED",
        timestamp: Optional[str] = None,
        reason: Optional[str] = None,
        metrics: Optional[Any] = None,
        retrospective_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a skipped or filtered trade setup (backwards-compatible wrapper around log_missed_opportunity)."""
        actual_reason = reason or filter_reason or category
        actual_note = retrospective_note or retrospective_outcome
        actual_metrics = metrics or {
            "side": side,
            "signal_price": signal_price,
            "category": category,
            "status": status,
            "pnl_saved_usd": pnl_saved_usd,
        }
        return self.log_missed_opportunity(
            coin=coin,
            strategy=strategy,
            reason=actual_reason,
            metrics=actual_metrics,
            retrospective_note=actual_note,
            timestamp=timestamp,
        )

    def get_missed_opportunities(
        self,
        limit: int = 50,
        reverse: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve missed opportunities from in-memory cache and reports/decision_journal.jsonl.

        Parameters:
            limit: Maximum number of records to return (default: 50).
            reverse: If True, returns most recent first. If False, returns chronological order.

        Returns:
            List of missed opportunity records.
        """
        if limit is not None and limit <= 0:
            return []

        with self._lock:
            # 1. Read from disk if file exists
            file_records: List[Dict[str, Any]] = []
            if os.path.exists(self.decision_journal_file):
                try:
                    with open(self.decision_journal_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    item = json.loads(line)
                                    if isinstance(item, dict):
                                        file_records.append(item)
                                except Exception:
                                    continue
                except Exception:
                    file_records = []

            # 2. Combine with in-memory cache, preserving order and deduplicating
            seen_keys = set()
            combined: List[Dict[str, Any]] = []

            for rec in file_records + self._missed_opportunities_cache:
                key = (
                    rec.get("timestamp") or rec.get("datetime_utc"),
                    rec.get("coin"),
                    rec.get("strategy"),
                    rec.get("reason") or rec.get("filter_reason"),
                )
                if key not in seen_keys:
                    seen_keys.add(key)
                    combined.append(rec)

            if reverse:
                result = list(reversed(combined))
                return result[:limit] if limit and limit > 0 else result
            else:
                return combined[-limit:] if limit and limit > 0 and len(combined) > limit else combined

    def get_closed_trades(
        self,
        limit: int = 100,
        reverse: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve closed trades from in-memory cache and reports/session_trades.json.

        Parameters:
            limit: Maximum number of records to return (default: 100).
            reverse: If True, returns most recent first. If False, returns chronological order.

        Returns:
            List of closed trade records.
        """
        if limit is not None and limit <= 0:
            return []

        with self._lock:
            # 1. Read from session_trades.json if exists
            file_records: List[Dict[str, Any]] = []
            if os.path.exists(self.session_trades_file):
                try:
                    with open(self.session_trades_file, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            loaded = json.loads(content)
                            if isinstance(loaded, list):
                                file_records = loaded
                except Exception:
                    file_records = []

            # 2. Combine with in-memory cache, preserving order and deduplicating
            seen_keys = set()
            combined: List[Dict[str, Any]] = []

            for rec in file_records + self._closed_trades_cache:
                key = (
                    rec.get("timestamp"),
                    rec.get("coin"),
                    rec.get("side"),
                    rec.get("entry"),
                    rec.get("exit"),
                    rec.get("pnl"),
                    rec.get("reason"),
                )
                if key not in seen_keys:
                    seen_keys.add(key)
                    combined.append(rec)

            if reverse:
                result = list(reversed(combined))
                return result[:limit] if limit and limit > 0 else result
            else:
                return combined[-limit:] if limit and limit > 0 and len(combined) > limit else combined
