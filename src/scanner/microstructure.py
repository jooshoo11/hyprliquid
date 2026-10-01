"""
Hummingbot-Grade Microstructure & Order Book Analytics (src/scanner/microstructure.py)

Incorporates battle-tested algorithms from hummingbot/hummingbot:
1. Volume-Weighted Micro-Price (Fair Value estimation)
2. Normalized Order Book Imbalance (OBI) across top N depth levels
3. Spread Cost in Basis Points (BPS)
4. Slippage & Market Impact estimation
"""

from typing import Dict, Any, List, Optional, Tuple
from src.utils.schemas import OrderBookMicrostructure


def calculate_micro_price(
    best_bid: float,
    best_ask: float,
    bid_size: float,
    ask_size: float,
) -> float:
    """
    Hummingbot micro-price formula: Volume-weighted fair mid-price.
    Weights opposite side quotes by volume:
    micro_px = (bid_sz * ask_px + ask_sz * bid_px) / (bid_sz + ask_sz)
    """
    total_sz = bid_size + ask_size
    if total_sz <= 0:
        return (best_bid + best_ask) / 2.0
    return (bid_size * best_ask + ask_size * best_bid) / total_sz


def calculate_order_book_imbalance(
    bids: List[Dict[str, Any]],
    asks: List[Dict[str, Any]],
    depth: int = 5,
) -> float:
    """
    Hummingbot normalized Order Book Imbalance (OBI) metric.
    Returns value between -1.0 (100% ask heavy / sell pressure)
    and +1.0 (100% bid heavy / buy pressure).
    """
    bid_vol = sum(float(b.get("sz", 0.0)) * float(b.get("px", 0.0)) for b in bids[:depth])
    ask_vol = sum(float(a.get("sz", 0.0)) * float(a.get("px", 0.0)) for a in asks[:depth])
    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def calculate_spread_bps(best_bid: float, best_ask: float) -> float:
    """Calculate bid-ask spread in basis points (bps)."""
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0:
        return 0.0
    return ((best_ask - best_bid) / mid) * 10000.0


def estimate_market_impact(
    side: str,
    notional_usd: float,
    levels: List[Dict[str, Any]],
) -> Tuple[float, float]:
    """
    Walk orderbook levels to calculate effective average fill price
    and slippage in basis points for a market order of size `notional_usd`.
    Returns: (avg_fill_px, slippage_bps)
    """
    if not levels:
        return (0.0, 999.0)

    remaining_usd = notional_usd
    total_qty = 0.0
    best_px = float(levels[0].get("px", 0.0))

    for lvl in levels:
        px = float(lvl.get("px", 0.0))
        sz = float(lvl.get("sz", 0.0))
        lvl_usd = px * sz
        if lvl_usd <= 0:
            continue

        if remaining_usd <= lvl_usd:
            take_qty = remaining_usd / px
            total_qty += take_qty
            remaining_usd = 0.0
            break
        else:
            total_qty += sz
            remaining_usd -= lvl_usd

    if remaining_usd > 0 or total_qty <= 0:
        # Depth exhausted, high slippage
        return (best_px * (1.02 if side.upper() == "BUY" else 0.98), 200.0)

    avg_fill_px = notional_usd / total_qty
    if best_px > 0:
        slippage_bps = abs(avg_fill_px - best_px) / best_px * 10000.0
    else:
        slippage_bps = 0.0

    return (avg_fill_px, slippage_bps)


def analyze_l2_snapshot(
    coin: str,
    l2_snapshot: Dict[str, Any],
) -> Optional[OrderBookMicrostructure]:
    """
    Construct complete Hummingbot microstructure analytics from raw Hyperliquid L2 snapshot.
    """
    if not l2_snapshot:
        return None

    levels = l2_snapshot.get("levels", [])
    if len(levels) < 2:
        return None

    bids = levels[0]
    asks = levels[1]
    if not bids or not asks:
        return None

    best_bid = float(bids[0].get("px", 0.0))
    bid_sz = float(bids[0].get("sz", 0.0))
    best_ask = float(asks[0].get("px", 0.0))
    ask_sz = float(asks[0].get("sz", 0.0))

    if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
        return None

    mid = (best_bid + best_ask) / 2.0
    micro = calculate_micro_price(best_bid, best_ask, bid_sz, ask_sz)
    spread = calculate_spread_bps(best_bid, best_ask)
    obi_l1 = calculate_order_book_imbalance(bids, asks, depth=1)
    obi_l5 = calculate_order_book_imbalance(bids, asks, depth=5)

    tot_bid_usd = sum(float(b.get("px", 0)) * float(b.get("sz", 0)) for b in bids[:10])
    tot_ask_usd = sum(float(a.get("px", 0)) * float(a.get("sz", 0)) for a in asks[:10])

    is_liquid = (spread <= 12.0) and (min(tot_bid_usd, tot_ask_usd) >= 5000.0)

    return OrderBookMicrostructure(
        coin=coin.upper(),
        best_bid=best_bid,
        best_ask=best_ask,
        mid_price=mid,
        micro_price=micro,
        spread_bps=round(spread, 2),
        depth_imbalance_top=round(obi_l1, 3),
        depth_imbalance_5=round(obi_l5, 3),
        total_bid_depth_usd=round(tot_bid_usd, 2),
        total_ask_depth_usd=round(tot_ask_usd, 2),
        is_liquid=is_liquid,
    )
