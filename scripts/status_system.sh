#!/usr/bin/env bash
# ==============================================================================
# Hyperliquid System Status Checker
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=================================================================="
echo "          HYPERLIQUID AUTONOMOUS SYSTEM STATUS                    "
echo "=================================================================="

ENGINE_PID=$(pgrep -f "run.py.*--port.*8000" || true)
if [ -n "$ENGINE_PID" ]; then
    echo "🟢 Trading Engine: RUNNING (PID: $ENGINE_PID)"
else
    echo "🔴 Trading Engine: STOPPED"
fi

SENTINEL_PID=$(pgrep -f "groq_sentinel.py" || true)
if [ -n "$SENTINEL_PID" ]; then
    echo "🟢 Groq Sentinel:  RUNNING (PID: $SENTINEL_PID)"
else
    echo "⚪ Groq Sentinel:  STOPPED"
fi

echo "------------------------------------------------------------------"

if curl -s -f http://localhost:8000/api/state > /dev/null 2>&1; then
    curl -s http://localhost:8000/api/state | "$PROJECT_DIR/.venv/bin/python" -c '
import sys, json
try:
    s = json.load(sys.stdin)
    equity = s.get("equity", 0)
    cash = s.get("cash_balance", 0)
    unrealized = s.get("net_unrealized", 0)
    realized = s.get("net_realized_pnl", 0)
    regime = s.get("market_regime", {}).get("regime", "N/A")
    positions = s.get("positions", [])
    orders = s.get("open_orders", [])

    print(f"💰 Equity:             ${equity:.2f}")
    print(f"💵 Cash Balance:       ${cash:.2f}")
    print(f"📊 Net Unrealized PnL: ${unrealized:+.2f}")
    print(f"📈 Realized PnL:       ${realized:+.2f}")
    print(f"🌐 Macro Regime:       {regime}")
    print(f"🎯 Open Positions:     {len(positions)}")
    for p in positions:
        coin = p.get("coin", "UNKNOWN")
        side = p.get("side", "")
        strat = p.get("strategy", "")
        roi = p.get("roi_pct", 0)
        pnl = p.get("unrealized_pnl", 0)
        print(f"   • {coin}: {side} ({strat}) | ROI: {roi:+.2f}% | PnL: ${pnl:+.2f}")
    print(f"📋 Open Orders:        {len(orders)}")
except Exception as e:
    print(f"Error parsing state: {e}")
'
elif [ -f "$PROJECT_DIR/bridge/paper_state.json" ]; then
    "$PROJECT_DIR/.venv/bin/python" -c '
import json, os
try:
    with open("bridge/paper_state.json") as f:
        s = json.load(f)
    print(f"💰 Equity:             ${s.get(\"equity\", 0):.2f}")
    print(f"💵 Cash Balance:       ${s.get(\"cash_balance\", 0):.2f}")
    print(f"📈 Realized PnL:       ${s.get(\"realized_pnl\", 0):+.2f}")
    pos = s.get("positions", [])
    print(f"🎯 Open Positions:     {len(pos)}")
    for p in pos:
        print(f"   • {p.get(\"coin\", \"?\")}: {p.get(\"side\", \"\")} | ROI: {p.get(\"roi_pct\", 0):+.2f}% | PnL: ${p.get(\"unrealized_pnl\", 0):+.2f}")
except Exception as e:
    print(f"Error reading bridge state: {e}")
'
else
    echo "⚠️ Trading Engine state not available."
fi

echo "=================================================================="
