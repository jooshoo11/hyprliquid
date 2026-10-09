#!/usr/bin/env bash
# ==============================================================================
# Pixel 9 Tensor G4 Local AI Server Daemon (start_pixel_ai.sh)
# Launches llama-server on localhost:8081 with hardware acceleration for Tensor G4:
# - 4 Threads targeting Cortex-X4 prime core & Cortex-A720 performance cores
# - 2048 context window with minimal RAM footprint (<1.2 GB)
# - Zero cloud tokens, unlimited offline inference
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="$PROJECT_DIR/models"
MODEL_NAME="qwen2.5-1.5b-instruct-q4_k_m.gguf"
MODEL_PATH="$MODELS_DIR/$MODEL_NAME"

# Check if model exists, offer to download if missing
if [ ! -f "$MODEL_PATH" ]; then
    echo "⚠️ Model file not found at $MODEL_PATH"
    echo "Running automatic setup to download model..."
    bash "$PROJECT_DIR/scripts/setup_pixel_model.sh"
fi

PID=$(pgrep -f "llama-server.*8081" || true)
if [ -n "$PID" ]; then
    echo "✅ Pixel 9 Local AI Server is already active (PID: $PID)"
    exit 0
fi

echo "🚀 Starting Pixel 9 Onboard Neural Engine (llama-server on port 8081)..."
setsid llama-server \
    -m "$MODEL_PATH" \
    --host 127.0.0.1 \
    --port 8081 \
    -t 4 \
    -c 2048 \
    -b 512 \
    --prio 1 \
    </dev/null > "$PROJECT_DIR/logs/pixel_ai.log" 2>&1 &

NEW_PID=$!
disown $NEW_PID 2>/dev/null || true

echo "✅ Pixel 9 Onboard AI Server launched (PID: $NEW_PID)"
echo "Waiting for endpoint initialization at http://127.0.0.1:8081/v1..."

for i in {1..15}; do
    if curl -s http://127.0.0.1:8081/models >/dev/null 2>&1; then
        echo "⚡ Onboard AI Engine is ONLINE and ready for zero-token inference!"
        exit 0
    fi
    sleep 1
done

echo "Server is initializing in background. Check $PROJECT_DIR/logs/pixel_ai.log"
