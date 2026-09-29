"""
Unit tests for Hyperliquid instrument factory and bar type utilities.
"""

from decimal import Decimal
import pytest
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CurrencyPair

from src.utils.instruments import get_hyperliquid_perp, get_bar_type


def test_get_hyperliquid_perp_specifications():
    instrument = get_hyperliquid_perp(coin="BTC", sz_decimals=5, px_decimals=1)
    assert isinstance(instrument, CurrencyPair)
    assert str(instrument.id) == "BTC-USD-PERP.HYPERLIQUID"
    assert instrument.venue == Venue("HYPERLIQUID")
    assert instrument.maker_fee == Decimal("-0.0001")  # -0.01% maker rebate
    assert instrument.taker_fee == Decimal("0.00035")  # 0.035% taker fee
    assert instrument.size_precision == 5
    assert instrument.price_precision == 1
    # BTC 40x max leverage: margin_init = 1/40 = 0.025, margin_maint = 0.5/40 = 0.0125
    assert instrument.margin_init == Decimal("0.025")
    assert instrument.margin_maint == Decimal("0.0125")

    # SOL 20x max leverage: margin_init = 1/20 = 0.05, margin_maint = 0.5/20 = 0.025
    sol_inst = get_hyperliquid_perp(coin="SOL")
    assert sol_inst.margin_init == Decimal("0.05")
    assert sol_inst.margin_maint == Decimal("0.025")

    # USELESS 3x max leverage: margin_init = 1/3 = 0.333333, margin_maint = 0.5/3 = 0.166667
    useless_inst = get_hyperliquid_perp(coin="USELESS")
    assert useless_inst.margin_init == Decimal("0.333333")
    assert useless_inst.margin_maint == Decimal("0.166667")


def test_get_bar_types():
    bt_4h = get_bar_type("ETH", "4h")
    assert str(bt_4h) == "ETH-USD-PERP.HYPERLIQUID-4-HOUR-LAST-EXTERNAL"

    bt_30m = get_bar_type("SOL", "30m")
    assert str(bt_30m) == "SOL-USD-PERP.HYPERLIQUID-30-MINUTE-LAST-EXTERNAL"

    bt_5m = get_bar_type("HYPE", "5m")
    assert str(bt_5m) == "HYPE-USD-PERP.HYPERLIQUID-5-MINUTE-LAST-EXTERNAL"


def test_invalid_timeframe_raises():
    with pytest.raises(ValueError):
        get_bar_type("BTC", "2h")
