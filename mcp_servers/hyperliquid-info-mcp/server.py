"""
hyperliquid-info-mcp MCP Server
Read-focused market surveillance server for Hyperliquid DEX.
Exposes real-time L2 order books, historical k-lines, open interest, and funding rate analytics
without requiring or exposing private trading keys to the LLM.
"""

import os
import sys
import json
import time
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
load_dotenv(dotenv_path)

from fastmcp import FastMCP
from hyperliquid.info import Info
from hyperliquid.utils import constants

mcp = FastMCP("hyperliquid-info-mcp")

def _get_info_client():
    network = os.getenv("HYPERLIQUID_NETWORK", "mainnet")
    base_url = constants.TESTNET_API_URL if network == "testnet" else constants.MAINNET_API_URL
    return Info(base_url, skip_ws=True)

@mcp.tool()
def get_l2_snapshot(coin: str) -> Dict[str, Any]:
    """
    Fetches real-time L2 order book depth (top bids and asks) for a specified market.

    Args:
        coin: Asset symbol e.g. "BTC", "ETH", "HYPE"
    """
    try:
        info = _get_info_client()
        l2_data = info.l2_snapshot(coin)
        return {
            "status": "SUCCESS",
            "coin": coin,
            "bids": l2_data.get("levels", [[]])[0][:10],
            "asks": l2_data.get("levels", [[], []])[1][:10],
            "time": l2_data.get("time")
        }
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

@mcp.tool()
def get_historical_klines(
    coin: str,
    interval: str = "1h",
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None
) -> Dict[str, Any]:
    """
    Fetches historical price candles (k-lines) for macro technical evaluation.

    Args:
        coin: Symbol e.g. "BTC"
        interval: Candle timeframe ("1m", "5m", "15m", "1h", "4h", "1d")
        start_time_ms: Optional start timestamp in milliseconds
        end_time_ms: Optional end timestamp in milliseconds
    """
    try:
        info = _get_info_client()
        now_ms = int(time.time() * 1000)
        if end_time_ms is None:
            end_time_ms = now_ms
        if start_time_ms is None:
            # Default to last 24 hours
            start_time_ms = end_time_ms - (24 * 3600 * 1000)

        candles = info.candles_snapshot(coin, interval, start_time_ms, end_time_ms)
        return {
            "status": "SUCCESS",
            "coin": coin,
            "interval": interval,
            "candle_count": len(candles),
            "candles": candles[-50:]  # Limit payload to last 50 candles
        }
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

@mcp.tool()
def get_open_interest(coin: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetches current open interest analytics across Hyperliquid markets.

    Args:
        coin: Optional symbol to filter by (e.g. "BTC"). If None, returns top markets.
    """
    try:
        info = _get_info_client()
        contexts = info.meta_and_asset_ctxs()
        universe = contexts[0]["universe"]
        asset_ctxs = contexts[1]

        results = []
        for i, asset in enumerate(universe):
            name = asset["name"]
            if coin and coin.upper() != name:
                continue
            ctx = asset_ctxs[i]
            results.append({
                "symbol": name,
                "open_interest": float(ctx.get("openInterest", 0)),
                "oracle_price": float(ctx.get("oraclePx", 0)),
                "funding_rate": float(ctx.get("funding", 0)),
                "volume_24h": float(ctx.get("dayNtlVlm", 0))
            })

        return {
            "status": "SUCCESS",
            "count": len(results),
            "markets": results[:20] if not coin else results
        }
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

@mcp.tool()
def get_funding_rates(coin: str) -> Dict[str, Any]:
    """
    Fetches recent funding rates and funding rate differentials for a specific perp market.

    Args:
        coin: Symbol e.g. "BTC"
    """
    try:
        info = _get_info_client()
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (7 * 24 * 3600 * 1000)
        history = info.funding_history(coin, start_ms, now_ms)
        
        # Calculate recent average funding
        recent_rates = [float(item.get("funding", 0)) for item in history[-24:]]
        avg_rate = sum(recent_rates) / len(recent_rates) if recent_rates else 0.0

        return {
            "status": "SUCCESS",
            "coin": coin,
            "average_hourly_funding_24h": avg_rate,
            "annualized_funding_pct": avg_rate * 24 * 365 * 100,
            "recent_records": history[-10:]
        }
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

@mcp.tool()
def get_meta() -> Dict[str, Any]:
    """
    Fetches market universe metadata (asset indices, max leverage, margin requirements).
    """
    try:
        info = _get_info_client()
        meta = info.meta()
        return {
            "status": "SUCCESS",
            "universe_size": len(meta.get("universe", [])),
            "assets": meta.get("universe", [])[:30]
        }
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

@mcp.tool()
def get_user_state_info(address: str) -> Dict[str, Any]:
    """
    Read-only fetch of any user's public state (open positions, account value) by EVM address.

    Args:
        address: EVM wallet address (0x...)
    """
    try:
        info = _get_info_client()
        state = info.user_state(address)
        return {
            "status": "SUCCESS",
            "address": address,
            "margin_summary": state.get("marginSummary"),
            "positions": state.get("assetPositions")
        }
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("Testing hyperliquid-info-mcp tools...")
        print(get_open_interest("BTC"))
        print(get_funding_rates("BTC"))
    else:
        mcp.run()
