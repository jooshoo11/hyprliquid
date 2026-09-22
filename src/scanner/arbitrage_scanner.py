"""
Delta-Neutral Funding Carry Arbitrage Scanner with Microstructure Pre-Filtering.
Interfaces with Hyperliquid Info MCP and Chainstack HyperCore MCP tools to detect
high-yield delta-neutral funding carry arbitrage opportunities while enforcing strict
liquidity and bid-ask spread pre-filters to prevent slippage.

Strategies & Microstructure:
1. Microstructure Liquidity & Spread Pre-Filtering:
   - Rejects tokens with bid-ask spread > 0.10% (0.0010).
   - Rejects tokens with top-5 orderbook depth < $10,000 USD (evaluates min(bid_depth_5, ask_depth_5)).
2. Delta-Neutral Funding Carry Arbitrage Scanner:
   - Long Leg: Token with extreme negative funding (< -50% APR) -> longs earn funding from shorts.
   - Short Leg: Token with extreme positive funding (> +100% APR) -> shorts earn funding from longs.
   - Combined Net Carry APR: short_funding_apr - long_funding_apr.
   - Capital-Weighted APR: (|long_funding_apr| + short_funding_apr) / 2.0 (50/50 capital allocation).
3. MCP Tool Endpoints Utilized:
   - hyperliquid-info-mcp: get_funding_rates, get_open_interest, get_l2_snapshot, get_meta.
   - chainstack-hypercore-mcp: check_rpc_latency_and_rate_limits, get_high_throughput_l2.
4. Persistence:
   - Atomically serializes detected arbitrage candidates to bridge/funding_arbitrage.json.
"""

import os
import sys
import time
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

import polars as pl

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.scanner.mcp_client import HyperliquidInfoClient


class ArbitrageScanner:
    """
    Quantitative Delta-Neutral Funding Carry Arbitrage Scanner with L2 Microstructure Pre-Filtering.
    """

    def __init__(
        self,
        info_client: Optional[HyperliquidInfoClient] = None,
        bridge_path: Optional[str] = None,
    ):
        self.info_client = info_client or HyperliquidInfoClient()
        self.bridge_path = bridge_path or os.path.join(REPO_ROOT, "bridge", "funding_arbitrage.json")

    def evaluate_liquidity_and_spread(
        self,
        coin: str,
        l2_data: Optional[Dict[str, Any]] = None,
        max_spread_pct: float = 0.10,
        min_top5_depth_usd: float = 10_000.0,
    ) -> Dict[str, Any]:
        """
        Microstructure pre-filter: Audits L2 order book depth and bid-ask spread.

        Criteria:
        - Bid-Ask Spread must be <= max_spread_pct (default 0.10%).
        - Top-5 order book depth must be >= min_top5_depth_usd (default $10,000 USD).

        Parameters
        ----------
        coin : str
            Symbol (e.g. 'BTC', 'ETH').
        l2_data : dict, optional
            Pre-fetched L2 order book snapshot. If None, fetched via get_high_throughput_l2.
        max_spread_pct : float
            Maximum allowable bid-ask spread percentage (default 0.10%).
        min_top5_depth_usd : float
            Minimum allowable top-5 depth in USD (default $10,000).

        Returns
        -------
        dict
            Orderbook microstructure audit results with pass/fail decision.
        """
        if l2_data is None:
            # First try high-throughput Chainstack RPC, falling back to info client
            l2_data = self.info_client.get_high_throughput_l2(coin)

        # Handle various L2 response shapes (MCP or native Info)
        bids = []
        asks = []
        if isinstance(l2_data, dict):
            if "response" in l2_data and isinstance(l2_data["response"], dict):
                levels = l2_data["response"].get("levels", [[], []])
                bids = levels[0] if len(levels) > 0 else []
                asks = levels[1] if len(levels) > 1 else []
            elif "bids" in l2_data and "asks" in l2_data:
                bids = l2_data.get("bids", [])
                asks = l2_data.get("asks", [])
            elif "levels" in l2_data:
                levels = l2_data.get("levels", [[], []])
                bids = levels[0] if len(levels) > 0 else []
                asks = levels[1] if len(levels) > 1 else []

        if not bids or not asks:
            return {
                "coin": coin,
                "passed": False,
                "reason": "Insufficient order book depth (empty bids or asks)",
                "best_bid": 0.0,
                "best_ask": 0.0,
                "mid_price": 0.0,
                "spread_pct": 999.0,
                "bid_depth_5": 0.0,
                "ask_depth_5": 0.0,
                "top5_depth": 0.0,
            }

        try:
            best_bid = float(bids[0]["px"])
            best_ask = float(asks[0]["px"])
        except (KeyError, IndexError, TypeError, ValueError):
            return {
                "coin": coin,
                "passed": False,
                "reason": "Malformed order book level data",
                "best_bid": 0.0,
                "best_ask": 0.0,
                "mid_price": 0.0,
                "spread_pct": 999.0,
                "bid_depth_5": 0.0,
                "ask_depth_5": 0.0,
                "top5_depth": 0.0,
            }

        if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
            return {
                "coin": coin,
                "passed": False,
                "reason": f"Inverted or non-positive bid/ask: bid={best_bid}, ask={best_ask}",
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid_price": 0.0,
                "spread_pct": 999.0,
                "bid_depth_5": 0.0,
                "ask_depth_5": 0.0,
                "top5_depth": 0.0,
            }

        mid_price = (best_bid + best_ask) / 2.0
        spread = (best_ask - best_bid) / mid_price
        spread_pct = spread * 100.0

        # Calculate top-5 depth in USD
        bid_depth_5 = sum(float(b.get("sz", 0.0)) * float(b.get("px", 0.0)) for b in bids[:5])
        ask_depth_5 = sum(float(a.get("sz", 0.0)) * float(a.get("px", 0.0)) for a in asks[:5])
        top5_depth = min(bid_depth_5, ask_depth_5)
        total_depth_5 = bid_depth_5 + ask_depth_5

        # Check filter conditions
        reasons = []
        if spread_pct > max_spread_pct:
            reasons.append(f"Spread {spread_pct:.3f}% > {max_spread_pct:.2f}% limit")
        if top5_depth < min_top5_depth_usd:
            reasons.append(f"Top-5 depth ${top5_depth:,.0f} < ${min_top5_depth_usd:,.0f} limit")

        passed = len(reasons) == 0

        return {
            "coin": coin,
            "passed": passed,
            "reason": "; ".join(reasons) if not passed else "Passed spread and depth filters",
            "best_bid": round(best_bid, 6),
            "best_ask": round(best_ask, 6),
            "mid_price": round(mid_price, 6),
            "spread_pct": round(spread_pct, 4),
            "bid_depth_5": round(bid_depth_5, 2),
            "ask_depth_5": round(ask_depth_5, 2),
            "top5_depth": round(top5_depth, 2),
            "total_depth_5": round(total_depth_5, 2),
        }

    def scan_funding_arbitrage(
        self,
        max_negative_apr: float = -0.50,
        min_positive_apr: float = 1.00,
        check_liquidity: bool = True,
        max_spread_pct: float = 0.10,
        min_top5_depth_usd: float = 10_000.0,
        market_universe: Optional[List[Dict[str, Any]]] = None,
        l2_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Scan market universe for delta-neutral funding carry arbitrage pairs.

        Criteria:
        - Long candidate: coin with funding APR < max_negative_apr (< -50% APR).
          Holding LONG receives funding from shorts.
        - Short candidate: coin with funding APR > min_positive_apr (> +100% APR).
          Holding SHORT receives funding from longs.
        - Pre-filtering: Filter out tokens where bid-ask spread > 0.10% or top-5 depth < $10,000.
        - Net Carry APR: short_funding_apr - long_funding_apr.

        Parameters
        ----------
        max_negative_apr : float
            Threshold for extreme negative funding (default -0.50, i.e. -50% APR).
        min_positive_apr : float
            Threshold for extreme positive funding (default 1.00, i.e. +100% APR).
        check_liquidity : bool
            Whether to enforce spread and top-5 depth pre-filtering (default True).
        max_spread_pct : float
            Max allowed bid-ask spread percentage (default 0.10%).
        min_top5_depth_usd : float
            Min allowed top-5 orderbook depth in USD (default $10,000).
        market_universe : list[dict], optional
            Override market universe data for testing.
        l2_cache : dict, optional
            Pre-computed L2 snapshots for testing.

        Returns
        -------
        list[dict]
            Detected delta-neutral carry arbitrage pairs ranked by net carry APR.
        """
        # Fetch universe metadata & asset contexts via MCP tool endpoints
        if market_universe is None:
            meta, asset_ctxs = self.info_client.get_meta_and_asset_ctxs()
            universe = meta.get("universe", [])
            records = []
            for i, asset in enumerate(universe):
                if asset.get("isDelisted", False):
                    continue
                name = asset.get("name", "")
                ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}
                hourly_funding = float(ctx.get("funding", 0.0))
                funding_apr = hourly_funding * 24 * 365
                px = float(ctx.get("oraclePx", 0.0))
                vol_24h = float(ctx.get("dayNtlVlm", 0.0))
                records.append({
                    "coin": name,
                    "funding_apr": funding_apr,
                    "oracle_price": px,
                    "volume_24h": vol_24h,
                    "open_interest": float(ctx.get("openInterest", 0.0)),
                })
        else:
            records = market_universe

        if not records:
            return []

        # Polars filtering for extreme funding candidates
        df = pl.DataFrame(records)

        # 1. Negative funding candidates (< -50% APR) -> Long Leg candidates
        neg_candidates_df = df.filter(pl.col("funding_apr") < max_negative_apr)
        # 2. Positive funding candidates (> +100% APR) -> Short Leg candidates
        pos_candidates_df = df.filter(pl.col("funding_apr") > min_positive_apr)

        neg_candidates = neg_candidates_df.to_dicts()
        pos_candidates = pos_candidates_df.to_dicts()

        if not neg_candidates or not pos_candidates:
            return []

        # Microstructure Liquidity & Spread Pre-Filter
        liquidity_audits: Dict[str, Dict[str, Any]] = {}

        def get_l2_audit(coin: str) -> Dict[str, Any]:
            if coin in liquidity_audits:
                return liquidity_audits[coin]
            l2 = l2_cache.get(coin) if l2_cache else None
            audit = self.evaluate_liquidity_and_spread(
                coin=coin,
                l2_data=l2,
                max_spread_pct=max_spread_pct,
                min_top5_depth_usd=min_top5_depth_usd,
            )
            liquidity_audits[coin] = audit
            return audit

        # Filter out high-slippage candidates if check_liquidity is active
        qualified_longs = []
        for c in neg_candidates:
            coin_name = c["coin"]
            if check_liquidity:
                audit = get_l2_audit(coin_name)
                if not audit["passed"]:
                    continue
                c["microstructure"] = audit
            qualified_longs.append(c)

        qualified_shorts = []
        for c in pos_candidates:
            coin_name = c["coin"]
            if check_liquidity:
                audit = get_l2_audit(coin_name)
                if not audit["passed"]:
                    continue
                c["microstructure"] = audit
            qualified_shorts.append(c)

        arbitrage_pairs: List[Dict[str, Any]] = []

        # Form delta-neutral pairs
        for l_cand in qualified_longs:
            long_coin = l_cand["coin"]
            l_apr = float(l_cand["funding_apr"])
            l_px = float(l_cand.get("oracle_price", 0.0))
            l_micro = l_cand.get("microstructure", {})

            for s_cand in qualified_shorts:
                short_coin = s_cand["coin"]
                if long_coin == short_coin:
                    continue

                s_apr = float(s_cand["funding_apr"])
                s_px = float(s_cand.get("oracle_price", 0.0))
                s_micro = s_cand.get("microstructure", {})

                # Combined net carry APR = short earns s_apr + long earns |l_apr|
                net_carry_apr = s_apr - l_apr
                capital_weighted_apr = (abs(l_apr) + s_apr) / 2.0

                pair_id = f"{long_coin}_LONG__{short_coin}_SHORT"
                desc = (
                    f"Long {long_coin} ({l_apr * 100:+.1f}% APR carry) + "
                    f"Short {short_coin} ({s_apr * 100:+.1f}% APR carry) -> "
                    f"Combined Net Carry: {net_carry_apr * 100:+.1f}% APR "
                    f"({capital_weighted_apr * 100:.1f}% capital-weighted yield)"
                )

                arbitrage_pairs.append({
                    "pair_id": pair_id,
                    "strategy": "DELTA_NEUTRAL_FUNDING_CARRY",
                    "delta_neutral": True,
                    "net_carry_apr": round(net_carry_apr, 4),
                    "net_carry_apr_pct": round(net_carry_apr * 100.0, 2),
                    "capital_weighted_apr_pct": round(capital_weighted_apr * 100.0, 2),
                    "description": desc,
                    "long_leg": {
                        "coin": long_coin,
                        "action": "LONG",
                        "funding_apr": round(l_apr, 4),
                        "funding_apr_pct": round(l_apr * 100.0, 2),
                        "oracle_price": l_px,
                        "spread_pct": l_micro.get("spread_pct", 0.0),
                        "top5_depth_usd": l_micro.get("top5_depth", 0.0),
                    },
                    "short_leg": {
                        "coin": short_coin,
                        "action": "SHORT",
                        "funding_apr": round(s_apr, 4),
                        "funding_apr_pct": round(s_apr * 100.0, 2),
                        "oracle_price": s_px,
                        "spread_pct": s_micro.get("spread_pct", 0.0),
                        "top5_depth_usd": s_micro.get("top5_depth", 0.0),
                    },
                    "timestamp": time.time(),
                })

        # Sort pairs by highest net carry APR
        arbitrage_pairs.sort(key=lambda p: p["net_carry_apr"], reverse=True)
        return arbitrage_pairs

    def find_arbitrage_pairs(
        self,
        min_net_carry_apr: float = 80.0,
        max_spread_pct: float = 0.10,
        min_top5_depth_usd: float = 10_000.0,
        use_cache: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Query detected delta-neutral funding carry arbitrage pairs.
        Checks bridge/funding_arbitrage.json if fresh (< 5m), or executes a fresh scan.
        """
        min_apr_pct = min_net_carry_apr if min_net_carry_apr > 1.0 else min_net_carry_apr * 100.0
        if use_cache and os.path.exists(self.bridge_path):
            try:
                with open(self.bridge_path, "r") as f:
                    data = json.load(f)
                ts = data.get("timestamp", 0.0)
                if time.time() - ts < 300:
                    pairs = data.get("pairs", [])
                    filtered = [p for p in pairs if p.get("net_carry_apr_pct", 0.0) >= min_apr_pct]
                    if filtered:
                        return filtered
            except Exception:
                pass

        pairs = self.scan_funding_arbitrage(
            max_negative_apr=-0.50,
            min_positive_apr=1.00,
            check_liquidity=True,
            max_spread_pct=max_spread_pct,
            min_top5_depth_usd=min_top5_depth_usd,
        )
        return [p for p in pairs if p.get("net_carry_apr_pct", 0.0) >= min_apr_pct]

    def save_funding_arbitrage(
        self,
        pairs: List[Dict[str, Any]],
        filepath: Optional[str] = None,
    ) -> str:
        """
        Atomically persist detected funding arbitrage pairs into bridge/funding_arbitrage.json.

        Parameters
        ----------
        pairs : list[dict]
            Detected arbitrage candidate pairs.
        filepath : str, optional
            Destination path (defaults to bridge/funding_arbitrage.json).

        Returns
        -------
        str
            Resolved path of the saved file.
        """
        target_path = filepath or self.bridge_path
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        # Check RPC latency and health via Chainstack MCP tool
        try:
            rpc_health = self.info_client.check_rpc_latency_and_rate_limits()
        except Exception as e:
            rpc_health = {"status": "UNKNOWN", "error": str(e)}

        payload = {
            "timestamp": time.time(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "strategy": "DELTA_NEUTRAL_FUNDING_CARRY",
            "thresholds": {
                "max_negative_funding_apr": -0.50,
                "min_positive_funding_apr": 1.00,
                "max_spread_pct": 0.10,
                "min_top5_depth_usd": 10000.0,
            },
            "count": len(pairs),
            "rpc_health": rpc_health.get("health_check", rpc_health),
            "pairs": pairs,
        }

        tmp_path = f"{target_path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, target_path)

        return target_path

    def scan_and_save(
        self,
        filepath: Optional[str] = None,
        max_negative_apr: float = -0.50,
        min_positive_apr: float = 1.00,
        check_liquidity: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        End-to-end orchestration: scan market universe, filter on liquidity,
        and atomically write to bridge/funding_arbitrage.json.

        Returns
        -------
        list[dict]
            Detected arbitrage candidate pairs.
        """
        pairs = self.scan_funding_arbitrage(
            max_negative_apr=max_negative_apr,
            min_positive_apr=min_positive_apr,
            check_liquidity=check_liquidity,
        )
        self.save_funding_arbitrage(pairs=pairs, filepath=filepath)
        return pairs
