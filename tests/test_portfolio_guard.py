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


def test_dynamic_sizing_multipliers_and_performance_status():
    guard = PortfolioGuard(max_strategy_equity_pct=0.25)

    # 1. Baseline multiplier without trades is 1.0x
    assert guard.get_strategy_sizing_multiplier("TrendContinuationSMC") == 1.0
    assert guard.get_strategy_sizing_multiplier("HourlyFundingFade") == 1.0

    # 2. Add winning trades for TrendContinuationSMC (75% win rate -> HOT 1.6x)
    trades = [
        {"strategy": "TrendContinuationSMC", "gross_pnl": 50.0, "fees": 1.0, "net_pnl": 49.0},
        {"strategy": "TrendContinuationSMC", "gross_pnl": 30.0, "fees": 1.0, "net_pnl": 29.0},
        {"strategy": "TrendContinuationSMC", "gross_pnl": 40.0, "fees": 1.0, "net_pnl": 39.0},
        {"strategy": "TrendContinuationSMC", "gross_pnl": -10.0, "fees": 1.0, "net_pnl": -11.0},
        # Add losing trades for HourlyFundingFade (<25% win rate -> PROBATION 0.4x)
        {"strategy": "HourlyFundingFade", "gross_pnl": -30.0, "fees": 1.0, "net_pnl": -31.0},
        {"strategy": "HourlyFundingFade", "gross_pnl": -20.0, "fees": 1.0, "net_pnl": -21.0},
    ]

    guard.update_dynamic_allocations(trades)

    # Hot strategy scales up to 1.6x sizing
    assert guard.get_strategy_sizing_multiplier("TrendContinuationSMC") == 1.6
    assert guard.get_strategy_sizing_multiplier("TrendContinuationSMC-001") == 1.6

    # Sizing multiplier is never throttled below 1.0x (no 60% shrinkage penalty!)
    assert guard.get_strategy_sizing_multiplier("HourlyFundingFade") == 1.0

    # Performance status verification
    status = guard.get_strategy_performance_status()
    assert "TrendContinuationSMC" in status
    assert status["TrendContinuationSMC"]["multiplier"] == 1.6
    assert "HOT" in status["TrendContinuationSMC"]["tier"]
    assert status["TrendContinuationSMC"]["win_rate_pct"] == 75.0
    assert status["HourlyFundingFade"]["multiplier"] == 0.4


def test_portfolio_guard_per_strategy_position_limit():
    """Verify that OrderBookImbalance cannot exceed 1 position while other strategies can take multiple."""
    guard = PortfolioGuard(max_total_open_positions=8)
    guard.update_equity(1000.0)
    inst_sol = InstrumentId(Symbol("SOL-USD-PERP"), Venue("HYPERLIQUID"))
    inst_btc = InstrumentId(Symbol("BTC-USD-PERP"), Venue("HYPERLIQUID"))

    # 1. OrderBookImbalance first position is approved
    can_open, _ = guard.can_open_position("OrderBookImbalance", inst_sol, OrderSide.BUY, 50.0, 0)
    assert can_open is True

    # Register first position
    order1 = _create_dummy_order("SOL-USD-PERP", OrderSide.BUY)
    guard.register_order_submitted(order1, "OrderBookImbalance", 50.0)

    # 2. Second OrderBookImbalance position is BLOCKED (capped at 1)
    can_open_2, reason_2 = guard.can_open_position("OrderBookImbalance", inst_btc, OrderSide.BUY, 50.0, 1)
    assert can_open_2 is False
    assert "reached max position limit (1)" in reason_2

    # 3. But TrendContinuationSMC can still open positions (up to 3)
    can_open_smc, _ = guard.can_open_position("TrendContinuationSMC", inst_btc, OrderSide.BUY, 100.0, 1)
    assert can_open_smc is True

    # 4. Closing the OrderBookImbalance position frees the slot
    guard.register_position_closed("OrderBookImbalance", inst_sol, 50.0)
    can_open_again, _ = guard.can_open_position("OrderBookImbalance", inst_btc, OrderSide.BUY, 50.0, 1)
    assert can_open_again is True


def test_ground_truth_margin_sync():
    """Verify sync_open_positions recalculates allocated margin directly from actual positions."""
    from unittest.mock import MagicMock
    from nautilus_trader.model.objects import Quantity, Price

    guard = PortfolioGuard(max_strategy_equity_pct=1.0)
    guard.update_equity(100.0)

    # Simulate 1 open position for TrendContinuationSMC
    pos1 = MagicMock()
    pos1.instrument_id = InstrumentId(Symbol("SOL-USD-PERP"), Venue("HYPERLIQUID"))
    pos1.strategy_id = "TrendContinuationSMC-000"
    pos1.is_long = True
    pos1.quantity = Quantity(0.2, 2)
    pos1.avg_px_open = Price(100.0, 2)

    guard.sync_open_positions([pos1])

    # Allocated margin for TrendContinuationSMC should accurately equal $20.0
    assert pytest.approx(guard.strategy_allocated_margin["TrendContinuationSMC"], 0.01) == 20.0

    # Syncing with an empty list should immediately clear allocated margin (zero leakage)
    guard.sync_open_positions([])
    assert len(guard.strategy_allocated_margin) == 0


def test_single_position_notional_cap():
    """Verify that orders exceeding 40% equity cap are rejected by PortfolioGuard."""
    guard = PortfolioGuard()
    guard.update_equity(100.0)
    inst = InstrumentId(Symbol("ETH-USD-PERP"), Venue("HYPERLIQUID"))

    # $35 notional (35% equity) is allowed
    can_open, _ = guard.can_open_position("TrendContinuationSMC", inst, OrderSide.BUY, 35.0, 0)
    assert can_open is True

    # $50 notional (50% equity > 40% cap) is rejected
    can_open_large, reason = guard.can_open_position("TrendContinuationSMC", inst, OrderSide.BUY, 50.0, 0)
    assert can_open_large is False
    assert "exceeds max single position cap 40%" in reason


def test_coin_specific_max_leverage_enforcement():
    """Verify that orders exceeding official Hyperliquid max leverage for a coin are rejected."""
    guard = PortfolioGuard(max_strategy_equity_pct=50.0, max_single_position_equity_pct=None)
    guard.update_equity(100.0)

    # USELESS max leverage is 3.0x -> max allowed notional is $300 (with 1.01 buffer: $303)
    inst_useless = InstrumentId(Symbol("USELESS-USD-PERP"), Venue("HYPERLIQUID"))
    can_open_ok, _ = guard.can_open_position("TrendContinuationSMC", inst_useless, OrderSide.BUY, 250.0, 0)
    assert can_open_ok is True

    can_open_fail, reason = guard.can_open_position("TrendContinuationSMC", inst_useless, OrderSide.BUY, 350.0, 0)
    assert can_open_fail is False
    assert "exceeds Hyperliquid max leverage for USELESS (3x" in reason

    # BTC max leverage is 40.0x -> max allowed notional is $4,000
    inst_btc = InstrumentId(Symbol("BTC-USD-PERP"), Venue("HYPERLIQUID"))
    can_open_btc_ok, _ = guard.can_open_position("TrendContinuationSMC", inst_btc, OrderSide.BUY, 3500.0, 0)
    assert can_open_btc_ok is True

    can_open_btc_fail, reason_btc = guard.can_open_position("TrendContinuationSMC", inst_btc, OrderSide.BUY, 4500.0, 0)
    assert can_open_btc_fail is False
    assert "exceeds Hyperliquid max leverage for BTC (40x" in reason_btc


def test_anti_pyramiding_enforcement():
    """Verify that multiple positions on the same coin are blocked when allow_pyramiding=False."""
    guard = PortfolioGuard(max_strategy_equity_pct=3.0, allow_pyramiding=False)
    guard.update_equity(100.0)
    inst_ena = InstrumentId(Symbol("ENA-USD-PERP"), Venue("HYPERLIQUID"))

    # Initial order allowed
    can_open_first, _ = guard.can_open_position("TrendContinuationSMC", inst_ena, OrderSide.SELL, 35.0, 0)
    assert can_open_first is True

    # Register active SELL order on ENA
    order = _create_dummy_order("ENA-USD-PERP", OrderSide.SELL)
    guard.register_order_submitted(order, "TrendContinuationSMC", 35.0)

    # Second order on ENA in the same direction MUST be blocked by anti-pyramiding
    can_open_second, reason_second = guard.can_open_position("TrendContinuationSMC", inst_ena, OrderSide.SELL, 35.0, 1)
    assert can_open_second is False
    assert "Anti-pyramiding" in reason_second
    assert "Position already active on ENA" in reason_second


def test_choppy_regime_blocks_trend_continuation():
    """Verify that TrendContinuationSMC is blocked during CHOPPY_MEAN_REVERTING_RANGE while scalper is permitted."""
    guard = PortfolioGuard()
    guard.update_equity(1000.0)
    inst_eth = InstrumentId(Symbol("ETH-USD-PERP"), Venue("HYPERLIQUID"))

    guard.set_market_regime("CHOPPY_MEAN_REVERTING_RANGE")

    # TrendContinuationSMC must be rejected
    can_open_trend, reason_trend = guard.can_open_position("TrendContinuationSMC", inst_eth, OrderSide.BUY, 100.0, 0)
    assert can_open_trend is False
    assert "Regime Strategy Gate" in reason_trend
    assert "blocked during CHOPPY_MEAN_REVERTING_RANGE" in reason_trend

    # OrderBookImbalance must be allowed
    can_open_scalp, _ = guard.can_open_position("OrderBookImbalance", inst_eth, OrderSide.BUY, 100.0, 0)
    assert can_open_scalp is True


def test_strategy_circuit_breaker_lockout():
    """Verify that a strategy with 3 consecutive losses is locked out for 60 minutes."""
    guard = PortfolioGuard()
    guard.update_equity(1000.0)
    inst_sol = InstrumentId(Symbol("SOL-USD-PERP"), Venue("HYPERLIQUID"))

    # Initial order allowed
    can_open, _ = guard.can_open_position("TrendContinuationSMC", inst_sol, OrderSide.BUY, 100.0, 0)
    assert can_open is True

    # Feed 3 consecutive losing trades for TrendContinuationSMC
    losing_trades = [
        {"strategy": "TrendContinuationSMC-000", "gross_pnl": -2.0, "fees": 0.1, "net_pnl": -2.1},
        {"strategy": "TrendContinuationSMC-000", "gross_pnl": -1.5, "fees": 0.1, "net_pnl": -1.6},
        {"strategy": "TrendContinuationSMC-000", "gross_pnl": -3.0, "fees": 0.1, "net_pnl": -3.1},
    ]
    guard.update_dynamic_allocations(losing_trades)

    # Strategy should now be locked out
    assert guard.is_strategy_locked("TrendContinuationSMC") is True
    stats = guard.get_strategy_performance_status()
    assert "TrendContinuationSMC" in stats
    assert stats["TrendContinuationSMC"]["is_locked"] is True
    assert "CIRCUIT BREAKER" in stats["TrendContinuationSMC"]["tier"]

    # can_open_position must block new orders
    can_open_blocked, reason_blocked = guard.can_open_position("TrendContinuationSMC", inst_sol, OrderSide.BUY, 100.0, 0)
    assert can_open_blocked is False
    assert "Strategy Circuit Breaker" in reason_blocked
    assert "locked out" in reason_blocked


def test_bypass_risk_guard_unconstrained_max_leverage():
    """Verify that when bypass_risk_guard is True, all internal throttles/caps are bypassed up to exchange max leverage."""
    guard = PortfolioGuard(bypass_risk_guard=True)
    guard.update_equity(100.0)

    # BTC max leverage is 40.0x -> max allowed notional is $4,000 (with buffer: $4,200)
    inst_btc = InstrumentId(Symbol("BTC-USD-PERP"), Venue("HYPERLIQUID"))
    # Order at 35x leverage ($3,500 on $100 equity) is approved even though it exceeds standard 40% cap and 5x cap
    can_open_btc, reason_btc = guard.can_open_position("TrendContinuationSMC", inst_btc, OrderSide.BUY, 3500.0, 0)
    assert can_open_btc is True
    assert "Risk guard bypassed: MAX LEVERAGE UNCONSTRAINED" in reason_btc

    # Order exceeding exchange maximum leverage (e.g. $4,500 > $4,000 * 1.05) is safely rejected
    can_open_exceed, reason_exceed = guard.can_open_position("TrendContinuationSMC", inst_btc, OrderSide.BUY, 4500.0, 0)
    assert can_open_exceed is False
    assert "exceeds Hyperliquid max leverage for BTC" in reason_exceed





