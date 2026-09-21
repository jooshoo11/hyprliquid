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
        max_strategy_equity_pct: float = 0.25,  # Max 25% margin per strategy
        max_total_open_positions: int = 4,      # Max 4 concurrent positions across node
        max_daily_drawdown_pct: float = 0.02,   # Hard stop at 2% 24h drawdown
        default_order_timeout_secs: float = 300.0,
    ) -> None:
        self.max_strategy_equity_pct = max_strategy_equity_pct
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
        self._lock = threading.Lock()
        self._pending_approvals: int = 0
        self.prospect_biases: Dict[str, str] = {}

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
            prospect_bias = self.prospect_biases.get(coin)
            if prospect_bias:
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

            max_allowed_margin = self.current_equity * self.max_strategy_equity_pct
            current_strategy_margin = self.strategy_allocated_margin.get(strategy_name, 0.0)
            if (current_strategy_margin + proposed_notional_usd) > max_allowed_margin:
                import time
                if not hasattr(self, "_log_cooldown"):
                    self._log_cooldown = {}
                now = time.time()
                key = f"{strategy_name}_{instrument_id}"
                if now - self._log_cooldown.get(key, 0) < 5.0:
                    return False, "SILENT_BLOCK"
                self._log_cooldown[key] = now
                
                return False, (
                    f"Strategy '{strategy_name}' exceeds 25% allocation limit "
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

        current = self.strategy_allocated_margin.get(strategy_name, 0.0)
        self.strategy_allocated_margin[strategy_name] = max(0.0, current - freed_notional_usd)

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
