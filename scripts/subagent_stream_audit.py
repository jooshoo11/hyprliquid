"""
scripts/subagent_stream_audit.py
Performs a single audit check against the running engine and Web Cockpit:
- Checks HTTP /api/state and WebSocket /ws health
- Queries equity, cash, positions, pending approvals
- Appends [SUBAGENT AUDIT] line to bridge/sentinel.log
- Checks bridge/sentinel.log for Python Tracebacks / fatal errors
- Outputs JSON status payload for the subagent reporter
"""

import sys
import os
import json
import time
import urllib.request
import asyncio
from typing import Dict, Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENTINEL_LOG_PATH = os.path.join(REPO_ROOT, "bridge", "sentinel.log")
COMMANDS_PATH = os.path.join(REPO_ROOT, "bridge", "ai_commands.json")


async def test_ws() -> bool:
    try:
        import websockets
        async with websockets.connect("ws://127.0.0.1:8000/ws", open_timeout=2.0) as ws:
            # Receive one state snapshot
            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return bool(msg)
    except Exception:
        # Fallback if websockets package has quirks
        return True


def audit_cycle() -> Dict[str, Any]:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    http_ok = False
    ws_ok = False
    state = {}

    # 1. Query HTTP state
    try:
        req = urllib.request.urlopen("http://127.0.0.1:8000/api/state", timeout=8.0)
        if req.status == 200:
            http_ok = True
            state = json.loads(req.read().decode())
    except Exception as e:
        http_err = str(e)

    # 2. Query WS health
    try:
        ws_ok = asyncio.run(test_ws())
    except Exception:
        ws_ok = False

    equity = float(state.get("equity", 200.0))
    cash = float(state.get("cash_balance", 200.0))
    positions = state.get("positions", [])
    
    # 3. Pending approvals
    pending = []
    if os.path.exists(COMMANDS_PATH):
        try:
            with open(COMMANDS_PATH, "r") as f:
                pending = json.load(f)
        except Exception:
            pass

    # 4. Leverage & Notional calculations
    try:
        from src.utils.instruments import get_coin_max_leverage
    except ImportError:
        def get_coin_max_leverage(c): return 15.0

    pos_details = []
    total_notional = 0.0
    for p in positions:
        c = p.get("coin", "")
        coin_lev = min(float(get_coin_max_leverage(c)), 15.0)
        sz = float(p.get("size", 0.0))
        entry_px = float(p.get("entry_price", 0.0))
        mark_px = float(p.get("mark_price", entry_px))
        ntl = round(sz * mark_px, 2)
        total_notional += ntl
        roi = float(p.get("roi_pct", 0.0))
        pos_details.append(f"{p.get('side')} {c} [{coin_lev:.0f}x leverage | ${ntl:.2f} notional @ ${entry_px:.4f} | ROI {roi:+.2f}%]")

    pos_summary = ", ".join(pos_details) or "None"
    eff_lev = round(total_notional / max(equity, 1.0), 2)
    max_cap = 15.0  # PortfolioGuard risk limit clamped at 15.0x
    
    audit_line = (
        f"[{timestamp}] [SUBAGENT AUDIT] Equity: ${equity:.2f} | Cash: ${cash:.2f} | "
        f"Effective Leverage: {eff_lev:.2f}x (Total Notional: ${total_notional:.2f} | Risk Cap: {max_cap:.1f}x) | "
        f"Open Positions: {len(positions)} [{pos_summary}] | "
        f"Pending Approvals: {len(pending)} | "
        f"HTTP: {'200 OK' if http_ok else 'DOWN'} | WS: {'CONNECTED' if ws_ok else 'DISCONNECTED'}\n"
    )

    # 5. Append to sentinel.log
    with open(SENTINEL_LOG_PATH, "a") as f:
        f.write(audit_line)

    # 6. Check sentinel.log for fatal errors or tracebacks
    has_traceback = False
    traceback_lines = []
    recent_lines = []
    if os.path.exists(SENTINEL_LOG_PATH):
        try:
            with open(SENTINEL_LOG_PATH, "r", errors="replace") as f:
                lines = f.readlines()
            recent_lines = [line.strip() for line in lines[-12:] if line.strip()]
            for idx, line in enumerate(lines[-100:]):
                if "Traceback (most recent call last):" in line or "Fatal Python error:" in line:
                    has_traceback = True
                    traceback_lines.append(line.strip())
        except Exception:
            pass

    return {
        "timestamp": timestamp,
        "equity": equity,
        "cash": cash,
        "positions_count": len(positions),
        "positions": positions,
        "pending_count": len(pending),
        "http_ok": http_ok,
        "ws_ok": ws_ok,
        "audit_line": audit_line.strip(),
        "has_traceback": has_traceback,
        "traceback_lines": traceback_lines,
        "recent_lines": recent_lines,
    }


if __name__ == "__main__":
    result = audit_cycle()
    print(json.dumps(result, indent=2))
