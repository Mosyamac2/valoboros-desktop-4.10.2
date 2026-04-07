#!/bin/bash
# Push Valoboros evolution commits from /repo to GitHub
# Usage: ./scripts/push-evolution.sh [--setup]
#
# First run:  ./scripts/push-evolution.sh --setup
# After that: ./scripts/push-evolution.sh

set -euo pipefail

CONTAINER="valoboros"
REMOTE_NAME="github"
# Read from env or prompt
GITHUB_URL="${VALOBOROS_GITHUB_URL:-}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
warn() { echo -e "${YELLOW}!${NC} $1"; }

# Check container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    fail "Container '$CONTAINER' is not running"
fi

# ── Setup mode ────────────────────────────────────────────────────────
if [[ "${1:-}" == "--setup" ]]; then
    echo "=== Setting up GitHub remote in /repo ==="
    echo ""

    if [ -z "$GITHUB_URL" ]; then
        echo "Enter your GitHub repo URL with PAT:"
        echo "  Format: https://USERNAME:PAT@github.com/USERNAME/REPO.git"
        echo "  Example: https://Mosyamac2:ghp_xxx@github.com/Mosyamac2/valoboros-desktop-4.10.2.git"
        echo ""
        read -rp "URL: " GITHUB_URL
    fi

    if [ -z "$GITHUB_URL" ]; then
        fail "No URL provided"
    fi

    # Check if remote exists
    existing=$(docker exec "$CONTAINER" git -C /repo remote get-url "$REMOTE_NAME" 2>/dev/null || echo "")
    if [ -n "$existing" ]; then
        warn "Remote '$REMOTE_NAME' already exists, updating URL..."
        docker exec "$CONTAINER" git -C /repo remote set-url "$REMOTE_NAME" "$GITHUB_URL"
    else
        docker exec "$CONTAINER" git -C /repo remote add "$REMOTE_NAME" "$GITHUB_URL"
    fi

    ok "Remote '$REMOTE_NAME' configured"
    echo ""
    echo "Now run without --setup to push:"
    echo "  ./scripts/push-evolution.sh"
    exit 0
fi

# ── Push mode ─────────────────────────────────────────────────────────

# Verify remote exists
if ! docker exec "$CONTAINER" git -C /repo remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    fail "Remote '$REMOTE_NAME' not configured. Run: ./scripts/push-evolution.sh --setup"
fi

echo "=== Valoboros /repo status ==="

# Show current branch and recent commits
branch=$(docker exec "$CONTAINER" git -C /repo rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
echo "Branch: $branch"
echo ""
echo "Recent commits:"
docker exec "$CONTAINER" git -C /repo log --oneline -10
echo ""

# Show tags
echo "Tags:"
docker exec "$CONTAINER" git -C /repo tag --sort=-version:refname | head -10
echo ""

# Show what will be pushed
echo "=== Pushing $branch → GitHub master ==="
docker exec "$CONTAINER" git -C /repo push "$REMOTE_NAME" "$branch":master --tags 2>&1 && \
    ok "Pushed successfully" || \
    fail "Push failed"

echo ""
echo "=== Verifying ==="
remote_url=$(docker exec "$CONTAINER" git -C /repo remote get-url "$REMOTE_NAME" 2>/dev/null)
# Strip credentials for display
display_url=$(echo "$remote_url" | sed -E 's|https://[^@]+@|https://|')
ok "Pushed to $display_url"
