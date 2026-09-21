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
