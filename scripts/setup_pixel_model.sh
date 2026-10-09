#!/usr/bin/env bash
# ==============================================================================
# Pixel 9 On-Device AI Model Setup Script
# Downloads an ultra-efficient 4-bit quantized GGUF model optimized for Tensor G4:
# Model: Qwen2.5-1.5B-Instruct-Q4_K_M.gguf (~986 MB)
# ==============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="$PROJECT_DIR/models"
mkdir -p "$MODELS_DIR"

MODEL_NAME="qwen2.5-1.5b-instruct-q4_k_m.gguf"
MODEL_PATH="$MODELS_DIR/$MODEL_NAME"
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"

if [ -f "$MODEL_PATH" ]; then
    echo "✅ Local Pixel 9 AI model already exists: $MODEL_PATH"
    ls -lh "$MODEL_PATH"
    exit 0
fi

echo "📥 Downloading Qwen2.5-1.5B-Instruct (Q4_K_M) for Pixel 9 Tensor G4 (~986MB)..."
curl -L -C - "$MODEL_URL" -o "$MODEL_PATH"

echo "✅ Download complete! Model stored at: $MODEL_PATH"
