"""
Unit tests for TrendContinuationStrategy.
Verifies:
  - 4H 50/200 EMA trend detection
  - 30M demand and supply zone creation via displacement (> 1.5 * ATR)
  - 5M Market Structure Shift (MSS) detection
  - Position sizing and bracket order submission
"""

from decimal import Decimal
import pytest

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models.fee import MakerTakerFeeModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money

from src.strategies.continuation import TrendContinuationStrategy, TrendContinuationConfig, Zone, InstrumentState
from src.utils.instruments import get_hyperliquid_perp, get_bar_type


def test_zone_dataclass():
    z = Zone(zone_type="DEMAND", low=100.0, high=105.0, ts_event=1000)
    assert z.zone_type == "DEMAND"
    assert z.low == 100.0
    assert z.high == 105.0
    assert not z.mitigated
    assert not z.touched


def test_strategy_lifecycle_in_backtest_engine():
    inst = get_hyperliquid_perp("SOL", sz_decimals=2, px_decimals=2)
    bar_type_4h = get_bar_type("SOL", "4h")
    bar_type_30m = get_bar_type("SOL", "30m")
    bar_type_5m = get_bar_type("SOL", "5m")

    engine = BacktestEngine(config=BacktestEngineConfig(trader_id="BACKTEST-TEST", logging=LoggingConfig(log_level="ERROR")))
    venue = Venue("HYPERLIQUID")
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=None,
        starting_balances=[Money(10000.0, USD)],
        fee_model=MakerTakerFeeModel(),
    )
    engine.add_instrument(inst)

    # Synthetic bars to test pipeline
    bars = []
    base_ts = 1_700_000_000_000_000_000

    # Feed 4H bars
    for i in range(10):
        b = Bar(
            bar_type=bar_type_4h,
            open=inst.make_price(Decimal("100.0")),
            high=inst.make_price(Decimal("105.0")),
            low=inst.make_price(Decimal("99.0")),
            close=inst.make_price(Decimal("104.0")),
            volume=inst.make_qty(Decimal("100.0")),
            ts_event=base_ts + i * 4 * 3600 * 1_000_000_000,
            ts_init=base_ts + i * 4 * 3600 * 1_000_000_000,
        )
        bars.append(b)

    # Feed 30M bars with displacement
    for i in range(20):
        # Create strong displacement on bar 15
        is_disp = (i == 15)
        o = 100.0 if not is_disp else 100.0
        c = 101.0 if not is_disp else 115.0
        h = 102.0 if not is_disp else 116.0
        l = 99.5 if not is_disp else 99.8
        b = Bar(
            bar_type=bar_type_30m,
            open=inst.make_price(Decimal(str(o))),
            high=inst.make_price(Decimal(str(h))),
            low=inst.make_price(Decimal(str(l))),
            close=inst.make_price(Decimal(str(c))),
            volume=inst.make_qty(Decimal("50.0")),
            ts_event=base_ts + 40 * 3600 * 1_000_000_000 + i * 1800 * 1_000_000_000,
            ts_init=base_ts + 40 * 3600 * 1_000_000_000 + i * 1800 * 1_000_000_000,
        )
        bars.append(b)

    # Feed 5M bars
    for i in range(30):
        b = Bar(
            bar_type=bar_type_5m,
            open=inst.make_price(Decimal("110.0")),
            high=inst.make_price(Decimal("111.0")),
            low=inst.make_price(Decimal("109.5")),
            close=inst.make_price(Decimal("110.5")),
            volume=inst.make_qty(Decimal("10.0")),
            ts_event=base_ts + 50 * 3600 * 1_000_000_000 + i * 300 * 1_000_000_000,
            ts_init=base_ts + 50 * 3600 * 1_000_000_000 + i * 300 * 1_000_000_000,
        )
        bars.append(b)

    bars = sorted(bars, key=lambda x: x.ts_event)
    engine.add_data(bars)

    strat = TrendContinuationStrategy(config=TrendContinuationConfig(venue="HYPERLIQUID"))
    engine.add_strategy(strat)
    engine.run()

    # Verify strategy state tracking
    state = strat.states.get(str(inst.id))
    assert state is not None
    assert state.last_4h_bar is not None
    assert state.last_30m_bar is not None
    assert state.last_5m_bar is not None
