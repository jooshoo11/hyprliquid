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

try:
    import polars as pl
except ImportError:
    pl = None

# Load environment
load_dotenv()

try:
    from hyperliquid.utils import constants
    from hyperliquid.info import Info
except ImportError:
    class _Constants:
        MAINNET_API_URL = "https://api.hyperliquid.xyz"
        TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"
    constants = _Constants()

    class Info:
        """Lightweight pure-Python fallback for Hyperliquid Info client without C-extensions."""
        def __init__(self, base_url: str, skip_ws: bool = True):
            self.base_url = base_url.rstrip("/")
            if not self.base_url.endswith("/info"):
                self.api_url = f"{self.base_url}/info"
            else:
                self.api_url = self.base_url
            import requests
            self._session = requests.Session()

        def l2_snapshot(self, coin: str) -> Dict[str, Any]:
            try:
                resp = self._session.post(self.api_url, json={"type": "l2Book", "coin": coin}, timeout=10)
                return resp.json() if resp.status_code == 200 else {}
            except Exception:
                return {}

        def candles_snapshot(self, coin: str, interval: str, start_time: int, end_time: int) -> List[Dict[str, Any]]:
            try:
                resp = self._session.post(self.api_url, json={"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_time, "endTime": end_time}}, timeout=10)
                return resp.json() if resp.status_code == 200 else []
            except Exception:
                return []

        def funding_history(self, coin: str, start_time: int, end_time: int) -> List[Dict[str, Any]]:
            try:
                resp = self._session.post(self.api_url, json={"type": "fundingHistory", "coin": coin, "startTime": start_time, "endTime": end_time}, timeout=10)
                return resp.json() if resp.status_code == 200 else []
            except Exception:
                return []



def compute_candle_features(candles: List[Dict[str, Any]]):
    """
    Computes displacement candles, True Range, and ATR volatility.
    Uses Polars if available, otherwise falls back to pure Python dictionaries.
    """
    if not candles:
        return pl.DataFrame() if pl is not None else []

    if pl is not None:
        df = pl.DataFrame({
            "timestamp": [int(c.get("t") or c.get("T") or 0) for c in candles],
            "open": [float(c["o"]) for c in candles],
            "high": [float(c["h"]) for c in candles],
            "low": [float(c["l"]) for c in candles],
            "close": [float(c["c"]) for c in candles],
            "volume": [float(c["v"]) for c in candles],
        })

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

    # Pure-Python fallback when polars is unavailable
    res = []
    prev_close = None
    trs = []
    for c in candles:
        ts = int(c.get("t") or c.get("T") or 0)
        o = float(c["o"])
        h = float(c["h"])
        l = float(c["l"])
        close = float(c["c"])
        v = float(c["v"])
        disp = close - o
        abs_disp = abs(disp)
        if prev_close is not None:
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            ret = (close - prev_close) / prev_close if prev_close != 0 else 0.0
        else:
            tr = h - l
            ret = 0.0
        prev_close = close
        trs.append(tr)
        window = trs[-14:]
        atr_14 = sum(window) / len(window)
        res.append({
            "timestamp": ts, "open": o, "high": h, "low": l, "close": close, "volume": v,
            "displacement": disp, "abs_displacement": abs_disp, "tr": tr, "returns": ret, "atr_14": atr_14
        })
    return res


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
        # Use Chainstack private RPC if valid token provided; ignore demo placeholder
        chainstack_url = os.getenv("CHAINSTACK_HYPERCORE_RPC_URL", "").strip()
        if chainstack_url and "demo" not in chainstack_url and "chainstack.com" in chainstack_url:
            self.base_url = chainstack_url
        else:
            self.base_url = default_url

        if not self.base_url.endswith("/info"):
            self.api_url = f"{self.base_url.rstrip('/')}/info"
        else:
            self.api_url = self.base_url
        import requests
        self._session = requests.Session()
        self._info = Info(self.base_url, skip_ws=True)

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

    def get_all_mids(self) -> Dict[str, float]:
        """Fetch lightweight mid prices dictionary for all perpetuals (~5KB vs 500KB) to preserve battery & bandwidth."""
        try:
            resp = self._session.post(self.api_url, json={"type": "allMids"}, timeout=5)
            if resp.status_code == 200:
                raw = resp.json()
                return {k.upper(): float(v) for k, v in raw.items()}
        except Exception:
            pass
        return {}

    def get_meta_and_asset_ctxs(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Fetch universe definitions and real-time asset contexts with a 10-second in-memory cache."""
        import time
        if not hasattr(self, "_cached_meta_ctx"):
            self._cached_meta_ctx = None
            self._cached_meta_ctx_time = 0
            
        if time.monotonic() - self._cached_meta_ctx_time < 10.0 and self._cached_meta_ctx:
            return self._cached_meta_ctx[0], self._cached_meta_ctx[1]
            
        try:
            resp = self._session.post(self.api_url, json={"type": "metaAndAssetCtxs"}, timeout=5)
            if resp.status_code != 200 and "chainstack" in self.api_url:
                fallback_url = (
                    constants.TESTNET_API_URL if self.network == "testnet" else constants.MAINNET_API_URL
                ) + "/info"
                resp = self._session.post(fallback_url, json={"type": "metaAndAssetCtxs"}, timeout=5)

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

    def get_funding_rates(self, coin: str) -> Dict[str, Any]:
        """
        Fetches recent funding rates and annualized funding metrics for a specific perp market.
        Interfaces directly with the hyperliquid-info-mcp tool specification.
        """
        try:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - (7 * 24 * 3600 * 1000)
            history = self._info.funding_history(coin, start_ms, now_ms)
            recent_rates = [float(item.get("funding", 0)) for item in history[-24:]]
            avg_rate = sum(recent_rates) / len(recent_rates) if recent_rates else 0.0

            # Also retrieve current instantaneous funding rate
            current_apr = self.get_funding_rate(coin) or 0.0

            return {
                "status": "SUCCESS",
                "coin": coin,
                "average_hourly_funding_24h": avg_rate,
                "annualized_funding_pct": avg_rate * 24 * 365 * 100,
                "annualized_funding_apr": (avg_rate * 24 * 365) if avg_rate != 0.0 else current_apr,
                "current_funding_apr": current_apr,
                "recent_records": history[-10:] if history else [],
            }
        except Exception as e:
            cur_apr = self.get_funding_rate(coin)
            if cur_apr is not None:
                return {
                    "status": "SUCCESS",
                    "coin": coin,
                    "average_hourly_funding_24h": cur_apr / (24 * 365),
                    "annualized_funding_pct": cur_apr * 100,
                    "annualized_funding_apr": cur_apr,
                    "current_funding_apr": cur_apr,
                    "recent_records": [],
                }
            return {"status": "FAILED", "coin": coin, "error": str(e)}

    def get_open_interest(self, coin: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetches current open interest analytics across Hyperliquid markets.
        Interfaces with hyperliquid-info-mcp tool specification.
        """
        try:
            meta, asset_ctxs = self.get_meta_and_asset_ctxs()
            universe = meta.get("universe", [])
            results = []
            for i, asset in enumerate(universe):
                name = asset.get("name", "")
                if coin and coin.upper() != name.upper():
                    continue
                ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}
                hourly_funding = float(ctx.get("funding", 0.0))
                results.append({
                    "symbol": name,
                    "open_interest": float(ctx.get("openInterest", 0.0)),
                    "oracle_price": float(ctx.get("oraclePx", 0.0)),
                    "funding_rate": hourly_funding,
                    "funding_apr": hourly_funding * 24 * 365,
                    "volume_24h": float(ctx.get("dayNtlVlm", 0.0)),
                })

            return {
                "status": "SUCCESS",
                "count": len(results),
                "markets": results[:20] if not coin else results,
            }
        except Exception as e:
            return {"status": "FAILED", "error": str(e)}

    def check_rpc_latency_and_rate_limits(self) -> Dict[str, Any]:
        """
        Tests and compares latency and rate-limit health of Chainstack HyperCore private RPC vs public RPC.
        Interfaces with chainstack-hypercore-mcp tool specification.
        """
        private_rpc = os.getenv("CHAINSTACK_HYPERCORE_RPC_URL")
        public_rpc = "https://api.hyperliquid.xyz/info"

        results = {}
        # Test Public RPC
        try:
            t0 = time.time()
            r = self._session.post(public_rpc, json={"type": "allMids"}, timeout=3)
            results["public_rpc"] = {
                "url": public_rpc,
                "status_code": r.status_code,
                "latency_ms": round((time.time() - t0) * 1000, 2),
                "rate_limit_cap": "100 req/min",
            }
        except Exception as e:
            results["public_rpc"] = {"error": str(e)}

        # Test Private RPC if configured
        if private_rpc and "your-api-key" not in private_rpc and "demo" not in private_rpc:
            try:
                t0 = time.time()
                r = self._session.post(private_rpc, json={"type": "allMids"}, timeout=3)
                results["chainstack_private_rpc"] = {
                    "url": private_rpc,
                    "status_code": r.status_code,
                    "latency_ms": round((time.time() - t0) * 1000, 2),
                    "rate_limit_cap": "Unlimited / High-Throughput Tier",
                }
            except Exception as e:
                results["chainstack_private_rpc"] = {"error": str(e)}
        else:
            results["chainstack_private_rpc"] = {
                "status": "UNCONFIGURED",
                "message": "CHAINSTACK_HYPERCORE_RPC_URL is not set in .env. Falling back to public RPC.",
            }

        return {
            "status": "SUCCESS",
            "health_check": results,
        }

    def get_high_throughput_l2(self, coin: str) -> Dict[str, Any]:
        """
        Fetches rapid L2 order book snapshot using high-throughput Chainstack RPC bypass.
        Interfaces with chainstack-hypercore-mcp tool specification.
        """
        private_rpc = os.getenv("CHAINSTACK_HYPERCORE_RPC_URL")
        public_rpc = "https://api.hyperliquid.xyz/info"
        target_url = private_rpc if (private_rpc and "your-api-key" not in private_rpc and "demo" not in private_rpc) else public_rpc
        endpoint_label = "Chainstack HyperCore Private RPC" if target_url == private_rpc else "Hyperliquid Public RPC (Fallback)"

        body = {"type": "l2Book", "coin": coin}
        try:
            t0 = time.time()
            resp = self._session.post(target_url, json=body, headers={"Content-Type": "application/json"}, timeout=5)
            latency_ms = (time.time() - t0) * 1000
            data = resp.json()
            levels = data.get("levels", [[], []]) if isinstance(data, dict) else [[], []]
            bids = levels[0][:10] if len(levels) > 0 else []
            asks = levels[1][:10] if len(levels) > 1 else []

            return {
                "status": "SUCCESS",
                "coin": coin,
                "endpoint_used": endpoint_label,
                "latency_ms": round(latency_ms, 2),
                "bids": bids,
                "asks": asks,
                "time": data.get("time") if isinstance(data, dict) else None,
                "response": data,
            }
        except Exception as e:
            fallback = self.get_l2_snapshot(coin)
            if fallback.get("status") == "SUCCESS":
                fallback["endpoint_used"] = "Fallback get_l2_snapshot"
                return fallback
            return {"status": "FAILED", "coin": coin, "endpoint_used": endpoint_label, "error": str(e)}

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

        if pl is not None:
            # 1. Polars LazyFrame with 24h volume and price filters
            df_universe = pl.DataFrame(records).lazy()
            filtered_lf = df_universe.filter(
                (pl.col("volume_24h") >= min_volume_usd) & (pl.col("oracle_price") > 0.0)
            ).sort(by="volume_24h", descending=True)

            liquid_pool = filtered_lf.collect()
            
            # PROXY: Use abs(return_24h_pct) as a real-time proxy for volatility
            ranked_df = liquid_pool.with_columns([
                pl.col("return_24h_pct").abs().alias("volatility_proxy")
            ]).with_columns([
                pl.col("volume_24h").rank(descending=True).alias("vol_rank"),
                pl.col("volatility_proxy").rank(descending=True).alias("volat_rank"),
            ]).with_columns([
                (pl.col("vol_rank") * 0.5 + pl.col("volat_rank") * 0.5).alias("composite_rank")
            ]).sort(by="composite_rank").head(top_n)

            return ranked_df.to_dicts()

        # Pure-Python fallback
        filtered = [r for r in records if r["volume_24h"] >= min_volume_usd and r["oracle_price"] > 0.0]
        if not filtered:
            return []

        for r in filtered:
            r["volatility_proxy"] = abs(r["return_24h_pct"])

        by_vol = sorted(filtered, key=lambda x: x["volume_24h"], reverse=True)
        for rank, item in enumerate(by_vol, start=1):
            item["vol_rank"] = float(rank)

        by_volat = sorted(filtered, key=lambda x: x["volatility_proxy"], reverse=True)
        for rank, item in enumerate(by_volat, start=1):
            item["volat_rank"] = float(rank)

        for item in filtered:
            item["composite_rank"] = item["vol_rank"] * 0.5 + item["volat_rank"] * 0.5

        filtered.sort(key=lambda x: x["composite_rank"])
        return filtered[:top_n]
