#!/usr/bin/env bash
# ==============================================================================
# Pixel 9 Always-On Autonomous Cockpit Daemon (start_cockpit_daemon.sh)
# Keeps the Hyprliquid trading engine & web cockpit running 24/7 in the background:
# 1. Acquires Android CPU Wake-Lock (prevents Android Doze from sleeping CPU).
# 2. Runs the process fully detached with auto-restart supervisor.
# 3. Survives closing Chrome, closing Termux app, and phone screen lock.
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
PID_FILE="$PROJECT_DIR/.cockpit.pid"
mkdir -p "$LOG_DIR"

# 1. Acquire Android Wake Lock so CPU cores stay active when screen locks
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock
    echo "🔒 Android Wake Lock acquired (CPU will remain awake when phone is locked)"
fi

# 2. Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "✅ Cockpit daemon is already running (PID: $OLD_PID)"
        echo "🌐 URL: http://localhost:8000"
        exit 0
    fi
fi

# 3. Launch Supervisor Loop in Detached Background Process (Session-Independent)
echo "🚀 Launching Always-On Hyprliquid Daemon in background..."

setsid bash -c '
    PROJECT_DIR="'"$PROJECT_DIR"'"
    PID_FILE="'"$PID_FILE"'"
    LOG_FILE="'"$LOG_DIR"'/cockpit_daemon.log"

    echo "$$" > "$PID_FILE"

    while true; do
        echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Starting Hyprliquid Cockpit..." >> "$LOG_FILE"
        python3 -u "$PROJECT_DIR/run_cockpit.py" >> "$LOG_FILE" 2>&1
        EXIT_CODE=$?
        echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Cockpit exited with code $EXIT_CODE. Restarting in 3 seconds..." >> "$LOG_FILE"
        sleep 3
    done
' </dev/null >/dev/null 2>&1 &

DAEMON_PID=$!
disown $DAEMON_PID 2>/dev/null || true

# Wait briefly to confirm process startup
sleep 2

if [ -f "$PID_FILE" ]; then
    ACTIVE_PID=$(cat "$PID_FILE")
    echo "✅ Always-On Cockpit Daemon active! (PID: $ACTIVE_PID)"
else
    echo "✅ Always-On Cockpit Daemon launched (PID: $DAEMON_PID)"
fi

echo "🌐 Local Cockpit: http://localhost:8000"
echo "📜 Live Log: tail -f $LOG_DIR/cockpit_daemon.log"
echo "🛑 To stop anytime: bash scripts/stop_cockpit_daemon.sh"
