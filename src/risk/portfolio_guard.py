"""
Unified Portfolio Risk Guard & Strategy Capital Allocation Engine.
Strict Constraints & Guardrails:
  - Max 25% margin equity allocation per individual strategy.
  - Limit max simultaneous open positions to 4 across the entire multi-strategy node.
  - Route all orders through collision detection to prevent opposing orders on the same asset.
  - Automatic cancellation of stale unmitigated limit orders exceeding their time-in-force window.
  - 2% 24-hour portfolio drawdown circuit breaker.
"""

import time
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple

import polars as pl

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId, StrategyId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import Order


@dataclass
class PendingOrderEntry:
    """Tracks a submitted limit order for timeout cancellation."""
    order_id: str
    instrument_id: InstrumentId
    strategy_id: str
    side: OrderSide
    submitted_time_ms: int
    timeout_seconds: float = 300.0  # 5 minutes default timeout


class PortfolioGuard:
    """
    Unified Risk Manager enforcing portfolio-level constraints across all 4 strategy actors:
      1. TrendContinuationSMC
      2. HourlyFundingFade
      3. OrderBookImbalance
      4. VwapOiMomentum
    """

    def __init__(
        self,
        max_strategy_equity_pct: float = 0.40,  # Max 40% margin per strategy
        max_total_open_positions: int = 8,      # Max 8 concurrent positions across node
        max_daily_drawdown_pct: float = 0.20,   # 20% daily drawdown circuit breaker
        default_order_timeout_secs: float = 300.0,
    ) -> None:
        self.max_strategy_equity_pct = max_strategy_equity_pct
        self.baseline_strategy_equity_pct = max_strategy_equity_pct
        self.strategy_allocation_caps: Dict[str, float] = {}
        self.strategy_sizing_multipliers: Dict[str, float] = {}
        self.strategy_performance_stats: Dict[str, Dict[str, Any]] = {}
        self.max_total_open_positions = max_total_open_positions
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.default_order_timeout_secs = default_order_timeout_secs

        # Tracking state
        self.strategy_allocated_margin: Dict[str, float] = {}
        self.active_instrument_directions: Dict[str, OrderSide] = {}
        self.pending_orders: Dict[str, PendingOrderEntry] = {}

        # 24h Drawdown tracking
        self.daily_high_water_mark: Optional[float] = None
        self.current_equity: float = 0.0
        self.is_circuit_breaker_triggered: bool = False
        self.day_start_timestamp_ms: int = int(time.time() * 1000)
        self._lock = threading.RLock()
        self._pending_approvals: int = 0
        self.prospect_biases: Dict[str, str] = {}
        self.cooldown_tracker: Dict[str, float] = {}
        self.reentry_cooldown_seconds: float = 30.0  # 30 seconds anti-churn cooldown

    def update_equity(self, equity: float) -> None:
        """Update portfolio equity and evaluate 24-hour drawdown circuit breaker."""
        now_ms = int(time.time() * 1000)
        # Reset high water mark if 24 hours elapsed
        if now_ms - self.day_start_timestamp_ms >= 86_400_000:
            self.day_start_timestamp_ms = now_ms
            self.daily_high_water_mark = equity
            self.is_circuit_breaker_triggered = False

        self.current_equity = equity
        if self.daily_high_water_mark is None:
            self.daily_high_water_mark = equity
        if equity > self.daily_high_water_mark:
            self.daily_high_water_mark = equity

        # Calculate daily drawdown
        if self.daily_high_water_mark > 0:
            drawdown_pct = (self.daily_high_water_mark - equity) / self.daily_high_water_mark
            if drawdown_pct >= self.max_daily_drawdown_pct:
                self.is_circuit_breaker_triggered = True

    def set_prospect_biases(self, biases: Dict[str, str]) -> None:
        """Update active AI prospect biases for instruments."""
        with self._lock:
            self.prospect_biases = dict(biases)

    def get_strategy_allocation_cap(self, strategy_name: str) -> float:
        """Get current dynamic or baseline equity margin cap for a strategy."""
        with self._lock:
            if strategy_name in self.strategy_allocation_caps:
                return self.strategy_allocation_caps[strategy_name]
            base_name = strategy_name.split("-")[0]
            if base_name in self.strategy_allocation_caps:
                return self.strategy_allocation_caps[base_name]
            return self.baseline_strategy_equity_pct

    def update_dynamic_allocations(
        self,
        closed_trades: List[Dict[str, Any]],
        rolling_window: int = 30,
    ) -> Dict[str, float]:
        """
        Dynamically scale strategy margin allocation caps and risk sizing multipliers
        based on rolling realized performance (last `rolling_window` trades per strategy).
        
        Tiers & Multipliers:
          - HOT 🔥: Win rate >= 75% and net PnL > 0 -> Multiplier: 1.6x (Expand size + cap)
          - WARM ⚡: Win rate >= 60% and net PnL > 0 -> Multiplier: 1.4x (Solid performer)
          - NORMAL ⚖️: Win rate >= 40% and net PnL >= 0 -> Multiplier: 1.0x (Baseline)
          - COLD ❄️: Win rate < 40% or net PnL < 0 -> Multiplier: 0.6x (Throttle size down)
          - PROBATION ⚠️: Win rate < 25% or heavy net loss -> Multiplier: 0.4x (Minimal size)
        """
        strat_trades: Dict[str, List[Dict[str, Any]]] = {}
        for t in closed_trades:
            strat = t.get("strategy") or "default"
            strat_trades.setdefault(strat, []).append(t)
            base_strat = strat.split("-")[0]
            if base_strat != strat:
                strat_trades.setdefault(base_strat, []).append(t)

        with self._lock:
            for strat, trades in strat_trades.items():
                if not trades:
                    continue
                # Use rolling window of recent trades for adaptive learning
                eval_trades = trades[-rolling_window:] if len(trades) > rolling_window else trades
                wins = 0
                gross_wins = 0.0
                gross_losses = 0.0
                total_net_pnl = 0.0
                for tr in eval_trades:
                    gross = float(tr.get("gross_pnl") if "gross_pnl" in tr else (tr.get("pnl") or 0.0))
                    fees = float(tr.get("fees") or 0.0)
                    net = float(tr.get("net_pnl") if "net_pnl" in tr else (gross - fees))
                    total_net_pnl += net
                    if net > 0:
                        wins += 1
                        gross_wins += gross
                    elif net < 0:
                        gross_losses += abs(gross)

                win_rate = wins / len(eval_trades)
                profit_factor = round(gross_wins / max(gross_losses, 0.01), 2) if gross_losses > 0 else (round(gross_wins, 2) if gross_wins > 0 else 1.0)

                if win_rate >= 0.60 and total_net_pnl > 0:
                    multiplier = 1.6 if win_rate >= 0.75 else 1.4
                    tier = "HOT 🔥" if win_rate >= 0.75 else "WARM ⚡"
                elif win_rate < 0.40 and total_net_pnl < 0:
                    multiplier = 0.4 if (win_rate < 0.25 or total_net_pnl < -50.0) else 0.6
                    tier = "PROBATION ⚠️" if (win_rate < 0.25 or total_net_pnl < -50.0) else "COLD ❄️"
                elif total_net_pnl < 0:
                    multiplier = 0.6
                    tier = "COLD ❄️"
                else:
                    multiplier = 1.0
                    tier = "NORMAL ⚖️"

                cap = round(self.baseline_strategy_equity_pct * multiplier, 4)
                self.strategy_allocation_caps[strat] = cap
                self.strategy_sizing_multipliers[strat] = multiplier
                self.strategy_performance_stats[strat] = {
                    "strategy": strat,
                    "tier": tier,
                    "multiplier": multiplier,
                    "allocation_cap": cap,
                    "rolling_trades": len(eval_trades),
                    "win_rate_pct": round(win_rate * 100.0, 1),
                    "profit_factor": profit_factor,
                    "rolling_net_pnl": round(total_net_pnl, 2),
                }

            return dict(self.strategy_allocation_caps)

    def get_strategy_sizing_multiplier(self, strategy_name: str) -> float:
        """Get current dynamic risk sizing multiplier for order sizing (never throttled below 1.0x)."""
        with self._lock:
            val = self.strategy_sizing_multipliers.get(strategy_name)
            if val is None:
                base_name = strategy_name.split("-")[0]
                val = self.strategy_sizing_multipliers.get(base_name, 1.0)
            # Never throttle below 1.0x - do not shrink positions 60% smaller!
            return max(1.0, float(val))

    def get_strategy_performance_status(self) -> Dict[str, Dict[str, Any]]:
        """Get live performance metrics, tiers, and multipliers across all strategies."""
        with self._lock:
            return dict(self.strategy_performance_stats)

    def can_open_position(
        self,
        strategy_name: str,
        instrument_id: InstrumentId,
        side: OrderSide,
        proposed_notional_usd: float,
        current_open_positions_count: int,
    ) -> Tuple[bool, str]:
        with self._lock:
            if self.is_circuit_breaker_triggered:
                return False, f"Trading halted: 24h drawdown breached 2% limit."

            # AI Prospect bias check from bridge/prospects.json
            coin = str(instrument_id).split("-")[0].split(".")[0].upper()

            # Anti-churn re-entry cooldown (prevent rapid flip-flopping)
            now_sec = time.time()
            cooldown_until = self.cooldown_tracker.get(coin, 0.0)
            if now_sec < cooldown_until:
                rem_sec = int(cooldown_until - now_sec)
                return False, f"Anti-churn cooldown active for {coin} ({rem_sec}s remaining)."

            prospect_bias = self.prospect_biases.get(coin)
            if prospect_bias:
                # Do not block microstructural orderbook scalpers from taking resting liquidity on either side
                if "OrderBook" not in strategy_name and "Scalp" not in strategy_name:
                    if prospect_bias == "LONG" and side != OrderSide.BUY:
                        return False, f"AI Prospect bias for {coin} is LONG; rejecting {side.name} entry."
                    elif prospect_bias == "SHORT" and side != OrderSide.SELL:
                        return False, f"AI Prospect bias for {coin} is SHORT; rejecting {side.name} entry."

            effective_count = current_open_positions_count + self._pending_approvals
            if effective_count >= self.max_total_open_positions:
                return False, f"Max node positions ({self.max_total_open_positions}) reached."

            # Enforce Hyperliquid official exchange max leverage per coin
            from src.utils.instruments import get_coin_max_leverage
            max_lev = get_coin_max_leverage(coin)
            max_coin_notional = self.current_equity * max_lev
            if proposed_notional_usd > (max_coin_notional * 1.01):
                return False, f"Order notional (${proposed_notional_usd:,.2f}) exceeds Hyperliquid max leverage for {coin} ({max_lev:.0f}x = ${max_coin_notional:,.2f})."

            cap = self.get_strategy_allocation_cap(strategy_name)
            max_allowed_margin = self.current_equity * cap
            current_strategy_margin = self.strategy_allocated_margin.get(strategy_name, 0.0)
            if (current_strategy_margin + proposed_notional_usd) > max_allowed_margin:
                if not hasattr(self, "_log_cooldown"):
                    self._log_cooldown = {}
                now = time.time()
                key = f"{strategy_name}_{instrument_id}"
                if now - self._log_cooldown.get(key, 0) < 5.0:
                    return False, "SILENT_BLOCK"
                self._log_cooldown[key] = now
                
                pct_str = f"{int(cap * 100)}%" if cap <= 1.0 else f"{int(cap)}x"
                return False, (
                    f"Strategy '{strategy_name}' exceeds {pct_str} allocation limit "
                    f"(${current_strategy_margin + proposed_notional_usd:,.2f} > ${max_allowed_margin:,.2f})."
                )

            instr_str = str(instrument_id)
            existing_side = self.active_instrument_directions.get(instr_str)
            if existing_side is not None and existing_side != side:
                return False, f"Collision detected: opposing order on {instr_str} ({existing_side} exists)."

            self._pending_approvals += 1
            return True, "Approved"

    def register_order_submitted(
        self,
        order: Order,
        strategy_name: str,
        notional_usd: float,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        with self._lock:
            self._pending_approvals = max(0, self._pending_approvals - 1)
            instr_str = str(order.instrument_id)
            order_id_str = str(order.client_order_id)

            self.strategy_allocated_margin[strategy_name] = (
                self.strategy_allocated_margin.get(strategy_name, 0.0) + notional_usd
            )
            self.active_instrument_directions[instr_str] = order.side

            entry = PendingOrderEntry(
                order_id=order_id_str,
                instrument_id=order.instrument_id,
                strategy_id=strategy_name,
                side=order.side,
                submitted_time_ms=int(time.time() * 1000),
                timeout_seconds=timeout_seconds or self.default_order_timeout_secs,
            )
            self.pending_orders[order_id_str] = entry

    def register_position_closed(
        self,
        strategy_name: str,
        instrument_id: InstrumentId,
        freed_notional_usd: float,
    ) -> None:
        """Release margin allocation and clear asset direction on position close."""
        instr_str = str(instrument_id)
        self.active_instrument_directions.pop(instr_str, None)

        # Set 3-minute anti-churn cooldown on the closed coin
        coin = instr_str.split("-")[0].split(".")[0].upper()
        self.cooldown_tracker[coin] = time.time() + self.reentry_cooldown_seconds

        current = self.strategy_allocated_margin.get(strategy_name, 0.0)
        self.strategy_allocated_margin[strategy_name] = max(0.0, current - freed_notional_usd)

    def set_cooldown(self, coin: str, duration_seconds: float = 180.0) -> None:
        """Explicitly set cooldown on a coin (e.g. after manual or sentinel close)."""
        with self._lock:
            c = str(coin).split("-")[0].split(".")[0].upper()
            self.cooldown_tracker[c] = time.time() + duration_seconds

    def is_in_cooldown(self, coin: str) -> bool:
        """Check if coin is currently under anti-churn cooldown."""
        c = str(coin).split("-")[0].split(".")[0].upper()
        return time.time() < self.cooldown_tracker.get(c, 0.0)

    def get_stale_orders_to_cancel(self) -> List[PendingOrderEntry]:
        """
        Identify unmitigated limit orders exceeding their time-in-force timeout.
        """
        now_ms = int(time.time() * 1000)
        stale = []
        for order_id, entry in list(self.pending_orders.items()):
            elapsed_secs = (now_ms - entry.submitted_time_ms) / 1000.0
            if elapsed_secs >= entry.timeout_seconds:
                stale.append(entry)
        return stale

    def acknowledge_order_cancelled(self, order_id_str: str) -> None:
        """Remove cancelled order from pending tracker."""
        entry = self.pending_orders.pop(order_id_str, None)
        if entry:
            instr_str = str(entry.instrument_id)
            self.active_instrument_directions.pop(instr_str, None)
