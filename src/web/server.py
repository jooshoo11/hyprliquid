"""
Hyperliquid AI Trading Cockpit - Modern Web Server (server.py)
High-performance FastAPI & WebSocket backend providing:
- Real-time streaming WebSocket (/ws) for live equity, active trades, AI prospect rankings, and risk logs.
- TradingView Lightweight Charts API (/api/klines) for interactive candlestick and volume charting.
- 1-Click execution API (/api/trades/close, /api/trades/close_all) bridging to Nautilus TradingNode.
- Orderbook depth API (/api/l2) for live visual depth inspection.
"""

import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import os
import time
import json
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.scanner.mcp_client import HyperliquidInfoClient

app = FastAPI(title="Hyperliquid AI Trading Cockpit", version="2.0.0")

info_client = HyperliquidInfoClient()

ACTIVE_TRADES_PATH = os.path.join(REPO_ROOT, "bridge", "active_trades.json")
PROSPECTS_PATH = os.path.join(REPO_ROOT, "bridge", "prospects.json")
AI_COMMANDS_PATH = os.path.join(REPO_ROOT, "bridge", "ai_commands.json")
PAPER_STATE_PATH = os.path.join(REPO_ROOT, "bridge", "paper_state.json")
SENTINEL_LOG_PATH = os.path.join(REPO_ROOT, "bridge", "sentinel.log")


class CloseTradeRequest(BaseModel):
    coin: str
    reason: Optional[str] = "Manual Web Cockpit Close"


def load_json_file(path: str, default: Any = None) -> Any:
    """Safe read of JSON files with fallback."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def get_live_state() -> Dict[str, Any]:
    """Assemble complete live portfolio, trade, prospect, and audit state."""
    active_trades = load_json_file(ACTIVE_TRADES_PATH, {"equity": 100.0, "cash_balance": 100.0, "positions": []})
    prospects_data = load_json_file(PROSPECTS_PATH, {"prospects": {}})
    paper_state = load_json_file(PAPER_STATE_PATH, {})

    positions = active_trades.get("positions", [])
    equity = float(active_trades.get("equity", 100.0))
    cash_balance = float(active_trades.get("cash_balance", 100.0))

    # Dynamically compute real-time mark-to-market P&L from live prices
    net_unrealized = 0.0
    notional_exposure = 0.0
    if positions:
        try:
            meta, asset_ctxs = info_client.get_meta_and_asset_ctxs()
            universe = meta.get("universe", [])
            px_map = {}
            for u, ctx in zip(universe, asset_ctxs):
                px_map[u.get("name")] = float(ctx.get("midPx") or ctx.get("markPx") or ctx.get("oraclePx", 0.0))

            for p in positions:
                coin = p.get("coin", "").upper()
                cur_px = px_map.get(coin, 0.0)
                entry_px = float(p.get("entry_price", 0.0))
                qty = float(p.get("size", 0.0))
                side = p.get("side", "LONG").upper()
                if cur_px > 0 and entry_px > 0 and qty > 0:
                    pnl = (cur_px - entry_px) * qty * (1.0 if side == "LONG" else -1.0)
                    p["unrealized_pnl"] = round(pnl, 2)
                    p["mark_price"] = cur_px
                    net_unrealized += pnl
                    notional_exposure += (cur_px * qty)
                else:
                    net_unrealized += float(p.get("unrealized_pnl", 0.0))
                    notional_exposure += (entry_px * qty)
        except Exception:
            net_unrealized = sum(float(p.get("unrealized_pnl", 0.0)) for p in positions)
            notional_exposure = sum(float(p.get("size", 0.0)) * float(p.get("entry_price", 0.0)) for p in positions)
    else:
        net_unrealized = 0.0
        notional_exposure = 0.0

    live_equity = round(cash_balance + net_unrealized, 2)
    pending_cmds = load_json_file(AI_COMMANDS_PATH, [])

    # Format prospects list
    raw_prospects = prospects_data.get("prospects") if (isinstance(prospects_data, dict) and "prospects" in prospects_data) else prospects_data
    prospects_list = []
    if isinstance(raw_prospects, dict):
        for coin, p_data in raw_prospects.items():
            if isinstance(p_data, dict):
                prospects_list.append({
                    "coin": coin,
                    "bias": p_data.get("bias", "NEUTRAL"),
                    "target_entry": p_data.get("target_entry", 0.0),
                    "rationale": p_data.get("reason") or p_data.get("rationale", ""),
                    "change_24h": p_data.get("change_24h", 0.0),
                    "volume_24h": p_data.get("volume_24h", 0.0),
                })
            elif isinstance(p_data, str):
                prospects_list.append({
                    "coin": coin,
                    "bias": p_data,
                    "target_entry": 0.0,
                    "rationale": "",
                    "change_24h": 0.0,
                    "volume_24h": 0.0,
                })

    return {
        "timestamp": time.time(),
        "equity": live_equity,
        "cash_balance": round(cash_balance, 2),
        "net_unrealized": round(net_unrealized, 2),
        "notional_exposure": round(notional_exposure, 2),
        "roi_pct": round((net_unrealized / cash_balance * 100.0) if cash_balance > 0 else 0.0, 2),
        "positions": positions,
        "prospects": prospects_list,
        "paper_state": paper_state,
        "pending_ai_commands": pending_cmds,
    }


@app.get("/api/state")
async def api_state():
    """Fetch current system state snapshot."""
    return get_live_state()


@app.get("/api/klines")
async def api_klines(coin: str = "BTC", interval: str = "1h", limit: int = 150):
    """
    Fetch historical candlestick bars formatted specifically for TradingView Lightweight Charts:
    [ { "time": 1700000000, "open": ..., "high": ..., "low": ..., "close": ..., "volume": ... } ]
    """
    try:
        coin_clean = coin.upper().split("-")[0].split(".")[0]
        candles = info_client.get_historical_klines(coin_clean, interval=interval)
        if not candles:
            return []

        formatted = []
        for c in candles[-limit:]:
            try:
                t_sec = int(int(c.get("t") or c.get("T") or 0) / 1000)
                formatted.append({
                    "time": t_sec,
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                    "volume": float(c.get("v", 0.0)),
                })
            except Exception:
                pass
        return formatted
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/l2")
async def api_l2(coin: str = "BTC"):
    """Fetch live L2 order book depth for a coin."""
    try:
        coin_clean = coin.upper().split("-")[0].split(".")[0]
        return info_client.get_l2_snapshot(coin_clean)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trades/close")
async def api_close_trade(req: CloseTradeRequest):
    """
    Publish an emergency CLOSE_POSITION command to bridge/ai_commands.json.
    Nautilus TradingNode will execute this within 1 second.
    """
    coin_clean = req.coin.upper().split("-")[0].split(".")[0]
    cmds = load_json_file(AI_COMMANDS_PATH, [])
    if not isinstance(cmds, list):
        cmds = []

    cmds.append({
        "action": "CLOSE_POSITION",
        "coin": coin_clean,
        "reason": req.reason or "Manual Web Cockpit Close",
        "timestamp": time.time(),
    })

    try:
        tmp_path = f"{AI_COMMANDS_PATH}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(cmds, f, indent=2)
        os.replace(tmp_path, AI_COMMANDS_PATH)
        return {"status": "SUCCESS", "message": f"Market close submitted for {coin_clean}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit close: {e}")


@app.post("/api/trades/close_all")
async def api_close_all():
    """Emergency abort: close all currently active positions."""
    state = get_live_state()
    positions = state.get("positions", [])
    if not positions:
        return {"status": "SUCCESS", "message": "No active positions to close"}

    cmds = load_json_file(AI_COMMANDS_PATH, [])
    if not isinstance(cmds, list):
        cmds = []

    closed_coins = []
    for pos in positions:
        coin = pos.get("coin", "").upper()
        if coin:
            cmds.append({
                "action": "CLOSE_POSITION",
                "coin": coin,
                "reason": "Emergency Web Cockpit Close All",
                "timestamp": time.time(),
            })
            closed_coins.append(coin)

    try:
        tmp_path = f"{AI_COMMANDS_PATH}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(cmds, f, indent=2)
        os.replace(tmp_path, AI_COMMANDS_PATH)
        return {"status": "SUCCESS", "message": f"Close-all submitted for: {', '.join(closed_coins)}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit close all: {e}")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time streaming WebSocket broadcasting live portfolio state and trade metrics every 1 second.
    """
    await websocket.accept()
    try:
        while True:
            state = get_live_state()
            await websocket.send_json(state)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Serve the modern dark-mode trading cockpit Single Page Application."""
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Cockpit loading... index.html not found</h1>"


if __name__ == "__main__":
    import uvicorn
    import argparse
    parser = argparse.ArgumentParser(description="Hyperliquid AI Trading Web Cockpit")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host address to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to run web server on")
    args = parser.parse_args()

    print(f"🚀 Starting Hyperliquid AI Trading Cockpit on http://{args.host}:{args.port}")
    uvicorn.run("src.web.server:app", host=args.host, port=args.port, reload=False)
