"""
Hyperliquid instrument helpers and factory functions.
Configures CurrencyPair instruments with Hyperliquid perpetual specifications,
tick sizes, lot sizes, and maker/taker fee tiers (-0.01% maker / 0.035% taker).
"""

import os
import json
import logging
from decimal import Decimal
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity

_LEVERAGE_CACHE: Dict[str, float] = {}


def get_coin_max_leverage(coin: str) -> float:
    """
    Get the official Hyperliquid maximum leverage for a given coin.
    Loads dynamically from catalog/mcp_meta_cache.json if available,
    falling back to known defaults (BTC: 40, ETH: 25, SOL: 20, etc.) or 10.0.
    """
    coin = coin.upper().split("-")[0].split(".")[0]
    global _LEVERAGE_CACHE
    if not _LEVERAGE_CACHE:
        cache_path = os.path.join(os.path.dirname(__file__), "..", "..", "catalog", "mcp_meta_cache.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    data = json.load(f)
                    resp = data.get("response", [])
                    universe = resp[0].get("universe", []) if isinstance(resp, list) and len(resp) > 0 else []
                    for u in universe:
                        name = u.get("name")
                        lev = u.get("maxLeverage")
                        if name and lev is not None:
                            _LEVERAGE_CACHE[name.upper()] = float(lev)
            except Exception as e:
                logger.debug("Failed to load leverage cache from %s: %s", cache_path, e)

    if coin in _LEVERAGE_CACHE:
        return _LEVERAGE_CACHE[coin]

    defaults = {
        "BTC": 40.0,
        "ETH": 25.0,
        "SOL": 20.0,
        "DOGE": 10.0,
        "NEAR": 10.0,
        "SUI": 10.0,
        "FARTCOIN": 10.0,
        "kPEPE": 10.0,
        "TAO": 5.0,
        "USELESS": 3.0,
        "PROVE": 3.0,
        "NIL": 3.0,
    }
    return defaults.get(coin, 10.0)


# Default precision mapping for common perpetuals
DEFAULT_SZ_DECIMALS: Dict[str, int] = {
    "BTC": 5,
    "ETH": 4,
    "SOL": 2,
    "HYPE": 2,
    "AVAX": 2,
    "SUI": 1,
    "ARB": 1,
    "OP": 1,
    "LINK": 2,
    "AAVE": 2,
    "BNB": 3,
    "DOGE": 0,
    "kPEPE": 0,
    "NEAR": 1,
    "APT": 2,
    "ATOM": 2,
    "CRV": 1,
    "ENA": 1,
    "ONDO": 1,
    "WIF": 1,
}

DEFAULT_PX_DECIMALS: Dict[str, int] = {
    "BTC": 1,
    "ETH": 2,
    "SOL": 3,
    "HYPE": 4,
    "AVAX": 3,
    "SUI": 4,
    "ARB": 4,
    "OP": 4,
    "LINK": 3,
    "AAVE": 2,
    "BNB": 2,
    "DOGE": 5,
    "kPEPE": 6,
    "NEAR": 3,
    "APT": 4,
    "ATOM": 3,
    "CRV": 4,
    "ENA": 4,
    "ONDO": 4,
    "WIF": 4,
}

VENUE = Venue("HYPERLIQUID")


def get_hyperliquid_perp(
    coin: str,
    sz_decimals: Optional[int] = None,
    px_decimals: Optional[int] = None,
) -> CurrencyPair:
    """
    Create a NautilusTrader CurrencyPair configured for Hyperliquid Perpetual.

    Parameters
    ----------
    coin : str
        The base asset symbol (e.g. 'BTC', 'ETH', 'SOL').
    sz_decimals : int, optional
        The size decimals (lot size precision). Defaults to known coin map or 2.
    px_decimals : int, optional
        The price decimals (tick size precision). Defaults to known coin map or 4.

    Returns
    -------
    CurrencyPair
    """
    coin = coin.upper()
    if sz_decimals is None:
        sz_decimals = DEFAULT_SZ_DECIMALS.get(coin, 2)
    if px_decimals is None:
        px_decimals = DEFAULT_PX_DECIMALS.get(coin, 4)

    raw_symbol = Symbol(f"{coin}-USD-PERP")
    instrument_id = InstrumentId(symbol=raw_symbol, venue=VENUE)

    # Hyperliquid fee schedule: -0.01% maker rebate, 0.035% taker fee
    maker_fee = Decimal("-0.0001")
    taker_fee = Decimal("0.00035")

    # Hyperliquid official leverage margins (margin_init = 1 / max_leverage)
    max_lev = get_coin_max_leverage(coin)
    margin_init = Decimal(str(round(1.0 / max(1.0, max_lev), 6)))
    margin_maint = Decimal(str(round(0.5 / max(1.0, max_lev), 6)))

    price_inc = Decimal(f"1e-{px_decimals}") if px_decimals > 0 else Decimal("1")
    size_inc = Decimal(f"1e-{sz_decimals}") if sz_decimals > 0 else Decimal("1")

    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=raw_symbol,
        base_currency=USD,
        quote_currency=USD,
        price_precision=px_decimals,
        price_increment=Price(price_inc, px_decimals),
        multiplier=Quantity(1, 0),
        size_precision=sz_decimals,
        size_increment=Quantity(size_inc, sz_decimals),
        margin_init=margin_init,
        margin_maint=margin_maint,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        ts_event=0,
        ts_init=0,
    )


def get_bar_type(coin: str, timeframe: str) -> BarType:
    """
    Generate NautilusTrader BarType for Hyperliquid 4H, 30M, or 5M candles.

    Parameters
    ----------
    coin : str
        Base asset symbol (e.g. 'BTC').
    timeframe : str
        One of '4h', '30m', '5m'.

    Returns
    -------
    BarType
    """
    coin = coin.upper()
    instr_id_str = f"{coin}-USD-PERP.HYPERLIQUID"
    tf_map = {
        "4h": f"{instr_id_str}-4-HOUR-LAST-EXTERNAL",
        "30m": f"{instr_id_str}-30-MINUTE-LAST-EXTERNAL",
        "5m": f"{instr_id_str}-5-MINUTE-LAST-EXTERNAL",
        "1m": f"{instr_id_str}-1-MINUTE-LAST-EXTERNAL",
    }
    tf_key = timeframe.lower()
    if tf_key not in tf_map:
        raise ValueError(f"Unsupported timeframe: {timeframe}. Must be one of {list(tf_map.keys())}")
    return BarType.from_str(tf_map[tf_key])
