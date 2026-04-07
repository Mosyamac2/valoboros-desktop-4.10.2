#!/bin/bash
# Push Valoboros evolution commits from /repo to a SEPARATE GitHub repo
# Usage: ./scripts/push-evolution.sh [--setup]
#
# First run:  ./scripts/push-evolution.sh --setup
#   - Creates a new GitHub repo (e.g. Mosyamac2/valoboros-evolved)
#   - Configures it as the push target
# After that: ./scripts/push-evolution.sh

set -euo pipefail

CONTAINER="valoboros"
REMOTE_NAME="evolution"
GITHUB_PAT="${VALOBOROS_GITHUB_PAT:-}"
GITHUB_USER="${VALOBOROS_GITHUB_USER:-}"
GITHUB_REPO="${VALOBOROS_GITHUB_REPO:-valoboros-evolved}"

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
    echo "=== Setting up a SEPARATE GitHub repo for Valoboros evolution ==="
    echo ""
    echo "This keeps Mosyamac2/valoboros-desktop-4.10.2 intact."
    echo "Evolution commits push to a new repo instead."
    echo ""

    # Gather info
    if [ -z "$GITHUB_USER" ]; then
        read -rp "GitHub username [Mosyamac2]: " GITHUB_USER
        GITHUB_USER="${GITHUB_USER:-Mosyamac2}"
    fi

    if [ -z "$GITHUB_PAT" ]; then
        read -rsp "GitHub PAT (ghp_...): " GITHUB_PAT
        echo ""
    fi

    if [ -z "$GITHUB_PAT" ]; then
        fail "No PAT provided"
    fi

    read -rp "New repo name [valoboros-evolved]: " input_repo
    GITHUB_REPO="${input_repo:-valoboros-evolved}"

    echo ""
    echo "Will create: $GITHUB_USER/$GITHUB_REPO"
    echo ""

    # Create the repo on GitHub via API
    echo "Creating repo on GitHub..."
    create_result=$(curl -s -w "\n%{http_code}" \
        -X POST \
        -H "Authorization: token $GITHUB_PAT" \
        -H "Accept: application/vnd.github+json" \
        https://api.github.com/user/repos \
        -d "{
            \"name\": \"$GITHUB_REPO\",
            \"description\": \"Valoboros self-evolved validation platform (auto-pushed from Docker /repo)\",
            \"private\": false
        }" 2>&1)

    http_code=$(echo "$create_result" | tail -1)
    body=$(echo "$create_result" | sed '$d')

    if [ "$http_code" = "201" ]; then
        ok "Repo created: https://github.com/$GITHUB_USER/$GITHUB_REPO"
    elif [ "$http_code" = "422" ]; then
        # Repo already exists
        warn "Repo $GITHUB_USER/$GITHUB_REPO already exists (OK, will use it)"
    else
        echo "  API response ($http_code): $body"
        fail "Failed to create repo"
    fi

    # Configure remote inside the container's /repo
    GITHUB_URL="https://${GITHUB_USER}:${GITHUB_PAT}@github.com/${GITHUB_USER}/${GITHUB_REPO}.git"

    existing=$(docker exec "$CONTAINER" git -C /repo remote get-url "$REMOTE_NAME" 2>/dev/null || echo "")
    if [ -n "$existing" ]; then
        docker exec "$CONTAINER" git -C /repo remote set-url "$REMOTE_NAME" "$GITHUB_URL"
    else
        docker exec "$CONTAINER" git -C /repo remote add "$REMOTE_NAME" "$GITHUB_URL"
    fi

    ok "Remote '$REMOTE_NAME' → $GITHUB_USER/$GITHUB_REPO"

    # Initial push
    echo ""
    echo "=== Initial push ==="
    branch=$(docker exec "$CONTAINER" git -C /repo rev-parse --abbrev-ref HEAD 2>/dev/null || echo "ouroboros")
    docker exec "$CONTAINER" git -C /repo push -u "$REMOTE_NAME" "$branch":main --tags --force 2>&1 && \
        ok "Initial push complete" || \
        fail "Initial push failed"

    echo ""
    ok "Setup complete!"
    echo ""
    echo "  Source repo (untouched): https://github.com/$GITHUB_USER/valoboros-desktop-4.10.2"
    echo "  Evolution repo:          https://github.com/$GITHUB_USER/$GITHUB_REPO"
    echo ""
    echo "Run ./scripts/push-evolution.sh anytime to sync new evolution commits."
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

# Push
echo "=== Pushing $branch → evolution repo (main) ==="
docker exec "$CONTAINER" git -C /repo push "$REMOTE_NAME" "$branch":main --tags 2>&1 && \
    ok "Pushed successfully" || \
    fail "Push failed"

echo ""
remote_url=$(docker exec "$CONTAINER" git -C /repo remote get-url "$REMOTE_NAME" 2>/dev/null)
display_url=$(echo "$remote_url" | sed -E 's|https://[^@]+@|https://|')
ok "Pushed to $display_url"
