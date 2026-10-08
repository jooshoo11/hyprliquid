#!/usr/bin/env bash
# ==============================================================================
# Hyperliquid WezTerm Cockpit Launcher
# Starts the institutional terminal trading dashboard in WezTerm
# ==============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# 1. Load environment variables if .env exists
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# 2. Locate WezTerm binary
WEZTERM_BIN=""
if command -v wezterm >/dev/null 2>&1; then
    WEZTERM_BIN="$(command -v wezterm)"
elif [ -x "$HOME/.local/bin/wezterm" ]; then
    WEZTERM_BIN="$HOME/.local/bin/wezterm"
elif [ -x "/usr/bin/wezterm" ]; then
    WEZTERM_BIN="/usr/bin/wezterm"
elif [ -x "/usr/local/bin/wezterm" ]; then
    WEZTERM_BIN="/usr/local/bin/wezterm"
fi

CONFIG_FILE="$PROJECT_DIR/wezterm.lua"

# 3. Check for graphical session (Wayland or X11)
HAS_DISPLAY=0
if [ -n "$WAYLAND_DISPLAY" ] || [ -n "$DISPLAY" ]; then
    HAS_DISPLAY=1
fi

echo "=================================================================="
echo "          HYPERLIQUID WEZTERM AUTONOMOUS COCKPIT                  "
echo "=================================================================="

if [ -n "$WEZTERM_BIN" ] && [ "$HAS_DISPLAY" -eq 1 ]; then
    echo "🖥️  Launching dedicated WezTerm Cockpit window..."
    echo "   Binary: $WEZTERM_BIN"
    echo "   Config: $CONFIG_FILE"
    exec "$WEZTERM_BIN" --config-file "$CONFIG_FILE" start --cwd "$PROJECT_DIR"
else
    echo "ℹ️  WezTerm GUI session not detected or headless environment."
    echo "📟 Launching interactive Terminal Cockpit in current terminal..."
    exec "$PROJECT_DIR/.venv/bin/python" -m src.tui.app
fi
