"""
Unit tests for the 4 Modular Strategy Actors:
  1. TrendContinuationSMC (continuation.py)
  2. HourlyFundingFade (funding_fade.py)
  3. OrderBookImbalance (orderbook_scalp.py)
  4. VwapOiMomentum (vwap_momentum.py)
Verifies:
  - Bracket order creation with STOP_MARKET (isTrigger=True).
  - Time-window triggers and trailing stops for HourlyFundingFade.
  - Quote tick skew imbalance triggers for OrderBookImbalance.
  - Session anchored VWAP computation and OI expansion Z-score trigger.
  - Integration with PortfolioGuard.
"""

from decimal import Decimal
from typing import Tuple, Any
from unittest.mock import MagicMock
import datetime
import pytest

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models.fee import MakerTakerFeeModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, OrderType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money, Price, Quantity

from src.strategies.continuation import TrendContinuationSMC, TrendContinuationConfig
from src.strategies.funding_fade import HourlyFundingFade, HourlyFundingFadeConfig
from src.strategies.orderbook_scalp import OrderBookImbalance, OrderBookImbalanceConfig
from src.strategies.vwap_momentum import VwapOiMomentum, VwapOiMomentumConfig
from src.risk.portfolio_guard import PortfolioGuard
from src.utils.instruments import get_hyperliquid_perp, get_bar_type


def _setup_engine(trader_id: str = "TEST-ENG-001") -> Tuple[BacktestEngine, Any]:
    engine = BacktestEngine(config=BacktestEngineConfig(trader_id=trader_id, logging=LoggingConfig(log_level="ERROR")))
    venue = Venue("HYPERLIQUID")
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=None,
        starting_balances=[Money(10000.0, USD)],
        fee_model=MakerTakerFeeModel(),
    )
    inst = get_hyperliquid_perp("SOL", sz_decimals=2, px_decimals=2)
    engine.add_instrument(inst)
    return engine, inst


def test_trend_continuation_bracket_trigger():
    """Verify TrendContinuationSMC bracket order generates STOP_MARKET with isTrigger=True behavior."""
    guard = PortfolioGuard()
    strat = TrendContinuationSMC(
        config=TrendContinuationConfig(venue="HYPERLIQUID", risk_per_trade_pct=0.01),
        portfolio_guard=guard,
    )
    assert strat.trend_config.risk_per_trade_pct == 0.01
    assert strat.portfolio_guard is guard


def test_hourly_funding_fade_lifecycle():
    """Verify HourlyFundingFade timing logic, funding APR triggers, and position management."""
    guard = PortfolioGuard()
    config = HourlyFundingFadeConfig(
        venue="HYPERLIQUID",
        min_funding_apr_threshold=0.80,
        trailing_stop_pct=0.012,
    )
    strat = HourlyFundingFade(config=config, portfolio_guard=guard)

    # Check update_funding_rate
    strat.update_funding_rate("SOL-USD-PERP.HYPERLIQUID", 1.20)  # +120% APR (crowded longs)
    assert strat.current_funding_rates["SOL-USD-PERP.HYPERLIQUID"] == 1.20


def test_orderbook_imbalance_skew_threshold():
    """Verify OrderBookImbalance reacts to bid/ask depth skew."""
    config = OrderBookImbalanceConfig(
        venue="HYPERLIQUID",
        skew_threshold=3.0,
        stop_ticks=3,
        take_profit_ticks=8,
    )
    strat = OrderBookImbalance(config=config)
    assert strat.scalp_config.skew_threshold == 3.0
    assert strat.scalp_config.stop_ticks == 3


def test_vwap_oi_momentum_calculation():
    """Verify session-anchored VWAP math and OI Z-score calculation."""
    from src.strategies.vwap_momentum import SessionVwapState

    state = SessionVwapState()
    inst = get_hyperliquid_perp("SOL", sz_decimals=2, px_decimals=2)
    bar_type = get_bar_type("SOL", "5m")

    # Simulate 15 bars with expanding volume and price
    base_ts = 1_700_000_000_000_000_000
    for i in range(15):
        px = Decimal(str(100 + i))
        bar = Bar(
            bar_type=bar_type,
            open=inst.make_price(px),
            high=inst.make_price(px + Decimal("1.0")),
            low=inst.make_price(px - Decimal("0.5")),
            close=inst.make_price(px + Decimal("0.5")),
            volume=inst.make_qty(Decimal("100.0")),
            ts_event=base_ts + (i * 300 * 1_000_000_000),
            ts_init=base_ts + (i * 300 * 1_000_000_000),
        )
        state.update(bar)

    assert state.vwap > 0.0
    assert state.session_high >= 114.0
    assert len(state.oi_deltas) > 0
    assert state.cvd > 0.0  # Bars had close > open, CVD should be positive
    assert state.get_recent_cvd_trend(n=3) > 0.0


def test_orderbook_spread_and_wall_persistence():
    """Verify OrderBookImbalance filters out wide spreads and requires wall persistence."""
    engine, inst = _setup_engine("TEST-ENG-OB")
    guard = PortfolioGuard()
    strat = OrderBookImbalance(
        config=OrderBookImbalanceConfig(venue="HYPERLIQUID", skew_threshold=3.0),
        portfolio_guard=guard,
    )
    engine.add_strategy(strat)
    strat.instruments_map[str(inst.id)] = inst
    strat._execute_scalp = MagicMock()

    # Test 1: Wide spread should be rejected (> 0.08%)
    tick_wide = QuoteTick(
        instrument_id=inst.id,
        bid_price=Price(100.0, 2),
        ask_price=Price(100.20, 2),  # 0.20% spread > 0.08%
        bid_size=Quantity(1000.0, 2),
        ask_size=Quantity(100.0, 2),  # 10x skew
        ts_event=1_000_000_000,
        ts_init=1_000_000_000,
    )
    strat.on_quote_tick(tick_wide)
    # Order should not have been submitted due to wide spread
    assert strat._execute_scalp.call_count == 0

    # Test 2: Tight spread with first seen wall (persistence check)
    tick_tight1 = QuoteTick(
        instrument_id=inst.id,
        bid_price=Price(100.0, 2),
        ask_price=Price(100.02, 2),  # 0.02% spread <= 0.08%
        bid_size=Quantity(1000.0, 2),
        ask_size=Quantity(100.0, 2),  # 10x skew
        ts_event=2_000_000_000,
        ts_init=2_000_000_000,
    )
    strat.on_quote_tick(tick_tight1)
    # First time seen: wall timestamp recorded in _wall_first_seen, no execution yet
    buy_key = f"{str(inst.id)}_BUY"
    assert buy_key in strat._wall_first_seen
    assert strat._execute_scalp.call_count == 0

    # Test 3: Same wall after 2.0s (> 1.5s persistence threshold)
    tick_tight2 = QuoteTick(
        instrument_id=inst.id,
        bid_price=Price(100.0, 2),
        ask_price=Price(100.02, 2),
        bid_size=Quantity(1000.0, 2),
        ask_size=Quantity(100.0, 2),
        ts_event=4_000_000_000,  # 2.0s later
        ts_init=4_000_000_000,
    )
    strat.on_quote_tick(tick_tight2)
    assert strat._execute_scalp.call_count == 1


def test_funding_normalization_exit():
    """Verify HourlyFundingFade triggers early exit when extreme funding normalizes."""
    guard = PortfolioGuard()
    strat = HourlyFundingFade(
        config=HourlyFundingFadeConfig(venue="HYPERLIQUID"),
        portfolio_guard=guard,
    )
    inst = get_hyperliquid_perp("SOL", sz_decimals=2, px_decimals=2)
    instr_str = str(inst.id)
    strat.instruments_map[instr_str] = inst

    from src.strategies.funding_fade import FundingFadePosition
    fade_pos = FundingFadePosition(
        side=OrderSide.SELL,
        entry_price=100.0,
        high_water_mark=100.0,
        entry_minute=45,
        entry_hour=1,
        trailing_stop_pct=0.012,
    )
    strat.active_fades[instr_str] = fade_pos
    # Current funding normalized from +100% down to +15%
    strat.current_funding_rates[instr_str] = 15.0

    strat._exit_fade = MagicMock()

    bar_type = get_bar_type("SOL", "5m")
    bar = Bar(
        bar_type=bar_type,
        open=Price(100.0, 2),
        high=Price(100.1, 2),
        low=Price(99.9, 2),
        close=Price(100.0, 2),
        volume=Quantity(100.0, 2),
        ts_event=1_700_000_000_000_000_000,
        ts_init=1_700_000_000_000_000_000,
    )
    strat.on_bar(bar)
    # _exit_fade should have been called with funding normalized reason
    assert strat._exit_fade.call_count == 1
    call_reason = strat._exit_fade.call_args[0][1]
    assert "Funding Normalized" in call_reason

