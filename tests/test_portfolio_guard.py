"""
Unit tests for Unified PortfolioGuard Risk Engine.
Verifies:
  - Max 25% margin equity allocation per strategy.
  - Node-wide max 4 simultaneous positions limit.
  - Order anti-collision (preventing opposing orders on the same asset).
  - 2% 24h drawdown circuit breaker.
  - Stale unmitigated limit order timeout detection and cancellation.
"""

import time
from decimal import Decimal
import pytest

from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue, TraderId, StrategyId, ClientOrderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder

from src.risk.portfolio_guard import PortfolioGuard


def _create_dummy_order(symbol: str, side: OrderSide) -> LimitOrder:
    return LimitOrder(
        trader_id=TraderId("HL-TEST-001"),
        strategy_id=StrategyId("TEST-STRAT-001"),
        instrument_id=InstrumentId(Symbol(symbol), Venue("HYPERLIQUID")),
        client_order_id=ClientOrderId(f"ORD-{symbol}-{side}"),
        order_side=side,
        quantity=Quantity.from_str("1.0"),
        price=Price.from_str("100.0"),
        time_in_force=TimeInForce.GTC,
        init_id=UUID4(),
        ts_init=1_000_000_000,
    )


def test_portfolio_guard_strategy_margin_limit():
    guard = PortfolioGuard(max_strategy_equity_pct=0.25)
    guard.update_equity(10_000.0)  # Max allowed margin per strategy is $2,500.00
    inst_id = InstrumentId(Symbol("SOL-USD-PERP"), Venue("HYPERLIQUID"))

    # Within 25% limit ($2,000 <= $2,500)
    guard._pending_approvals = 0
    can_open, reason = guard.can_open_position(
        strategy_name="TrendContinuationSMC",
        instrument_id=inst_id,
        side=OrderSide.BUY,
        proposed_notional_usd=2000.0,
        current_open_positions_count=1,
    )
    assert can_open is True

    # Exceeding 25% limit ($3,000 > $2,500)
    guard._pending_approvals = 0
    can_open_exceed, reason_exceed = guard.can_open_position(
        strategy_name="TrendContinuationSMC",
        instrument_id=inst_id,
        side=OrderSide.BUY,
        proposed_notional_usd=3000.0,
        current_open_positions_count=1,
    )
    assert can_open_exceed is False
    assert "exceeds 25% allocation limit" in reason_exceed


def test_portfolio_guard_max_positions_limit():
    guard = PortfolioGuard(max_total_open_positions=4)
    guard.update_equity(10000.0)
    inst_id = InstrumentId(Symbol("BTC-USD-PERP"), Venue("HYPERLIQUID"))

    # When 3 positions are open, 4th is allowed
    guard._pending_approvals = 0
    can_open, _ = guard.can_open_position("HourlyFundingFade", inst_id, OrderSide.BUY, 500.0, 3)
    assert can_open is True

    # When 4 positions are open, 5th is blocked
    guard._pending_approvals = 0
    can_open_blocked, reason = guard.can_open_position("HourlyFundingFade", inst_id, OrderSide.BUY, 500.0, 4)
    assert can_open_blocked is False
    assert "Max node positions (4) reached" in reason


def test_portfolio_guard_collision_detection():
    guard = PortfolioGuard()
    guard.update_equity(10_000.0)
    inst_id = InstrumentId(Symbol("ETH-USD-PERP"), Venue("HYPERLIQUID"))
    order = _create_dummy_order("ETH-USD-PERP", OrderSide.BUY)

    # Register an active BUY order
    guard.register_order_submitted(order, "TrendContinuationSMC", 1000.0)

    # Opposing SELL order on the same asset must be rejected
    guard._pending_approvals = 0
    can_sell, reason = guard.can_open_position("HourlyFundingFade", inst_id, OrderSide.SELL, 500.0, 1)
    assert can_sell is False
    assert "Collision detected" in reason

    # Same direction BUY order is permitted
    guard._pending_approvals = 0
    can_buy, _ = guard.can_open_position("TrendContinuationSMC", inst_id, OrderSide.BUY, 500.0, 1)
    assert can_buy is True


def test_portfolio_guard_drawdown_circuit_breaker():
    guard = PortfolioGuard(max_daily_drawdown_pct=0.02)
    guard.update_equity(10_000.0)
    inst_id = InstrumentId(Symbol("BTC-USD-PERP"), Venue("HYPERLIQUID"))

    # 1.5% drawdown: within limits
    guard.update_equity(9_850.0)
    assert not guard.is_circuit_breaker_triggered
    guard._pending_approvals = 0
    can_open, _ = guard.can_open_position("TrendContinuationSMC", inst_id, OrderSide.BUY, 500.0, 0)
    assert can_open is True

    # 2.5% drawdown from peak 10,000: trips circuit breaker
    guard.update_equity(9_750.0)
    assert guard.is_circuit_breaker_triggered
    guard._pending_approvals = 0
    can_open_halted, reason = guard.can_open_position("TrendContinuationSMC", inst_id, OrderSide.BUY, 500.0, 0)
    assert can_open_halted is False
    assert "24h drawdown breached" in reason


def test_portfolio_guard_stale_order_cancellation():
    guard = PortfolioGuard(default_order_timeout_secs=0.1)  # 100ms timeout
    order = _create_dummy_order("SOL-USD-PERP", OrderSide.BUY)

    guard.register_order_submitted(order, "OrderBookImbalance", 500.0, timeout_seconds=0.05)
    assert len(guard.get_stale_orders_to_cancel()) == 0

    # Wait for order to become stale
    time.sleep(0.06)
    stale = guard.get_stale_orders_to_cancel()
    assert len(stale) == 1
    assert stale[0].order_id == str(order.client_order_id)

    # Acknowledge cancellation
    guard.acknowledge_order_cancelled(stale[0].order_id)
    assert len(guard.pending_orders) == 0
