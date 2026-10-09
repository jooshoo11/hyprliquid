#!/usr/bin/env bash
# ==============================================================================
# Stop Hyprliquid Cockpit Daemon & Release Wake-Lock (stop_cockpit_daemon.sh)
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$PROJECT_DIR/.cockpit.pid"

echo "🛑 Stopping Hyprliquid Cockpit Daemon..."

# Kill running python process
pkill -f "run_cockpit.py" 2>/dev/null || true

# Kill supervisor bash subshell if PID file exists
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$PID" ]; then
        kill -9 "$PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi

# Release Wake Lock if no other termux services require it
if command -v termux-wake-unlock >/dev/null 2>&1; then
    termux-wake-unlock
    echo "🔓 Android Wake Lock released."
fi

echo "✅ Hyprliquid Cockpit Daemon stopped cleanly."
