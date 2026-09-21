"""
chainstack-hypercore-mcp MCP Server
Integrates high-throughput private RPC endpoints directly into Antigravity CLI/IDE,
bypassing public 100 req/min rate limits for data-heavy scrapers, high-frequency orderbook feeds,
and quant execution loops.
"""

import os
import sys
import json
import time
from typing import Dict, Any, Optional
import requests
from dotenv import load_dotenv

dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
load_dotenv(dotenv_path)

from fastmcp import FastMCP

mcp = FastMCP("chainstack-hypercore-mcp")

@mcp.tool()
def query_hypercore_rpc(
    request_type: str = "allMids",
    payload: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Executes a high-throughput RPC query directly against Chainstack HyperCore private RPC endpoint.

    Args:
        request_type: Info request type e.g. "allMids", "l2Book", "metaAndAssetCtxs"
        payload: Optional additional JSON payload parameters
    """
    private_rpc = os.getenv("CHAINSTACK_HYPERCORE_RPC_URL")
    public_rpc = "https://api.hyperliquid.xyz/info"
    
    target_url = private_rpc if (private_rpc and "your-api-key" not in private_rpc and "demo" not in private_rpc) else public_rpc
    endpoint_label = "Chainstack HyperCore Private RPC" if target_url == private_rpc else "Hyperliquid Public RPC (Fallback)"

    body = {"type": request_type}
    if payload:
        body.update(payload)

    try:
        t0 = time.time()
        resp = requests.post(target_url, json=body, headers={"Content-Type": "application/json"}, timeout=5)
        latency_ms = (time.time() - t0) * 1000
        
        return {
            "status": "SUCCESS",
            "endpoint_used": endpoint_label,
            "latency_ms": round(latency_ms, 2),
            "response": resp.json()
        }
    except Exception as e:
        return {"status": "FAILED", "endpoint_used": endpoint_label, "error": str(e)}

@mcp.tool()
def get_high_throughput_l2(coin: str) -> Dict[str, Any]:
    """
    Fetches rapid L2 order book snapshot using high-throughput Chainstack RPC bypass.

    Args:
        coin: Symbol e.g. "BTC"
    """
    return query_hypercore_rpc(request_type="l2Book", payload={"coin": coin})

@mcp.tool()
def check_rpc_latency_and_rate_limits() -> Dict[str, Any]:
    """
    Tests and compares latency and rate-limit health of Chainstack HyperCore private RPC vs public RPC.
    """
    private_rpc = os.getenv("CHAINSTACK_HYPERCORE_RPC_URL")
    public_rpc = "https://api.hyperliquid.xyz/info"

    results = {}
    
    # Test Public RPC
    try:
        t0 = time.time()
        r = requests.post(public_rpc, json={"type": "allMids"}, timeout=3)
        results["public_rpc"] = {
            "url": public_rpc,
            "status_code": r.status_code,
            "latency_ms": round((time.time() - t0) * 1000, 2),
            "rate_limit_cap": "100 req/min"
        }
    except Exception as e:
        results["public_rpc"] = {"error": str(e)}

    # Test Private RPC if configured
    if private_rpc and "your-api-key" not in private_rpc and "demo" not in private_rpc:
        try:
            t0 = time.time()
            r = requests.post(private_rpc, json={"type": "allMids"}, timeout=3)
            results["chainstack_private_rpc"] = {
                "url": private_rpc,
                "status_code": r.status_code,
                "latency_ms": round((time.time() - t0) * 1000, 2),
                "rate_limit_cap": "Unlimited / High-Throughput Tier"
            }
        except Exception as e:
            results["chainstack_private_rpc"] = {"error": str(e)}
    else:
        results["chainstack_private_rpc"] = {
            "status": "UNCONFIGURED",
            "message": "CHAINSTACK_HYPERCORE_RPC_URL is not set in .env. Falling back to public RPC."
        }

    return {
        "status": "SUCCESS",
        "health_check": results
    }

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("Testing chainstack-hypercore-mcp tools...")
        print(check_rpc_latency_and_rate_limits())
        print(get_high_throughput_l2("BTC"))
    else:
        mcp.run()
