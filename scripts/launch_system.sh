#!/usr/bin/env bash
# ==============================================================================
# Hyperliquid Autonomous Trading System & Cockpit Launcher
# Launches:
#   1. Autonomous Trading Engine Daemon (Nautilus Trader + 4 Strategies)
#   2. Groq Real-Time Risk Sentinel Daemon
#   3. Desktop Browser Cockpit Dashboard (App Window Mode)
# ==============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# 1. Load environment variables if .env exists
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

mkdir -p "$PROJECT_DIR/logs"

# 2. Check and start the Main Trading Engine Daemon if not already running
ENGINE_PID=$(pgrep -f "run.py.*--port.*8000" || true)
if [ -n "$ENGINE_PID" ]; then
    echo "✅ Trading Engine is already active (PID: $ENGINE_PID)"
else
    echo "🚀 Starting Hyperliquid Trading Engine in background..."
    nohup "$PROJECT_DIR/.venv/bin/python" -u run.py \
        --top-n 50 \
        --port 8000 \
        --watchdog-interval 10 \
        --prospector-interval 900 \
        > "$PROJECT_DIR/logs/engine_daemon.log" 2>&1 &
    NEW_ENGINE_PID=$!
    disown $NEW_ENGINE_PID 2>/dev/null || true
    echo "Engine daemon launched (PID: $NEW_ENGINE_PID)"
fi

# 3. Check and start Groq Real-Time Risk Sentinel if not already running
SENTINEL_PID=$(pgrep -f "groq_sentinel.py" || true)
if [ -n "$SENTINEL_PID" ]; then
    echo "✅ Groq Sentinel is already active (PID: $SENTINEL_PID)"
else
    echo "⚡ Starting Groq Real-Time Risk Sentinel..."
    nohup "$PROJECT_DIR/.venv/bin/python" -u src/monitoring/groq_sentinel.py \
        --interval 20 \
        > "$PROJECT_DIR/logs/groq_sentinel.log" 2>&1 &
    NEW_SENTINEL_PID=$!
    disown $NEW_SENTINEL_PID 2>/dev/null || true
    echo "Groq Sentinel launched (PID: $NEW_SENTINEL_PID)"
fi

# 4. Wait for Web API server to become ready (up to 12 seconds)
echo "⏳ Waiting for Web Cockpit API at http://localhost:8000..."
MAX_WAIT=12
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if curl -s -f http://localhost:8000/api/state > /dev/null 2>&1; then
        echo "🌐 Web API is online and responding."
        break
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done

# 5. Send Desktop Notification
if command -v notify-send > /dev/null 2>&1; then
    notify-send \
        -i "$PROJECT_DIR/assets/hyperliquid.png" \
        "Hyperliquid Cockpit" \
        "Engine & Groq Sentinel are active. Launching trading cockpit..." \
        2>/dev/null || true
fi

# 6. Launch Web Dashboard in App Mode
URL="http://localhost:8000"
echo "🖥️ Opening Trading Cockpit: $URL"

if command -v google-chrome > /dev/null 2>&1; then
    # App mode opens a clean, distraction-free desktop window without browser URL bar
    google-chrome --app="$URL" > /dev/null 2>&1 &
elif command -v xdg-open > /dev/null 2>&1; then
    xdg-open "$URL" > /dev/null 2>&1 &
else
    echo "Please open $URL in your browser."
fi

exit 0
