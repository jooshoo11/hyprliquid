#!/usr/bin/env bash
# ==============================================================================
# Hyperliquid System Shutdown Script
# Gracefully stops:
#   1. Trading Engine Daemon (run.py)
#   2. Groq Risk Sentinel (groq_sentinel.py)
# ==============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "🛑 Stopping Hyperliquid Autonomous Trading daemons..."

# Stop Groq Sentinel
SENTINEL_PIDS=$(pgrep -f "groq_sentinel.py" || true)
if [ -n "$SENTINEL_PIDS" ]; then
    echo "Stopping Groq Sentinel (PID: $SENTINEL_PIDS)..."
    kill $SENTINEL_PIDS 2>/dev/null || true
fi

# Stop Main Engine
ENGINE_PIDS=$(pgrep -f "run.py.*--port.*8000" || true)
if [ -n "$ENGINE_PIDS" ]; then
    echo "Stopping Trading Engine (PID: $ENGINE_PIDS)..."
    kill -TERM $ENGINE_PIDS 2>/dev/null || true
    sleep 1
    # Force kill if still hanging
    REMAINING=$(pgrep -f "run.py.*--port.*8000" || true)
    if [ -n "$REMAINING" ]; then
        kill -9 $REMAINING 2>/dev/null || true
    fi
fi

if command -v notify-send > /dev/null 2>&1; then
    notify-send \
        -i "$PROJECT_DIR/assets/hyperliquid.png" \
        "Hyperliquid Cockpit" \
        "All trading engine and risk sentinel daemons stopped." \
        2>/dev/null || true
fi

echo "✅ All services stopped."
