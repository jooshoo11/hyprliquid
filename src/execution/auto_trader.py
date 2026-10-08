"""
Autonomous Multi-Strategy Execution Engine & Risk Sentinel (auto_trader.py)
High-performance paper trading daemon that autonomously executes and manages trades:
1. Evaluates live high-conviction prospects (from bridge/prospects.json) and
   extreme negative funding short squeeze setups (from bridge/market_regime.json).
2. Applies Hyperliquid Coin-Specific Maximum Leverage:
   - Queries exact official maxLeverage per coin from Hyperliquid's universe (e.g. 40x BTC, 25x ETH, 20x SOL, 10x CRV/ONDO, 5x STRK, 3x MET).
   - Position Notional = Allocated Margin * Coin Max Leverage!
   - Dynamic bracket Stop-Loss ensuring risk is safely capped at ~20-25% of margin, preventing liquidation.
3. Applies PortfolioGuard risk rules:
   - Max concurrent open positions (default 3, preserving cash cushion).
   - Dynamic strategy allocation sizing via DynamicStrategyAllocator (0.4x - 1.8x).
   - Minimum conviction score gate (>= 85).
   - Cooldown gate (90s anti-churn hold after closing a symbol).
4. Active Watchdog Sentry:
   - Evaluates open positions every 5s against live mark prices.
   - Executes Take-Profit (TP) and Stop-Loss (SL) exits.
   - Breakeven trailing ratchet: locks in risk-free stop once ROI exceeds +15% on margin.
5. Closed Trades Feedback Loop:
   - Feeds realized closed trades immediately into DynamicStrategyAllocator
     so winning strategies get awarded higher margin allocations.
"""

import os
import sys
import time
import json
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.scanner.mcp_client import HyperliquidInfoClient
from src.risk.dynamic_allocator import DynamicStrategyAllocator

ACTIVE_TRADES_PATH = os.path.join(REPO_ROOT, "bridge", "active_trades.json")
SESSION_TRADES_PATH = os.path.join(REPO_ROOT, "reports", "session_trades.json")
PROSPECTS_PATH = os.path.join(REPO_ROOT, "bridge", "prospects.json")
MARKET_REGIME_PATH = os.path.join(REPO_ROOT, "bridge", "market_regime.json")
AUTOTRADE_STATE_PATH = os.path.join(REPO_ROOT, "bridge", "autotrade_state.json")


def load_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_atomic(path: str, data: Any) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


class AutoTrader:
    """
    Autonomous Execution Agent managing max-leverage auto-entries, dynamic exits, and risk limits.
    """

    def __init__(
        self,
        info_client: Optional[HyperliquidInfoClient] = None,
        dynamic_allocator: Optional[DynamicStrategyAllocator] = None,
        enabled: bool = True,
        max_open_positions: int = 3,
        base_margin_usd: float = 15.0,
        min_conviction_score: int = 85,
        cooldown_seconds: float = 90.0,
    ):
        self.info_client = info_client or HyperliquidInfoClient()
        self.allocator = dynamic_allocator or DynamicStrategyAllocator()
        self.lock = threading.RLock()

        # Load persisted toggle state if present
        saved_state = load_json(AUTOTRADE_STATE_PATH, {})
        self.enabled = saved_state.get("enabled", enabled)
        self.max_open_positions = saved_state.get("max_open_positions", max_open_positions)
        self.base_margin_usd = saved_state.get("base_margin_usd", base_margin_usd)
        self.min_conviction_score = saved_state.get("min_conviction_score", min_conviction_score)
        self.cooldown_seconds = cooldown_seconds

        self._leverage_map: Dict[str, float] = {}
        self._cooldowns: Dict[str, float] = {}
        self._ratcheted: Dict[str, bool] = {}
        self._thoughts_buffer: List[Dict[str, Any]] = []
        self._last_rotation_time: float = 0.0
        self.min_rotation_interval: float = 180.0  # Min 3 minutes between rotations to prevent fee churn
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.last_action: str = "Initialized auto-trader with Hyperliquid Max Leverage"

    def get_coin_max_leverage(self, coin: str) -> float:
        """Fetch the exact official Hyperliquid maximum leverage for a coin."""
        coin = coin.upper()
        if not self._leverage_map:
            try:
                meta, _ = self.info_client.get_meta_and_asset_ctxs()
                self._leverage_map = {
                    u["name"].upper(): float(u.get("maxLeverage", 10.0))
                    for u in meta.get("universe", [])
                }
            except Exception:
                self._leverage_map = {}
        return self._leverage_map.get(coin, 10.0)

    def get_mark_price(self, coin: str) -> float:
        """Fetch live mark price for a coin with case-insensitive symbol resolution."""
        coin_up = coin.upper()
        try:
            meta, ctxs = self.info_client.get_meta_and_asset_ctxs()
            for u, ctx in zip(meta.get("universe", []), ctxs):
                if u.get("name", "").upper() == coin_up:
                    return float(ctx.get("midPx") or ctx.get("oraclePx", 0.0))
        except Exception:
            pass
        return 0.0

    def calculate_position_margin(
        self,
        total_cash: float,
        current_positions: Any = 0,
        strat_mult: float = 1.0,
    ) -> float:
        """
        Deploy 100% of account cash evenly across position slots without exceeding total_cash.
        Guarantees: sum(margin for all positions) <= total_cash.
        Caps single position to max 38% of account to prevent over-concentration.
        """
        if isinstance(current_positions, list):
            already_locked = sum(float(p.get("margin", 0.0)) for p in current_positions)
        else:
            already_locked = 0.0
        available_cash = max(0.0, total_cash - already_locked - 0.50)  # 0.50 fee buffer

        # Base share per slot: e.g. ($100 - $0.50) / 3 slots = $33.17 per position
        base_slot_share = (total_cash - 0.50) / max(1, self.max_open_positions)
        target_margin = round(base_slot_share * strat_mult, 2)
        # Cap single position to max 38% of total account cash to protect against drawdowns
        max_single_position = round(total_cash * 0.38, 2)
        target_margin = min(target_margin, max_single_position)
        allocated_margin = round(min(available_cash, target_margin), 2)
        return allocated_margin

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            active_data = load_json(ACTIVE_TRADES_PATH, {"positions": [], "equity": 100.0})
            positions = active_data.get("positions", [])
            total_margin = sum(float(p.get("margin", 0.0)) for p in positions)
            total_notional = sum(float(p.get("size", 0.0)) * float(p.get("mark_price", 0.0)) for p in positions)
            equity = float(active_data.get("equity", 100.0))
            acct_lev = round(total_notional / equity, 2) if equity > 0 else 0.0

            return {
                "enabled": self.enabled,
                "status_label": "ACTIVE 🟢 (MAX LEV)" if self.enabled else "PAUSED ⏸️",
                "open_positions_count": len(positions),
                "max_open_positions": self.max_open_positions,
                "base_margin_usd": self.base_margin_usd,
                "total_margin_used": round(total_margin, 2),
                "total_notional": round(total_notional, 2),
                "account_leverage": acct_lev,
                "min_conviction_score": self.min_conviction_score,
                "leverage_mode": "HYPERLIQUID_COIN_MAX",
                "last_action": self.last_action,
                "timestamp": time.time(),
            }

    def set_enabled(self, enabled: bool) -> bool:
        with self.lock:
            self.enabled = enabled
            save_json_atomic(AUTOTRADE_STATE_PATH, {
                "enabled": self.enabled,
                "max_open_positions": self.max_open_positions,
                "base_margin_usd": self.base_margin_usd,
                "min_conviction_score": self.min_conviction_score,
                "updated_at": time.time(),
            })
            self.log_thought(
                category="[AUTO-PILOT]",
                status="OPTIMAL" if enabled else "WARNING",
                badge_color="emerald" if enabled else "slate",
                coin="SYS",
                msg=f"Autonomous Auto-Pilot {'ENGAGED 🟢 (Hyperliquid Max Leverage Active)' if enabled else 'PAUSED ⏸️ (Manual Mode)'}",
            )
            return self.enabled

    def log_thought(self, category: str, status: str, badge_color: str, coin: str, msg: str) -> None:
        time_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = {
            "timestamp": time_str,
            "category": category,
            "badge_color": badge_color,
            "status": status,
            "coin": coin,
            "thought": msg,
            "text": msg,
        }
        with self.lock:
            self._thoughts_buffer.append(entry)
            if len(self._thoughts_buffer) > 50:
                self._thoughts_buffer.pop(0)
            self.last_action = msg

    def get_thoughts(self) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self._thoughts_buffer)

    def open_trade(
        self,
        coin: str,
        side: str,
        strategy: str,
        margin_usd: Optional[float] = None,
        leverage: Optional[float] = None,
        notional_usd: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        reason: str = "",
        score: int = 85,
    ) -> Optional[Dict[str, Any]]:
        """
        Atomically execute paper trade using Hyperliquid's official maximum leverage per coin.
        """
        coin = coin.upper()
        side = side.upper()

        with self.lock:
            active_data = load_json(ACTIVE_TRADES_PATH, {"equity": 100.0, "cash_balance": 100.0, "positions": []})
            positions = active_data.get("positions", [])

            # Check if coin is already open
            for p in positions:
                if p.get("coin", "").upper() == coin:
                    return None

            # Fetch live mark price & universe metadata
            mark_px = 0.0
            coin_max_lev = self.get_coin_max_leverage(coin)
            try:
                meta, ctxs = self.info_client.get_meta_and_asset_ctxs()
                for u, ctx in zip(meta.get("universe", []), ctxs):
                    if u.get("name", "").upper() == coin:
                        mark_px = float(ctx.get("midPx") or ctx.get("oraclePx", 0.0))
                        coin_max_lev = float(u.get("maxLeverage", coin_max_lev))
                        self._leverage_map[coin] = coin_max_lev
                        break
            except Exception:
                pass

            if mark_px <= 0:
                return None

            # User specified leverage or official Hyperliquid Coin Max Leverage
            active_leverage = float(leverage) if leverage and leverage > 0 else coin_max_lev
            mult = self.allocator.get_multiplier(strategy)

            # Determine margin and notional
            cash_balance = float(active_data.get("cash_balance", 100.0))
            already_locked = sum(float(p.get("margin", 0.0)) for p in positions)
            available_cash = max(0.0, cash_balance - already_locked - 0.50)

            if available_cash < 5.0:
                self.log_thought("[RISK]", "WARNING", "rose", coin, f"Insufficient free margin (${available_cash:.2f}) to open {coin}")
                return None

            if notional_usd is not None and notional_usd > 0:
                # If explicit notional provided, derive margin
                effective_notional = round(notional_usd * mult, 2)
                allocated_margin = round(min(available_cash, effective_notional / active_leverage), 2)
            elif margin_usd is not None and margin_usd > 0:
                allocated_margin = round(min(available_cash, margin_usd * mult), 2)
                effective_notional = round(allocated_margin * active_leverage, 2)
            else:
                # ZERO CASH BUFFER: Evenly divide account cash across max_open_positions
                allocated_margin = self.calculate_position_margin(cash_balance, positions, mult)
                effective_notional = round(allocated_margin * active_leverage, 2)

            if allocated_margin < 5.0:
                return None

            qty = round(effective_notional / mark_px, 4)
            if qty <= 0:
                return None

            # Dynamic Leverage-Aware Bracket Stops:
            # Risk capped at ~25% of margin, preventing liquidation on high leverage.
            # 2.5:1 reward-to-risk ratio.
            sl_dist_pct = min(0.025, max(0.006, 0.25 / active_leverage))
            tp_dist_pct = round(sl_dist_pct * 2.5, 4)

            if side == "LONG":
                sl = stop_loss if (stop_loss and stop_loss < mark_px) else round(mark_px * (1.0 - sl_dist_pct), 4)
                tp = take_profit if (take_profit and take_profit > mark_px) else round(mark_px * (1.0 + tp_dist_pct), 4)
            else:
                sl = stop_loss if (stop_loss and stop_loss > mark_px) else round(mark_px * (1.0 + sl_dist_pct), 4)
                tp = take_profit if (take_profit and take_profit < mark_px) else round(mark_px * (1.0 - tp_dist_pct), 4)

            now = time.time()
            new_pos = {
                "coin": coin,
                "side": side,
                "size": qty,
                "entry_price": mark_px,
                "mark_price": mark_px,
                "leverage": active_leverage,
                "margin": allocated_margin,
                "notional": effective_notional,
                "unrealized_pnl": 0.0,
                "roi_pct": 0.0,
                "strategy": strategy,
                "strategy_multiplier": mult,
                "stop_loss": sl,
                "take_profit": tp,
                "entry_time": now,
                "duration_seconds": 0.0,
                "entry_reason": reason,
                "conviction_score": score,
            }
            positions.append(new_pos)
            active_data["positions"] = positions
            active_data["timestamp"] = now
            save_json_atomic(ACTIVE_TRADES_PATH, active_data)

            self.log_thought(
                category="[AUTONOMOUS TRADE]",
                status="OPTIMAL",
                badge_color="emerald",
                coin=coin,
                msg=f"Opened {side} {coin} ({active_leverage:.0f}x Max Leverage | ${effective_notional:.2f} Notional from ${allocated_margin:.2f} Margin | {mult:.1f}x {strategy}). TP: ${tp:,.4f}, SL: ${sl:,.4f}. {reason}",
            )
            return new_pos

    def close_trade(
        self,
        coin: str,
        exit_px: Optional[float] = None,
        reason: str = "Autonomous Close",
    ) -> Optional[Dict[str, Any]]:
        """Atomically close active position, realize PnL on margin, deduct fees, and trigger allocator feedback."""
        coin = coin.upper()

        with self.lock:
            active_data = load_json(ACTIVE_TRADES_PATH, {"equity": 100.0, "cash_balance": 100.0, "positions": []})
            positions = active_data.get("positions", [])
            pos_to_close = None
            remaining = []

            for p in positions:
                if p.get("coin", "").upper() == coin:
                    pos_to_close = p
                else:
                    remaining.append(p)

            if not pos_to_close:
                return None

            if exit_px is None or exit_px <= 0:
                exit_px = float(pos_to_close.get("mark_price", pos_to_close.get("entry_price", 0.0)))
                try:
                    meta, ctxs = self.info_client.get_meta_and_asset_ctxs()
                    for u, ctx in zip(meta.get("universe", []), ctxs):
                        if u.get("name", "").upper() == coin:
                            exit_px = float(ctx.get("midPx") or ctx.get("oraclePx", exit_px))
                            break
                except Exception:
                    pass

            entry_px = float(pos_to_close.get("entry_price", exit_px))
            qty = float(pos_to_close.get("size", 0.0))
            side = pos_to_close.get("side", "LONG").upper()
            lev = float(pos_to_close.get("leverage", 10.0))
            margin_used = float(pos_to_close.get("margin", (entry_px * qty) / lev if lev > 0 else (entry_px * qty)))

            gross_pnl = (exit_px - entry_px) * qty * (1.0 if side == "LONG" else -1.0)
            fees = round((entry_px * qty * 0.00035) + (exit_px * qty * 0.00035), 4)
            net_pnl = round(gross_pnl - fees, 2)
            # Return on Margin ROI %
            roi_pct = round((gross_pnl / margin_used) * 100.0, 2) if margin_used > 0 else 0.0

            closed_record = {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "coin": coin,
                "side": side,
                "size": qty,
                "entry": entry_px,
                "exit": exit_px,
                "leverage": lev,
                "margin": margin_used,
                "notional": round(exit_px * qty, 2),
                "gross_pnl": round(gross_pnl, 2),
                "fees": fees,
                "net_pnl": net_pnl,
                "roi_pct": roi_pct,
                "strategy": pos_to_close.get("strategy", "AutoTrader"),
                "reason": reason,
                "is_rotation": ("rotation" in str(reason).lower()),
            }

            trades_list = load_json(SESSION_TRADES_PATH, [])
            if not isinstance(trades_list, list):
                trades_list = []
            trades_list.append(closed_record)
            save_json_atomic(SESSION_TRADES_PATH, trades_list)

            # Update active ledger
            active_data["positions"] = remaining
            active_data["cash_balance"] = round(float(active_data.get("cash_balance", 100.0)) + net_pnl, 2)
            active_data["equity"] = active_data["cash_balance"]
            save_json_atomic(ACTIVE_TRADES_PATH, active_data)

            # Cooldown to avoid churning back into the same market immediately
            self._cooldowns[coin] = time.time()
            self._ratcheted.pop(coin, None)

            # Trigger allocator update immediately so winning strategies get boosted
            self.allocator.evaluate_allocations(trades_list)

            status_str = "OPTIMAL" if net_pnl >= 0 else "WARNING"
            col_str = "emerald" if net_pnl >= 0 else "rose"
            self.log_thought(
                category="[POSITION CLOSED]",
                status=status_str,
                badge_color=col_str,
                coin=coin,
                msg=f"Closed {side} {coin} ({lev:.0f}x Lev | {'+' if net_pnl>=0 else ''}${net_pnl:.2f} Net PnL, {roi_pct:+.2f}% ROI on Margin). Reason: {reason}",
            )
            return closed_record

    def watchdog_cycle(self) -> None:
        """Audit open positions for Take-Profit, Stop-Loss, and Breakeven Trailing Ratchet."""
        active_data = load_json(ACTIVE_TRADES_PATH, {"positions": []})
        positions = active_data.get("positions", [])
        if not positions:
            return

        try:
            meta, ctxs = self.info_client.get_meta_and_asset_ctxs()
            px_map = {}
            for u, ctx in zip(meta.get("universe", []), ctxs):
                px_map[u.get("name", "").upper()] = float(ctx.get("midPx") or ctx.get("oraclePx", 0.0))
        except Exception:
            return

        for p in list(positions):
            coin = p.get("coin", "").upper()
            mark_px = px_map.get(coin, 0.0)
            if mark_px <= 0:
                continue

            entry_px = float(p.get("entry_price", mark_px))
            qty = float(p.get("size", 0.0))
            side = p.get("side", "LONG").upper()
            lev = float(p.get("leverage", 10.0))
            sl = float(p.get("stop_loss", 0.0))
            tp = float(p.get("take_profit", 0.0))

            price_pct = ((mark_px - entry_px) / entry_px * 100.0) if side == "LONG" else ((entry_px - mark_px) / entry_px * 100.0)
            margin_roi_pct = price_pct * lev

            # 1. Trailing Breakeven Ratchet: if price moves favorably >= 0.8% (e.g. +16% ROI at 20x)
            if price_pct >= 0.8 and not self._ratcheted.get(coin):
                be_sl = round(entry_px * (1.001 if side == "LONG" else 0.999), 4)
                p["stop_loss"] = be_sl
                self._ratcheted[coin] = True
                save_json_atomic(ACTIVE_TRADES_PATH, active_data)
                self.log_thought(
                    category="[RATCHET STOP]",
                    status="OPTIMAL",
                    badge_color="cyan",
                    coin=coin,
                    msg=f"Ratcheted stop for {coin} ({lev:.0f}x) to Breakeven (${be_sl:,.4f}) to guarantee risk-free profit (+{margin_roi_pct:.1f}% ROI on margin).",
                )

            # 2. Take-Profit Check
            if tp > 0:
                if (side == "LONG" and mark_px >= tp) or (side == "SHORT" and mark_px <= tp):
                    self.close_trade(coin, exit_px=mark_px, reason=f"Take Profit Hit (${mark_px:,.4f} hit TP target ${tp:,.4f})")
                    continue

            # 3. Stop-Loss Check
            if sl > 0:
                if (side == "LONG" and mark_px <= sl) or (side == "SHORT" and mark_px >= sl):
                    self.close_trade(coin, exit_px=mark_px, reason=f"Stop Loss Hit (${mark_px:,.4f} hit SL stop ${sl:,.4f})")
                    continue

    def find_rotation_candidate(
        self,
        new_coin: str,
        new_leverage: float,
        new_score: int,
        current_positions: List[Dict[str, Any]],
        now: float,
    ) -> Optional[Tuple[str, str]]:
        """
        Identify if an active position should be rotated out for a superior play.
        Triggers when:
        1. All position slots are full (len(positions) >= max_open_positions).
        2. Candidate has been held >= 90s (anti-churn minimum hold).
        3. Candidate is not an active high runner (ROI < +3.0%).
        4. Either:
           - Significant Leverage Upgrade: new_leverage >= current_leverage * 1.5 AND new_score >= current_score - 5
           - Substantial Conviction Upgrade: new_score >= current_score + 8
        """
        if len(current_positions) < self.max_open_positions:
            return None

        new_coin_clean = new_coin.upper()
        if any(p.get("coin", "").upper() == new_coin_clean for p in current_positions):
            return None

        candidates = []
        for p in current_positions:
            coin = p.get("coin", "").upper()
            entry_t = float(p.get("entry_time", now))
            dur = now - entry_t
            if dur < 90.0:  # Respect anti-churn minimum hold
                continue

            roi = float(p.get("roi_pct", 0.0))
            if roi >= 3.0:  # Protect profitable runners
                continue

            cur_lev = float(p.get("leverage", 10.0))
            cur_score = int(p.get("conviction_score", 80))

            is_lev_upgrade = (new_leverage >= cur_lev * 1.5) and (new_score >= cur_score - 5)
            is_conv_upgrade = (new_score >= cur_score + 8)

            if is_lev_upgrade or is_conv_upgrade:
                # Priority: lowest existing leverage, lowest ROI, longest duration
                rank_score = (cur_lev - new_leverage) + (roi * 2.0)
                candidates.append((rank_score, coin, cur_lev, roi, is_lev_upgrade))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0])
        _, best_coin, old_lev, old_roi, was_lev_up = candidates[0]

        if was_lev_up:
            reason = f"Capital Rotation: Switched to higher-leverage {new_coin} ({new_leverage:.0f}x vs {old_lev:.0f}x Lev, {best_coin} ROI: {old_roi:+.1f}%)"
        else:
            reason = f"Capital Rotation: Reallocated stagnant {best_coin} ({old_roi:+.1f}%) to high-conviction {new_coin} (Score: {new_score})"

        return best_coin, reason

    def entry_cycle(self) -> None:
        """Scan top prospects & extreme short squeeze alerts to autonomously open trades using Hyperliquid max leverage."""
        if not self.enabled:
            return

        active_data = load_json(ACTIVE_TRADES_PATH, {"positions": [], "equity": 100.0, "cash_balance": 100.0})
        positions = active_data.get("positions", [])
        is_full = len(positions) >= self.max_open_positions

        open_coins = {p.get("coin", "").upper() for p in positions}
        now = time.time()

        # Priority 1: Check Extreme Short Squeeze Outliers (< -20% APR funding)
        regime_data = load_json(MARKET_REGIME_PATH, {})
        squeeze_alerts = regime_data.get("squeeze_alerts", []) if isinstance(regime_data, dict) else []
        for sq in squeeze_alerts:
            coin = sq.get("coin", "").upper()
            funding_apr = float(sq.get("funding_apr", 0.0))
            vol_24h = float(sq.get("vol_24h", 0.0))

            if coin and coin not in open_coins and funding_apr < -20.0 and vol_24h > 3000000:
                if (now - self._cooldowns.get(coin, 0)) > self.cooldown_seconds:
                    coin_max_lev = self.get_coin_max_leverage(coin)
                    if not is_full:
                        self.open_trade(
                            coin=coin,
                            side="LONG",
                            strategy="ShortSqueezeIgnition",
                            reason=f"Extreme Negative Funding ({funding_apr:.1f}% APR) on ${vol_24h/1e6:.1f}M 24h Vol",
                            score=95,
                        )
                        return  # Open at most 1 trade per cycle
                    else:
                        if (now - self._last_rotation_time) >= self.min_rotation_interval:
                            cand_px = self.get_mark_price(coin)
                            if cand_px > 0:
                                rot = self.find_rotation_candidate(coin, coin_max_lev, 95, positions, now)
                                if rot:
                                    old_coin, rot_reason = rot
                                    self.close_trade(old_coin, reason=rot_reason)
                                    self._last_rotation_time = now
                                    self.open_trade(
                                        coin=coin,
                                        side="LONG",
                                        strategy="ShortSqueezeIgnition",
                                        reason=f"[ROTATION] {rot_reason}",
                                        score=95,
                                    )
                                    return

        # Priority 2: Check High-Conviction AI Prospects (Conviction Score >= min_conviction_score)
        prospects_data = load_json(PROSPECTS_PATH, {})
        prospects_list = prospects_data.get("prospects", []) if isinstance(prospects_data, dict) else prospects_data
        if isinstance(prospects_list, list):
            for p in prospects_list:
                coin = p.get("coin", "").upper()
                score = int(p.get("conviction_score", p.get("score", 0)))
                bias = str(p.get("bias", "LONG")).upper()
                strategy = str(p.get("strategy") or "TrendContinuationSMC")
                rationale = p.get("rationale") or f"Conviction Score {score}"

                if coin and coin not in open_coins and score >= self.min_conviction_score:
                    if (now - self._cooldowns.get(coin, 0)) > self.cooldown_seconds:
                        coin_max_lev = self.get_coin_max_leverage(coin)
                        if not is_full:
                            self.open_trade(
                                coin=coin,
                                side=bias,
                                strategy=strategy,
                                reason=f"{rationale} (Conviction: {score})",
                                score=score,
                            )
                            return  # Open at most 1 trade per cycle
                        else:
                            if (now - self._last_rotation_time) >= self.min_rotation_interval:
                                cand_px = self.get_mark_price(coin)
                                if cand_px > 0:
                                    rot = self.find_rotation_candidate(coin, coin_max_lev, score, positions, now)
                                    if rot:
                                        old_coin, rot_reason = rot
                                        self.close_trade(old_coin, reason=rot_reason)
                                        self._last_rotation_time = now
                                        self.open_trade(
                                            coin=coin,
                                            side=bias,
                                            strategy=strategy,
                                            reason=f"[ROTATION] {rot_reason}",
                                            score=score,
                                        )
                                        return

    def loop(self) -> None:
        """Main autonomous execution background thread loop."""
        self._running = True
        cycle_counter = 0

        while self._running:
            try:
                # Watchdog runs every 5 seconds (fast exit execution)
                self.watchdog_cycle()

                # Entry scanner runs every ~15 seconds (every 3rd cycle)
                if cycle_counter % 3 == 0:
                    self.entry_cycle()

            except Exception as e:
                pass

            cycle_counter += 1
            time.sleep(5)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self.loop, daemon=True, name="AutoTrader")
        self._thread.start()

    def stop(self) -> None:
        self._running = False


# Shared singleton instance
auto_trader = AutoTrader()
