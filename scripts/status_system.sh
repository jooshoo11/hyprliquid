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
else
    echo "⚠️ Web Cockpit API not responding at http://localhost:8000"
fi

echo "=================================================================="
