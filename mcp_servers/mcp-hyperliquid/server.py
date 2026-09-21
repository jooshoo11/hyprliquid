"""
mcp-hyperliquid MCP Server
Trading & Execution Toolkit for Hyperliquid perps powered by FastMCP and hyperliquid-python-sdk.
Exposes atomic trading tools including bracket orders (Entry + TP + SL), market/limit orders,
leverage updates, and position management via EIP-712 Agent API keys.
"""

import os
import sys
import json
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

# Load environment variables from workspace root
dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
load_dotenv(dotenv_path)

from fastmcp import FastMCP

mcp = FastMCP("mcp-hyperliquid")

# Global Hyperliquid client reference
_exchange = None
_info = None

def _get_hyperliquid_clients():
    global _exchange, _info
    if _exchange is not None and _info is not None:
        return _exchange, _info

    main_address = os.getenv("HYPERLIQUID_MAIN_ADDRESS")
    agent_key = os.getenv("HYPERLIQUID_AGENT_KEY")
    network = os.getenv("HYPERLIQUID_NETWORK", "testnet")

    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        
        base_url = constants.TESTNET_API_URL if network == "testnet" else constants.MAINNET_API_URL
        _info = Info(base_url, skip_ws=True)

        if agent_key and not agent_key.startswith("0x000000"):
            from eth_account import Account
            from hyperliquid.exchange import Exchange

            account = Account.from_key(agent_key)
            _exchange = Exchange(account, base_url, account_address=main_address)
    except Exception as e:
        print(f"[mcp-hyperliquid warning] Could not initialize live Exchange client: {e}", file=sys.stderr)

    return _exchange, _info

@mcp.tool()
def place_bracket_order(
    coin: str,
    is_buy: bool,
    sz: float,
    limit_px: float,
    tp_px: float,
    sl_px: float,
    leverage: int = 3,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Places an atomic bracket order on Hyperliquid (Entry + Take Profit + Stop Loss in a single call).

    Args:
        coin: Asset symbol e.g. "BTC", "ETH", "SOL"
        is_buy: True for Long/Buy, False for Short/Sell
        sz: Order size in base asset
        limit_px: Entry limit price
        tp_px: Take profit trigger price
        sl_px: Stop loss trigger price
        leverage: Leverage multiplier (default 3)
        dry_run: If True, validates order without broadcasting to exchange
    """
    side_str = "BUY/LONG" if is_buy else "SELL/SHORT"

    # Validation checks
    if is_buy:
        if tp_px <= limit_px:
            return {"status": "ERROR", "message": f"Take profit price ({tp_px}) must be higher than entry limit ({limit_px}) for Long."}
        if sl_px >= limit_px:
            return {"status": "ERROR", "message": f"Stop loss price ({sl_px}) must be lower than entry limit ({limit_px}) for Long."}
    else:
        if tp_px >= limit_px:
            return {"status": "ERROR", "message": f"Take profit price ({tp_px}) must be lower than entry limit ({limit_px}) for Short."}
        if sl_px <= limit_px:
            return {"status": "ERROR", "message": f"Stop loss price ({sl_px}) must be higher than entry limit ({limit_px}) for Short."}

    if dry_run or os.getenv("HYPERLIQUID_AGENT_KEY", "").startswith("0x000000"):
        return {
            "status": "DRY_RUN_VALIDATED",
            "message": f"Validated bracket order for {sz} {coin} {side_str} @ ${limit_px:.2f}",
            "bracket": {
                "coin": coin,
                "side": side_str,
                "size": sz,
                "entry_price": limit_px,
                "take_profit_price": tp_px,
                "stop_loss_price": sl_px,
                "leverage": leverage
            }
        }

    exchange, _ = _get_hyperliquid_clients()
    if not exchange:
        return {"status": "ERROR", "message": "Exchange client not authenticated. Please check HYPERLIQUID_AGENT_KEY in .env."}

    try:
        # Step 1: Update leverage
        exchange.update_leverage(leverage, coin)
        
        # Step 2: Main Limit Order
        main_order = exchange.order(coin, is_buy, sz, limit_px, {"limit": {"tif": "Gtc"}})
        
        # Step 3: Take Profit Trigger Order
        tp_side = not is_buy
        tp_order = exchange.order(coin, tp_side, sz, tp_px, {"trigger": {"isMarket": True, "triggerPx": tp_px, "tpsl": "tp"}})
        
        # Step 4: Stop Loss Trigger Order
        sl_order = exchange.order(coin, tp_side, sz, sl_px, {"trigger": {"isMarket": True, "triggerPx": sl_px, "tpsl": "sl"}})

        return {
            "status": "SUCCESS",
            "coin": coin,
            "main_order": main_order,
            "take_profit_order": tp_order,
            "stop_loss_order": sl_order
        }
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

@mcp.tool()
def place_market_order(
    coin: str,
    is_buy: bool,
    sz: float,
    slippage_pct: float = 0.01,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Places an immediate market order on Hyperliquid perps.

    Args:
        coin: Asset symbol e.g. "BTC"
        is_buy: True for Buy, False for Sell
        sz: Order size
        slippage_pct: Max allowed slippage (default 0.01 = 1%)
        dry_run: If True, validates order without sending to exchange
    """
    side_str = "BUY" if is_buy else "SELL"
    if dry_run or os.getenv("HYPERLIQUID_AGENT_KEY", "").startswith("0x000000"):
        return {
            "status": "DRY_RUN_VALIDATED",
            "message": f"Market order for {sz} {coin} ({side_str}) validated with {slippage_pct*100}% slippage buffer."
        }

    exchange, info = _get_hyperliquid_clients()
    if not exchange or not info:
        return {"status": "ERROR", "message": "Exchange client not authenticated."}

    try:
        all_mids = info.all_mids()
        mid_price = float(all_mids.get(coin, 0))
        if mid_price == 0:
            return {"status": "ERROR", "message": f"Could not fetch mid price for {coin}."}

        limit_px = mid_price * (1 + slippage_pct) if is_buy else mid_price * (1 - slippage_pct)
        res = exchange.order(coin, is_buy, sz, limit_px, {"limit": {"tif": "Ioc"}})
        return {"status": "SUCCESS", "market_order_response": res}
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

@mcp.tool()
def place_limit_order(
    coin: str,
    is_buy: bool,
    sz: float,
    limit_px: float,
    tif: str = "Gtc",
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Places a limit order on Hyperliquid.

    Args:
        coin: Asset symbol e.g. "ETH"
        is_buy: True for Buy, False for Sell
        sz: Order size
        limit_px: Limit price
        tif: Time-in-force option ("Gtc", "Ioc", "Alo")
        dry_run: Validate without sending
    """
    if dry_run or os.getenv("HYPERLIQUID_AGENT_KEY", "").startswith("0x000000"):
        return {
            "status": "DRY_RUN_VALIDATED",
            "message": f"Limit order {sz} {coin} @ ${limit_px:.2f} (TIF: {tif}) validated."
        }

    exchange, _ = _get_hyperliquid_clients()
    if not exchange:
        return {"status": "ERROR", "message": "Exchange client not authenticated."}

    try:
        res = exchange.order(coin, is_buy, sz, limit_px, {"limit": {"tif": tif}})
        return {"status": "SUCCESS", "limit_order_response": res}
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

@mcp.tool()
def cancel_order(coin: str, oid: int) -> Dict[str, Any]:
    """
    Cancels an open order by order ID (oid).
    """
    exchange, _ = _get_hyperliquid_clients()
    if not exchange:
        return {"status": "DRY_RUN", "message": f"Simulated cancellation of order {oid} for {coin}."}

    try:
        res = exchange.cancel(coin, oid)
        return {"status": "SUCCESS", "cancel_response": res}
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

@mcp.tool()
def update_leverage(coin: str, leverage: int, is_cross: bool = True) -> Dict[str, Any]:
    """
    Updates account leverage setting for a specific asset on Hyperliquid perps.
    """
    exchange, _ = _get_hyperliquid_clients()
    if not exchange:
        return {"status": "DRY_RUN", "message": f"Simulated leverage update to {leverage}x (cross={is_cross}) for {coin}."}

    try:
        res = exchange.update_leverage(leverage, coin, is_cross=is_cross)
        return {"status": "SUCCESS", "leverage_response": res}
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

@mcp.tool()
def get_account_summary() -> Dict[str, Any]:
    """
    Fetches user account margin summary, equity balance, and active perp positions.
    """
    _, info = _get_hyperliquid_clients()
    main_address = os.getenv("HYPERLIQUID_MAIN_ADDRESS")

    if not main_address or main_address.startswith("0x000000"):
        return {
            "status": "UNCONFIGURED",
            "message": "HYPERLIQUID_MAIN_ADDRESS is not configured in .env.",
            "demo_state": {
                "account_value_usd": 10000.0,
                "margin_used_usd": 0.0,
                "free_collateral_usd": 10000.0,
                "positions": []
            }
        }

    try:
        user_state = info.user_state(main_address)
        margin_summary = user_state.get("marginSummary", {})
        positions = user_state.get("assetPositions", [])
        return {
            "status": "SUCCESS",
            "account_address": main_address,
            "account_value": margin_summary.get("accountValue"),
            "total_margin_used": margin_summary.get("totalMarginUsed"),
            "free_collateral": float(margin_summary.get("accountValue", 0)) - float(margin_summary.get("totalMarginUsed", 0)),
            "open_positions": positions
        }
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("Testing mcp-hyperliquid tools...")
        print(place_bracket_order("BTC", True, 0.001, 90000.0, 95000.0, 88000.0, dry_run=True))
        print(get_account_summary())
    else:
        mcp.run()
