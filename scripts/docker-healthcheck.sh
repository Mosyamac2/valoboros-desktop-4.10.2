#!/bin/bash
# Valoboros Docker health check & restart script
# Usage: ./scripts/docker-healthcheck.sh [--restart] [--rebuild]

set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER="valoboros"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }

DO_RESTART=false
DO_REBUILD=false
for arg in "$@"; do
    case "$arg" in
        --restart) DO_RESTART=true ;;
        --rebuild) DO_REBUILD=true ;;
        --help|-h)
            echo "Usage: $0 [--restart] [--rebuild]"
            echo "  --restart   docker compose down + up"
            echo "  --rebuild   docker compose down + build --no-cache + up"
            exit 0
            ;;
    esac
done

cd "$COMPOSE_DIR"

# ── Restart/Rebuild if requested ──────────────────────────────────────
if $DO_REBUILD; then
    echo "=== Rebuilding and restarting ==="
    docker compose down
    docker compose build --no-cache
    docker compose up -d
    echo "Waiting 10s for startup..."
    sleep 10
elif $DO_RESTART; then
    echo "=== Restarting ==="
    docker compose down
    docker compose up -d
    echo "Waiting 10s for startup..."
    sleep 10
fi

# ── Container status ──────────────────────────────────────────────────
echo ""
echo "=== Container Status ==="
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    ok "Container '$CONTAINER' is running"
else
    fail "Container '$CONTAINER' is NOT running"
    echo "  Run: docker compose up -d"
    exit 1
fi

# ── .env file check ──────────────────────────────────────────────────
echo ""
echo "=== .env File ==="
if [ -f .env ]; then
    ok ".env file exists"
    while IFS= read -r line; do
        # skip comments and empty lines
        [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
        key="${line%%=*}"
        val="${line#*=}"
        if [ -z "$val" ]; then
            warn "$key is empty in .env"
        else
            ok "$key is set (${val:0:10}...)"
        fi
    done < .env
else
    fail ".env file not found"
fi

# ── API keys inside container ─────────────────────────────────────────
echo ""
echo "=== API Keys (inside container) ==="
for key in OPENROUTER_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY; do
    val=$(docker exec "$CONTAINER" printenv "$key" 2>/dev/null || echo "")
    if [ -n "$val" ]; then
        ok "$key = ${val:0:12}..."
    else
        if [ "$key" = "OPENROUTER_API_KEY" ]; then
            fail "$key NOT SET (required)"
        elif [ "$key" = "ANTHROPIC_API_KEY" ]; then
            fail "$key NOT SET (required for Claude Code evolution)"
        else
            warn "$key not set (optional)"
        fi
    fi
done

# ── Proxy configuration ──────────────────────────────────────────────
echo ""
echo "=== Proxy (inside container) ==="
http_proxy=$(docker exec "$CONTAINER" printenv HTTP_PROXY 2>/dev/null || echo "")
https_proxy=$(docker exec "$CONTAINER" printenv HTTPS_PROXY 2>/dev/null || echo "")
no_proxy=$(docker exec "$CONTAINER" printenv NO_PROXY 2>/dev/null || echo "")

if [ -n "$https_proxy" ]; then
    echo "  HTTPS_PROXY=$https_proxy"
    if echo "$https_proxy" | grep -q "127.0.0.1"; then
        fail "HTTPS_PROXY uses 127.0.0.1 — unreachable from container! Use 172.17.0.1 or host IP"
    else
        ok "HTTPS_PROXY address looks correct"
    fi
elif [ -n "$http_proxy" ]; then
    echo "  HTTP_PROXY=$http_proxy"
    if echo "$http_proxy" | grep -q "127.0.0.1"; then
        fail "HTTP_PROXY uses 127.0.0.1 — unreachable from container! Use 172.17.0.1 or host IP"
    else
        ok "HTTP_PROXY address looks correct"
    fi
else
    warn "No proxy configured (OK if direct internet access)"
fi
[ -n "$no_proxy" ] && echo "  NO_PROXY=$no_proxy"

# ── DNS resolution ────────────────────────────────────────────────────
echo ""
echo "=== DNS Resolution ==="
for host in openrouter.ai api.anthropic.com api.openai.com; do
    result=$(docker exec "$CONTAINER" python3 -c "import socket; print(socket.gethostbyname('$host'))" 2>&1)
    if echo "$result" | grep -qE '^[0-9]+\.[0-9]+'; then
        ok "$host -> $result"
    else
        fail "$host -> DNS FAILED: $result"
    fi
done

# ── API connectivity ──────────────────────────────────────────────────
echo ""
echo "=== API Connectivity ==="

# OpenRouter
or_result=$(docker exec "$CONTAINER" python3 -c "
import urllib.request, os
proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY') or ''
if proxy:
    handler = urllib.request.ProxyHandler({'https': proxy, 'http': proxy})
    opener = urllib.request.build_opener(handler)
else:
    opener = urllib.request.build_opener()
try:
    r = opener.open('https://openrouter.ai/api/v1/models', timeout=15)
    print('OK ' + str(r.status))
except Exception as e:
    print('FAIL ' + str(e))
" 2>&1)
if echo "$or_result" | grep -q "^OK"; then
    ok "OpenRouter API: $or_result"
else
    fail "OpenRouter API: $or_result"
fi

# Anthropic
an_result=$(docker exec "$CONTAINER" python3 -c "
import urllib.request, os
proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY') or ''
if proxy:
    handler = urllib.request.ProxyHandler({'https': proxy, 'http': proxy})
    opener = urllib.request.build_opener(handler)
else:
    opener = urllib.request.build_opener()
try:
    r = opener.open('https://api.anthropic.com/', timeout=15)
    print('OK ' + str(r.status))
except urllib.error.HTTPError as e:
    print('OK ' + str(e.code) + ' (auth expected)')
except Exception as e:
    print('FAIL ' + str(e))
" 2>&1)
if echo "$an_result" | grep -q "^OK"; then
    ok "Anthropic API: $an_result"
else
    fail "Anthropic API: $an_result"
fi

# ── Claude Code CLI ───────────────────────────────────────────────────
echo ""
echo "=== Claude Code CLI ==="
claude_ver=$(docker exec "$CONTAINER" claude --version 2>&1 || echo "NOT FOUND")
if echo "$claude_ver" | grep -qiE '[0-9]+\.[0-9]+'; then
    ok "Claude CLI: $claude_ver"
else
    fail "Claude CLI: $claude_ver"
fi

# ── Valoboros web UI ──────────────────────────────────────────────────
echo ""
echo "=== Valoboros Web UI ==="
web_result=$(docker exec "$CONTAINER" python3 -c "
import urllib.request
try:
    r = urllib.request.urlopen('http://localhost:8765/', timeout=5)
    print('OK ' + str(r.status))
except Exception as e:
    print('FAIL ' + str(e))
" 2>&1)
if echo "$web_result" | grep -q "^OK"; then
    ok "Web UI: $web_result"
else
    fail "Web UI: $web_result"
fi

# ── Validation data ──────────────────────────────────────────────────
echo ""
echo "=== Validation Data ==="
bundle_count=$(docker exec "$CONTAINER" python3 -c "
from pathlib import Path
vdir = Path('/data/validations')
if vdir.exists():
    dirs = [d for d in vdir.iterdir() if d.is_dir()]
    print(len(dirs))
else:
    print(0)
" 2>&1)
ok "Validation bundles: $bundle_count"

inbox_count=$(docker exec "$CONTAINER" python3 -c "
from pathlib import Path
inbox = Path('/data/ml-models-to-validate')
if inbox.exists():
    zips = list(inbox.glob('*.zip'))
    print(len(zips))
else:
    print(0)
" 2>&1)
ok "ZIPs in inbox: $inbox_count"

# ── Recent errors ─────────────────────────────────────────────────────
echo ""
echo "=== Recent Errors (last 5) ==="
docker exec "$CONTAINER" python3 -c "
import json
errors = []
try:
    with open('/data/logs/events.jsonl') as f:
        for line in f:
            try:
                ev = json.loads(line)
                if 'error' in ev.get('type', ''):
                    errors.append(ev)
            except: pass
except FileNotFoundError:
    pass
for e in errors[-5:]:
    ts = e.get('ts', '?')[:19]
    etype = e.get('type', '?')
    err = e.get('error', '?')[:80]
    print(f'  {ts} | {etype} | {err}')
if not errors:
    print('  No errors found')
" 2>&1

echo ""
echo "=== Done ==="
