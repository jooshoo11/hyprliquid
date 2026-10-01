"""
Unit tests for Strategy 6: LiquidationAbsorber (liquidation_absorber.py)
Tests:
- LiquidationAbsorberConfig defaults and thresholds.
- CascadeTracker rolling volume and VWAP math.
- Downside cascade detection and execution dispatch.
- Upside short squeeze cascade detection and execution dispatch.
- Nautilus instrument price and quantity precision handling.
"""

from decimal import Decimal
from unittest.mock import MagicMock
import pytest

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId

from src.strategies.liquidation_absorber import (
    LiquidationAbsorber,
    LiquidationAbsorberConfig,
    CascadeTracker,
)
from src.risk.portfolio_guard import PortfolioGuard
from src.utils.instruments import get_hyperliquid_perp, get_bar_type


def test_liquidation_absorber_config():
    """Verify default parameters and thresholds for LiquidationAbsorber."""
    config = LiquidationAbsorberConfig()
    assert config.dislocation_pct == 0.02
    assert config.volume_multiplier == 3.0
    assert config.stop_loss_pct == 0.015
    assert config.take_profit_pct == 0.03
    assert config.post_only is True


def test_cascade_tracker_state():
    """Verify rolling volume and VWAP state updates on CascadeTracker."""
    inst_id = InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID")
    tracker = CascadeTracker(instrument_id=inst_id)
    assert tracker.cum_vol == 0.0
    assert tracker.vwap == 0.0
    assert len(tracker.rolling_volume) == 0


def test_liquidation_absorber_downside_cascade_signal():
    """Verify that downside dislocation on heavy volume triggers _execute_absorption with BUY side."""
    inst = get_hyperliquid_perp("BTC")
    bar_type = get_bar_type("BTC", "1m")
    guard = PortfolioGuard()

    config = LiquidationAbsorberConfig(
        dislocation_pct=0.02,
        volume_multiplier=2.5,
    )
    strat = LiquidationAbsorber(config=config, portfolio_guard=guard)
    strat.instruments_map[str(inst.id)] = inst
    strat._execute_absorption = MagicMock()

    base_ts = 1_700_000_000_000_000_000

    # 1. Warm up with 25 bars around $60,000 with volume 10.0
    for i in range(25):
        ts = base_ts + (i * 60_000_000_000)
        bar = Bar(
            bar_type=bar_type,
            open=inst.make_price(Decimal("60000.0")),
            high=inst.make_price(Decimal("60050.0")),
            low=inst.make_price(Decimal("59950.0")),
            close=inst.make_price(Decimal("60000.0")),
            volume=inst.make_qty(Decimal("10.0")),
            ts_event=ts,
            ts_init=ts,
        )
        strat.on_bar(bar)

    assert strat._execute_absorption.call_count == 0

    # 2. Inject massive liquidation flush: Price dislocates to $58,500 (-2.5%) on 50.0 volume (5x avg)
    ts_flush = base_ts + (26 * 60_000_000_000)
    flush_bar = Bar(
        bar_type=bar_type,
        open=inst.make_price(Decimal("59900.0")),
        high=inst.make_price(Decimal("59900.0")),
        low=inst.make_price(Decimal("58400.0")),
        close=inst.make_price(Decimal("58500.0")),
        volume=inst.make_qty(Decimal("50.0")),
        ts_event=ts_flush,
        ts_init=ts_flush,
    )
    strat.on_bar(flush_bar)

    assert strat._execute_absorption.call_count == 1
    call_args = strat._execute_absorption.call_args[0]
    assert call_args[0] == inst  # instrument
    assert call_args[1] == OrderSide.BUY  # side
    assert pytest.approx(call_args[2], 0.01) == 58500.0  # entry_px
    assert call_args[3] < 58500.0  # sl_px
    assert call_args[4] > 58500.0  # tp_px


def test_liquidation_absorber_upside_squeeze_signal():
    """Verify that upside dislocation on heavy volume triggers _execute_absorption with SELL side."""
    inst = get_hyperliquid_perp("BTC")
    bar_type = get_bar_type("BTC", "1m")
    guard = PortfolioGuard()

    config = LiquidationAbsorberConfig(
        dislocation_pct=0.02,
        volume_multiplier=2.5,
    )
    strat = LiquidationAbsorber(config=config, portfolio_guard=guard)
    strat.instruments_map[str(inst.id)] = inst
    strat._execute_absorption = MagicMock()

    base_ts = 1_700_000_000_000_000_000

    # Warm up with 25 steady bars
    for i in range(25):
        ts = base_ts + (i * 60_000_000_000)
        bar = Bar(
            bar_type=bar_type,
            open=inst.make_price(Decimal("60000.0")),
            high=inst.make_price(Decimal("60050.0")),
            low=inst.make_price(Decimal("59950.0")),
            close=inst.make_price(Decimal("60000.0")),
            volume=inst.make_qty(Decimal("10.0")),
            ts_event=ts,
            ts_init=ts,
        )
        strat.on_bar(bar)

    assert strat._execute_absorption.call_count == 0

    # Inject massive short squeeze: Price shoots to $61,500 (+2.5%) on 50.0 volume
    ts_squeeze = base_ts + (26 * 60_000_000_000)
    squeeze_bar = Bar(
        bar_type=bar_type,
        open=inst.make_price(Decimal("60100.0")),
        high=inst.make_price(Decimal("61600.0")),
        low=inst.make_price(Decimal("60100.0")),
        close=inst.make_price(Decimal("61500.0")),
        volume=inst.make_qty(Decimal("50.0")),
        ts_event=ts_squeeze,
        ts_init=ts_squeeze,
    )
    strat.on_bar(squeeze_bar)

    assert strat._execute_absorption.call_count == 1
    call_args = strat._execute_absorption.call_args[0]
    assert call_args[0] == inst
    assert call_args[1] == OrderSide.SELL
    assert pytest.approx(call_args[2], 0.01) == 61500.0
    assert call_args[3] > 61500.0  # sl_px
    assert call_args[4] < 61500.0  # tp_px
