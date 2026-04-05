#!/bin/bash
# Valoboros deployment — run this on the remote server after git pull
# Usage: bash aux_notes/deploy.sh

set -e

echo "=== Valoboros Deploy ==="

# 1. Create .env if missing (keys set later via web UI)
test -f .env || (cp .env.example .env && echo "[1] Created .env from template")
test -f .env && echo "[1] .env exists"

# 2. Build and start
echo "[2] Building Docker image and starting container..."
docker compose up -d --build

# 3. Wait for startup
echo "[3] Waiting for server..."
sleep 5
docker compose logs --tail=5

echo ""
echo "=== Ready ==="
echo "Open in browser: http://178.154.198.151:8765"
echo "Set OPENROUTER_API_KEY in Settings tab on first visit."
echo ""
echo "Commands:"
echo "  docker compose logs -f       # live logs"
echo "  docker compose down          # stop"
echo "  docker compose up -d         # start"
echo "  docker compose restart       # restart"
