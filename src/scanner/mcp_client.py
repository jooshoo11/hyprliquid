"""
Hyperliquid Info MCP Client & Market Discovery Service with Polars Engine.
Interfaces with hyperliquid-info-mcp server tools and direct Info client
for market metadata, open interest, 24h volume, volatility ranking, and L2 snapshots.
All filtering, ATR volatility, and displacement feature calculations are executed
strictly using Polars expressions.
"""

import os
import sys
import time
import math
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv

import polars as pl

# Load environment
load_dotenv()

from hyperliquid.utils import constants


def compute_candle_features(candles: List[Dict[str, Any]]) -> pl.DataFrame:
    """
    Computes displacement candles, True Range, and ATR volatility using Polars expressions.

    Parameters
    ----------
    candles : list[dict]
        Raw candle records with keys 't'/'T', 'o', 'h', 'l', 'c', 'v'.

    Returns
    -------
    pl.DataFrame
        Polars DataFrame with calculated displacement, True Range (tr), and 14-period ATR.
    """
    if not candles:
        return pl.DataFrame()

    df = pl.DataFrame({
        "timestamp": [int(c.get("t") or c.get("T") or 0) for c in candles],
        "open": [float(c["o"]) for c in candles],
        "high": [float(c["h"]) for c in candles],
        "low": [float(c["l"]) for c in candles],
        "close": [float(c["c"]) for c in candles],
        "volume": [float(c["v"]) for c in candles],
    })

    # Polars expressions for displacement candles (close - open) and True Range
    window_size = min(14, len(df)) if len(df) > 0 else 1
    df = df.with_columns([
        (pl.col("close") - pl.col("open")).alias("displacement"),
        (pl.col("close") - pl.col("open")).abs().alias("abs_displacement"),
        pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - pl.col("close").shift(1)).abs(),
            (pl.col("low") - pl.col("close").shift(1)).abs(),
        ).fill_null(pl.col("high") - pl.col("low")).alias("tr"),
        ((pl.col("close") - pl.col("close").shift(1)) / pl.col("close").shift(1)).alias("returns"),
    ]).with_columns([
        pl.col("tr").rolling_mean(window_size=window_size).alias("atr_14"),
    ])

    return df


class HyperliquidInfoClient:
    """
    Client interface for Hyperliquid Info MCP server / read endpoints.
    Provides market universe ranking, orderbook depth snapshots, and historical candles
    leveraging Polars for high-performance data processing.
    """

    def __init__(self, network: Optional[str] = None):
        if network is not None:
            self.network = network
        elif "mainnet" in os.getenv("CHAINSTACK_HYPERCORE_RPC_URL", "").lower():
            self.network = "mainnet"
        else:
            self.network = os.getenv("HYPERLIQUID_NETWORK", "mainnet")

        default_url = constants.TESTNET_API_URL if self.network == "testnet" else constants.MAINNET_API_URL
        # Use Chainstack private RPC to completely bypass public rate limits!
        self.base_url = os.getenv(
            "CHAINSTACK_HYPERCORE_RPC_URL",
            default_url
        )
        if not self.base_url.endswith("/info"):
            self.api_url = f"{self.base_url.rstrip('/')}/info"
        else:
            self.api_url = self.base_url
        import requests
        self._session = requests.Session()
        from hyperliquid.info import Info
        info_base_url = default_url if ("demo" in self.base_url) else self.base_url
        self._info = Info(info_base_url, skip_ws=True)

    def _resolve_meta_cache_path(self) -> str:
        """Resolve path to catalog/mcp_meta_cache.json."""
        cwd_path = os.path.join(os.getcwd(), "catalog", "mcp_meta_cache.json")
        if os.path.exists(os.path.dirname(cwd_path)):
            return cwd_path
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        return os.path.join(repo_root, "catalog", "mcp_meta_cache.json")

    def _save_meta_cache(self, data: Any) -> None:
        """Persist fresh meta and asset contexts to catalog/mcp_meta_cache.json."""
        try:
            import json
            cache_path = self._resolve_meta_cache_path()
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp_path = f"{cache_path}.tmp"
            with open(tmp_path, "w") as f:
                json.dump({
                    "status": "SUCCESS",
                    "endpoint_used": self.api_url,
                    "timestamp": time.time(),
                    "response": data,
                }, f)
            os.replace(tmp_path, cache_path)
        except Exception:
            pass

    def get_meta(self) -> Dict[str, Any]:
        """Fetch market universe metadata."""
        try:
            resp = self._session.post(self.api_url, json={"type": "meta"}, timeout=10)
            if resp.status_code != 200 and "chainstack" in self.api_url:
                fallback_url = (
                    constants.TESTNET_API_URL if self.network == "testnet" else constants.MAINNET_API_URL
                ) + "/info"
                resp = self._session.post(fallback_url, json={"type": "meta"}, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        meta, _ = self.get_meta_and_asset_ctxs()
        return meta

    def get_meta_and_asset_ctxs(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Fetch universe definitions and real-time asset contexts with a 5-second in-memory cache."""
        import time
        if not hasattr(self, "_cached_meta_ctx"):
            self._cached_meta_ctx = None
            self._cached_meta_ctx_time = 0
            
        if time.monotonic() - self._cached_meta_ctx_time < 5.0 and self._cached_meta_ctx:
            return self._cached_meta_ctx[0], self._cached_meta_ctx[1]
            
        try:
            resp = self._session.post(self.api_url, json={"type": "metaAndAssetCtxs"}, timeout=10)
            if resp.status_code != 200 and "chainstack" in self.api_url:
                fallback_url = (
                    constants.TESTNET_API_URL if self.network == "testnet" else constants.MAINNET_API_URL
                ) + "/info"
                resp = self._session.post(fallback_url, json={"type": "metaAndAssetCtxs"}, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                self._cached_meta_ctx = (data[0], data[1])
                self._cached_meta_ctx_time = time.monotonic()
                self._save_meta_cache(data)
                return data[0], data[1]
        except Exception:
            pass
            
        # Fallback to local cache if network fails
        import json
        cache_path = self._resolve_meta_cache_path()
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            resp = cached.get("response", cached)
            return resp[0], resp[1]
        except Exception:
            if os.path.exists("catalog/mcp_meta_cache.json"):
                with open("catalog/mcp_meta_cache.json", "r") as f:
                    cached = json.load(f)
                resp = cached.get("response", cached)
                return resp[0], resp[1]
            raise

    def get_l2_snapshot(self, coin: str) -> Dict[str, Any]:
        """
        Fetch real-time L2 order book depth (top bids and asks) for a specified market.
        """
        try:
            snapshot = self._info.l2_snapshot(coin)
            levels = snapshot.get("levels", [[], []])
            bids = levels[0][:10] if len(levels) > 0 else []
            asks = levels[1][:10] if len(levels) > 1 else []
            return {
                "status": "SUCCESS",
                "coin": coin,
                "bids": bids,
                "asks": asks,
                "time": snapshot.get("time"),
            }
        except Exception as e:
            return {"status": "FAILED", "coin": coin, "error": str(e)}

    def get_historical_klines(
        self,
        coin: str,
        interval: str = "1h",
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical candle bars (k-lines) for a coin.
        """
        now_ms = int(time.time() * 1000)
        if end_time_ms is None:
            end_time_ms = now_ms
        if start_time_ms is None:
            start_time_ms = end_time_ms - (24 * 3600 * 1000)

        candles = self._info.candles_snapshot(coin, interval, start_time_ms, end_time_ms)
        return candles

    def get_funding_rate(self, coin: str) -> Optional[float]:
        """
        Fetch annualized funding rate APR for a coin from real-time asset contexts.
        """
        try:
            meta, asset_ctxs = self.get_meta_and_asset_ctxs()
            universe = meta.get("universe", [])
            for i, asset in enumerate(universe):
                if asset.get("name") == coin:
                    if i < len(asset_ctxs):
                        ctx = asset_ctxs[i]
                        hourly_rate = float(ctx.get("funding", 0.0))
                        return hourly_rate * 24 * 365
            return 0.0
        except Exception:
            return None

    def get_top_perpetuals(
        self,
        top_n: int = 20,
        min_volume_usd: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        Scan market universe and rank top perpetuals by 24h volume and volatility using Polars.

        Parameters
        ----------
        top_n : int
            Number of top perpetuals to return (default 20).
        min_volume_usd : float
            Minimum 24h notional volume filter.

        Returns
        -------
        list[dict]
            Top N ranked instruments with metadata (name, volume, volatility, szDecimals, etc.).
        """
        meta, asset_ctxs = self.get_meta_and_asset_ctxs()
        universe = meta.get("universe", [])

        records = []
        for i, asset in enumerate(universe):
            if asset.get("isDelisted", False):
                continue

            coin_name = asset.get("name", "")
            ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}

            prev_day_px = float(ctx.get("prevDayPx", 0.0))
            oracle_px = float(ctx.get("oraclePx", 0.0))
            return_24h = ((oracle_px - prev_day_px) / prev_day_px * 100.0) if prev_day_px > 0 else 0.0

            records.append({
                "name": coin_name,
                "szDecimals": int(asset.get("szDecimals", 2)),
                "maxLeverage": int(asset.get("maxLeverage", 10)),
                "volume_24h": float(ctx.get("dayNtlVlm", 0.0)),
                "oracle_price": oracle_px,
                "prev_day_price": prev_day_px,
                "return_24h_pct": return_24h,
                "open_interest": float(ctx.get("openInterest", 0.0)),
            })

        if not records:
            return []

        # 1. Polars LazyFrame with 24h volume and price filters
        df_universe = pl.DataFrame(records).lazy()
        filtered_lf = df_universe.filter(
            (pl.col("volume_24h") >= min_volume_usd) & (pl.col("oracle_price") > 0.0)
        ).sort(by="volume_24h", descending=True)

        liquid_pool = filtered_lf.collect()
        
        # PROXY: Use abs(return_24h_pct) as a real-time proxy for volatility
        # This requires ZERO additional REST requests, eliminating 429 rate limits!
        ranked_df = liquid_pool.with_columns([
            pl.col("return_24h_pct").abs().alias("volatility_proxy")
        ]).with_columns([
            pl.col("volume_24h").rank(descending=True).alias("vol_rank"),
            pl.col("volatility_proxy").rank(descending=True).alias("volat_rank"),
        ]).with_columns([
            (pl.col("vol_rank") * 0.5 + pl.col("volat_rank") * 0.5).alias("composite_rank")
        ]).sort(by="composite_rank").head(top_n)

        return ranked_df.to_dicts()
