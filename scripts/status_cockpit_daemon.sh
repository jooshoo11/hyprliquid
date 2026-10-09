#!/usr/bin/env bash
# ==============================================================================
# Check Hyprliquid Cockpit Daemon Status (status_cockpit_daemon.sh)
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$PROJECT_DIR/.cockpit.pid"

RUNNING_PYTHON=$(pgrep -f "run_cockpit.py" || true)

if [ -n "$RUNNING_PYTHON" ]; then
    echo "🟢 STATUS: ONLINE & ACTIVE"
    echo "• Cockpit Python PID: $RUNNING_PYTHON"
    if [ -f "$PID_FILE" ]; then
        echo "• Supervisor PID:     $(cat "$PID_FILE")"
    fi
    echo "• Local Cockpit URL:  http://localhost:8000"
    
    # Check if API responds
    if curl -s -f http://localhost:8000/api/state >/dev/null 2>&1; then
        echo "• API Health:         ONLINE (HTTP 200 OK)"
    else
        echo "• API Health:         STARTING UP..."
    fi
    echo "• Recent Log Entries:"
    tail -n 5 "$PROJECT_DIR/logs/cockpit_daemon.log" 2>/dev/null || echo "  (no log file yet)"
else
    echo "🔴 STATUS: STOPPED"
    echo "To start the daemon, run: bash scripts/start_cockpit_daemon.sh"
fi
