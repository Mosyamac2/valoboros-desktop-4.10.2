# Docker Deployment Plan for Valoboros

**Date:** 2026-04-05  
**Status:** PLAN ONLY — do not implement yet  
**Target:** Ubuntu server (4 vCPU, 8 GB RAM, 50 GB disk recommended)

---

## 1. Why Docker

| Concern | Bare Python | Docker |
|---------|------------|--------|
| Agent rewrites system files | LLM safety check (software) | Container filesystem isolation (physical) |
| Sandbox escape | RLIMIT + unshare (same user space) | Container boundary (different namespace) |
| Memory runaway | Can OOM-kill the host | `--memory=6g` container limit |
| Disk runaway | No limit | Volume quota or `--storage-opt` |
| Network exfiltration by model code | `unshare --net` (may fail without privileges) | Guaranteed: container network policy |
| Agent modifies safety files | Restored on restart (software) | Read-only bind mount (physical) |
| Reproducible deployment | Manual pip install | Single `docker build` |

---

## 2. Architecture

```
Host machine
├── /opt/valoboros/                    ← project source (git clone)
├── /opt/valoboros-data/              ← persistent data (Docker volume)
│   ├── data/                         ← ~/Ouroboros/data/ inside container
│   │   ├── state/
│   │   ├── memory/
│   │   ├── logs/
│   │   ├── settings.json
│   │   └── validations/              ← all validation bundles
│   └── repo/                         ← ~/Ouroboros/repo/ inside container
├── /opt/valoboros-inbox/             ← model ZIP drop folder
│   ├── model_a.zip
│   ├── model_b.zip
│   └── .valoboros_processed.json
└── Docker container (valoboros)
    ├── /app/                         ← project code (COPY from build)
    ├── /app/ouroboros/validation/sandbox.py  ← READ-ONLY bind mount
    ├── /app/ouroboros/safety.py              ← READ-ONLY bind mount
    ├── /app/ouroboros/tools/registry.py      ← READ-ONLY bind mount
    ├── /app/prompts/SAFETY.md               ← READ-ONLY bind mount
    ├── /app/BIBLE.md                        ← READ-ONLY bind mount
    ├── /data/ → /opt/valoboros-data/data/   ← read-write volume
    ├── /repo/ → /opt/valoboros-data/repo/   ← read-write volume
    └── /inbox/ → /opt/valoboros-inbox/      ← read-write bind mount
```

**Key design decisions:**
- Safety-critical files mounted as **read-only** — agent physically cannot modify them
- Validation data and repo on **persistent volumes** — survive container restarts
- Inbox folder as a **bind mount** — user drops ZIPs from the host
- Container runs as **non-root** user
- Network: only outbound HTTPS to LLM APIs allowed

---

## 3. Dockerfile

**New file:** `Dockerfile`

```dockerfile
# --- Build stage ---
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install \
    -r requirements.txt \
    pandas nbformat nbconvert arxiv pyarrow openpyxl

# --- Runtime stage ---
FROM python:3.12-slim

# System deps for sandbox (unshare) and git
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 valoboros
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Copy project source
COPY . /app/

# Create data directories
RUN mkdir -p /data /repo /inbox \
    && chown -R valoboros:valoboros /app /data /repo /inbox

# Environment defaults
ENV OUROBOROS_APP_ROOT=/opt/ouroboros \
    OUROBOROS_REPO_DIR=/repo \
    OUROBOROS_DATA_DIR=/data \
    OUROBOROS_SERVER_HOST=0.0.0.0 \
    OUROBOROS_SERVER_PORT=8765 \
    OUROBOROS_VALIDATION_INBOX_DIR=/inbox \
    OUROBOROS_VALIDATION_SANDBOX_MEM_MB=2048 \
    OUROBOROS_FILE_BROWSER_DEFAULT=/data \
    PYTHONUNBUFFERED=1

USER valoboros

EXPOSE 8765

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/')" || exit 1

CMD ["python", "server.py"]
```

**Notes:**
- Multi-stage build keeps image small (~400 MB vs ~1.2 GB single stage)
- `util-linux` provides `unshare` for sandbox network isolation
- Non-root user `valoboros` (UID 1000) — prevents privilege escalation
- `PYTHONUNBUFFERED=1` ensures logs stream in real-time

---

## 4. Docker Compose

**New file:** `docker-compose.yml`

```yaml
version: "3.8"

services:
  valoboros:
    build: .
    container_name: valoboros
    restart: unless-stopped

    ports:
      - "8765:8765"

    environment:
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
      - OUROBOROS_NETWORK_PASSWORD=${OUROBOROS_NETWORK_PASSWORD:-}
      - OUROBOROS_SERVER_HOST=0.0.0.0
      - OUROBOROS_SERVER_PORT=8765
      - OUROBOROS_REPO_DIR=/repo
      - OUROBOROS_DATA_DIR=/data
      - OUROBOROS_VALIDATION_INBOX_DIR=/inbox
      - OUROBOROS_VALIDATION_SANDBOX_MEM_MB=2048
      - OUROBOROS_FILE_BROWSER_DEFAULT=/data
      - TOTAL_BUDGET=50

    volumes:
      # Persistent data (survives container restarts)
      - valoboros-data:/data
      - valoboros-repo:/repo

      # Model inbox (host → container)
      - ./ml-models-to-validate:/inbox

      # Safety-critical files: READ-ONLY bind mounts
      # Agent physically cannot modify these inside the container
      - ./BIBLE.md:/app/BIBLE.md:ro
      - ./ouroboros/safety.py:/app/ouroboros/safety.py:ro
      - ./ouroboros/tools/registry.py:/app/ouroboros/tools/registry.py:ro
      - ./prompts/SAFETY.md:/app/prompts/SAFETY.md:ro
      - ./ouroboros/validation/sandbox.py:/app/ouroboros/validation/sandbox.py:ro

    # Resource limits
    deploy:
      resources:
        limits:
          cpus: "4"
          memory: 6G
        reservations:
          cpus: "2"
          memory: 2G

    # Security: drop all capabilities except what's needed
    cap_drop:
      - ALL
    cap_add:
      - SYS_ADMIN    # needed for unshare --net in sandbox

    # Prevent container from gaining new privileges
    security_opt:
      - no-new-privileges:true

volumes:
  valoboros-data:
  valoboros-repo:
```

---

## 5. Environment File

**New file:** `.env.example`

```bash
# Required
OPENROUTER_API_KEY=sk-or-v1-your-key-here

# Optional: password for non-localhost web UI access
OUROBOROS_NETWORK_PASSWORD=

# Optional: other LLM providers
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# Optional: budget
TOTAL_BUDGET=50
```

User copies to `.env` and fills in:

```bash
cp .env.example .env
nano .env  # add your keys
```

Docker Compose reads `.env` automatically.

---

## 6. Launch Commands

### First time setup

```bash
# Clone the repo
git clone https://github.com/Mosyamac2/valoboros-desktop-4.10.2.git /opt/valoboros
cd /opt/valoboros

# Configure
cp .env.example .env
nano .env  # add OPENROUTER_API_KEY

# Build and launch
docker compose up -d --build

# Check logs
docker compose logs -f valoboros
```

### Daily operations

```bash
# Start
docker compose up -d

# Stop
docker compose down

# Restart
docker compose restart

# View logs
docker compose logs -f --tail=100

# Drop a model for validation
cp /path/to/model.zip ml-models-to-validate/

# Read validation reports (from inside the volume)
docker compose exec valoboros cat /data/validations/<bundle_id>/results/report.md

# Or mount the volume to read from host
docker run --rm -v valoboros-data:/data alpine cat /data/validations/<bundle_id>/results/report.md

# Shell into the container for debugging
docker compose exec valoboros bash
```

### Update to new version

```bash
cd /opt/valoboros
git pull
docker compose up -d --build
# Data persists across rebuilds (Docker volumes)
```

---

## 7. Safety-Critical Read-Only Mounts

The key security feature: safety files are bind-mounted as `:ro` (read-only).

```yaml
- ./BIBLE.md:/app/BIBLE.md:ro
- ./ouroboros/safety.py:/app/ouroboros/safety.py:ro
- ./ouroboros/tools/registry.py:/app/ouroboros/tools/registry.py:ro
- ./prompts/SAFETY.md:/app/prompts/SAFETY.md:ro
- ./ouroboros/validation/sandbox.py:/app/ouroboros/validation/sandbox.py:ro
```

**What this means:**
- The agent can `repo_read` these files — they appear normal
- The agent CANNOT `repo_write` or `str_replace_editor` them — write will fail
- `launcher.py → _sync_core_files()` will fail silently (file is read-only) — this is fine because the source file IS the correct version
- Even if the agent rewrites `_sync_core_files()` itself, the mount is enforced by the Docker daemon, not by Python code

**Impact on `launcher.py`:**
- `_sync_core_files()` tries to `shutil.copy2(src, dst)` for safety files
- With ro mounts, `dst` is read-only → `PermissionError` → caught by existing `try/except`
- The files are already correct (mounted from host source) so sync is unnecessary
- `_commit_synced_files()` will see no changes to commit — clean git status

**Impact on agent self-modification:**
- Agent CAN still modify all other files (validation checks, prompts, etc.)
- These writes go to `/repo/` volume which is read-write
- Only the 5 safety-critical files are physically protected

---

## 8. Network Security

### Option A: Simple (default in docker-compose.yml)

Container has full outbound network access (needed for OpenRouter API).
Model code sandbox uses `unshare --net` inside the container.

### Option B: Strict (for production)

Create a custom Docker network that only allows specific outbound destinations:

```yaml
# In docker-compose.yml:
services:
  valoboros:
    networks:
      - valoboros-net

networks:
  valoboros-net:
    driver: bridge
    internal: false  # allows outbound
```

Then add iptables rules on the host:

```bash
# Allow only LLM API endpoints
iptables -I DOCKER-USER -s 172.18.0.0/16 -d api.openai.com -j ACCEPT
iptables -I DOCKER-USER -s 172.18.0.0/16 -d openrouter.ai -j ACCEPT
iptables -I DOCKER-USER -s 172.18.0.0/16 -d api.anthropic.com -j ACCEPT
iptables -I DOCKER-USER -s 172.18.0.0/16 -d export.arxiv.org -j ACCEPT
iptables -I DOCKER-USER -s 172.18.0.0/16 -j DROP
```

This means: even if model code escapes the subprocess sandbox AND escapes
`unshare --net`, it still can't reach anything except the approved APIs.

---

## 9. Code Changes Required

### 9.1. New files to create

| File | Content |
|------|---------|
| `Dockerfile` | As shown in §3 |
| `docker-compose.yml` | As shown in §4 |
| `.env.example` | As shown in §5 |
| `.dockerignore` | See below |

### 9.2. `.dockerignore`

```
.venv/
.git/
__pycache__/
*.pyc
validation_data/
ml-models-to-validate/
.env
aux_notes/
tests/
*.egg-info/
dist/
build/
```

### 9.3. Modifications to existing code

| File | Change | Why |
|------|--------|-----|
| `launcher.py` → `_sync_core_files()` | Add `try/except PermissionError` around `shutil.copy2` | Read-only mounts will raise PermissionError; should log and continue, not crash |
| `ouroboros/config.py` | No changes needed | Already reads all paths from env vars (`OUROBOROS_REPO_DIR`, `OUROBOROS_DATA_DIR`, etc.) |
| `ouroboros/validation/watcher.py` | No changes needed | Already reads `inbox_dir` from config |
| `ouroboros/validation/sandbox.py` | No changes needed | `unshare --net` works inside Docker with `SYS_ADMIN` capability |
| `README.md` | Add Docker section | Deployment instructions |

### 9.4. launcher.py fix for read-only mounts

Current code in `_sync_core_files()`:

```python
for rel in sync_paths:
    src = bundle_dir / rel
    dst = REPO_DIR / rel
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)  # ← will fail on ro mount
```

Change to:

```python
for rel in sync_paths:
    src = bundle_dir / rel
    dst = REPO_DIR / rel
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except PermissionError:
            log.debug("Skipped sync for read-only file: %s", rel)
```

This is the ONLY code change needed. Everything else works via environment variables.

---

## 10. Persistent Data Strategy

| Data | Docker volume | Survives rebuild? | Survives `docker compose down -v`? |
|------|--------------|-------------------|-------------------------------------|
| Validation bundles | `valoboros-data:/data/validations/` | Yes | **No** — use `down` without `-v` |
| Knowledge base | `valoboros-data:/data/memory/knowledge/` | Yes | No |
| Settings | `valoboros-data:/data/settings.json` | Yes | No |
| Effectiveness data | `valoboros-data:/data/` | Yes | No |
| Agent repo (self-modified code) | `valoboros-repo:/repo/` | Yes | No |
| Inbox (model ZIPs) | Bind mount `./ml-models-to-validate:/inbox` | Yes (host filesystem) | Yes |

**Backup strategy:**

```bash
# Backup all persistent data
docker run --rm -v valoboros-data:/data -v $(pwd)/backups:/backup \
    alpine tar czf /backup/valoboros-data-$(date +%Y%m%d).tar.gz /data

# Backup agent's self-modified repo
docker run --rm -v valoboros-repo:/repo -v $(pwd)/backups:/backup \
    alpine tar czf /backup/valoboros-repo-$(date +%Y%m%d).tar.gz /repo
```

---

## 11. Monitoring

### Container health

```bash
# Health status
docker inspect --format='{{.State.Health.Status}}' valoboros

# Resource usage
docker stats valoboros --no-stream

# Disk usage
docker system df -v | grep valoboros
```

### Validation activity

```bash
# Recent validation logs
docker compose exec valoboros tail -50 /data/logs/events.jsonl

# List all validations
docker compose exec valoboros python -c "
from ouroboros.tools.model_intake import _list_validations_impl
from pathlib import Path
print(_list_validations_impl(Path('/data/validations')))
"

# Read latest report
docker compose exec valoboros cat /data/validations/$(ls -t /data/validations/ | head -1)/results/report.md
```

---

## 12. Estimated Effort

| Item | LOC | Complexity |
|------|-----|-----------|
| `Dockerfile` | ~35 | Low |
| `docker-compose.yml` | ~55 | Low |
| `.env.example` | ~10 | Low |
| `.dockerignore` | ~12 | Low |
| `launcher.py` PermissionError fix | ~3 | Low |
| `README.md` Docker section | ~30 | Low |
| **Total** | **~145** | **Low** |

All changes are additive — no existing code is modified except a 3-line
try/except in launcher.py. The Docker setup is a deployment layer on top
of the existing architecture, not a refactor.

---

## 13. Quick Start (what the user will actually run)

```bash
git clone https://github.com/Mosyamac2/valoboros-desktop-4.10.2.git
cd valoboros-desktop-4.10.2
cp .env.example .env
echo "OPENROUTER_API_KEY=sk-or-v1-your-key" >> .env
docker compose up -d --build
# Open http://your-server:8765
# Drop model ZIPs into ml-models-to-validate/
```

Five commands from zero to running.
