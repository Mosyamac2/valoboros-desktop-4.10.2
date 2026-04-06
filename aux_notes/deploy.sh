#!/bin/bash
# Valoboros deployment — run this on the remote server after git pull
# Usage: bash aux_notes/deploy.sh

set -e

PROXY="http://127.0.0.1:10809"

echo "=== Valoboros Deploy ==="

# 1. Create .env if missing (keys set later via web UI)
test -f .env || (cp .env.example .env && echo "[1] Created .env from template")
test -f .env && echo "[1] .env exists"

# 2. Set proxy for build (npm install, pip install need internet)
export HTTP_PROXY=$PROXY
export HTTPS_PROXY=$PROXY
export NO_PROXY=localhost,127.0.0.1
echo "[2] Proxy: $PROXY"

# 3. Build with proxy passed as build args
echo "[3] Building Docker image..."
sudo docker compose build \
    --build-arg HTTP_PROXY=$PROXY \
    --build-arg HTTPS_PROXY=$PROXY \
    --build-arg NO_PROXY=localhost,127.0.0.1

# 4. Start container (proxy passed via environment for runtime LLM calls)
echo "[4] Starting container..."
sudo docker compose up -d

# 5. Wait for startup
echo "[5] Waiting for server..."
sleep 5
sudo docker compose logs --tail=5

echo ""
echo "=== Ready ==="
echo "Open in browser: http://178.154.198.151:8765"
echo "Set OPENROUTER_API_KEY in Settings tab on first visit."
echo "Set ANTHROPIC_API_KEY for Claude Code evolution (recommended)."
echo ""
echo "Commands:"
echo "  sudo docker compose logs -f       # live logs"
echo "  sudo docker compose down          # stop"
echo "  sudo docker compose up -d         # start"
echo "  sudo docker compose restart       # restart"
