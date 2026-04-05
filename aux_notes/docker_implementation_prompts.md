# Docker Deployment Implementation Prompts

**How to use:** Execute these 2 prompts sequentially in separate Claude Code sessions.

**Start each session by saying:**
> Read `aux_notes/docker_deployment_plan.md` — this is the full Docker plan.
> Then execute the prompt below.

---

## Prompt 1 of 2: Dockerfile, Compose, Config Files + Code Fix

```
Read the Docker deployment plan in aux_notes/docker_deployment_plan.md.

This prompt creates all Docker files and applies the one code fix needed.

### Files to create:

1. Dockerfile — Multi-stage build as specified in plan §3:
   - Builder stage: python:3.12-slim, install all pip dependencies
     (requirements.txt + pandas nbformat nbconvert arxiv pyarrow openpyxl)
   - Runtime stage: python:3.12-slim, install system deps (git, util-linux),
     create non-root user "valoboros" (UID 1000), copy Python packages from
     builder, copy project source, create /data /repo /inbox dirs,
     set environment defaults (OUROBOROS_APP_ROOT, REPO_DIR, DATA_DIR,
     SERVER_HOST=0.0.0.0, SERVER_PORT=8765, VALIDATION_INBOX_DIR=/inbox,
     SANDBOX_MEM_MB=2048, FILE_BROWSER_DEFAULT=/data, PYTHONUNBUFFERED=1),
     switch to valoboros user, expose 8765, add healthcheck, CMD python server.py

2. docker-compose.yml — As specified in plan §4:
   - Service "valoboros": build from ., restart unless-stopped
   - Ports: 8765:8765
   - Environment: read from .env (OPENROUTER_API_KEY, OUROBOROS_NETWORK_PASSWORD,
     TOTAL_BUDGET), plus hardcoded server/path/sandbox settings
   - Volumes:
     a. valoboros-data:/data (persistent)
     b. valoboros-repo:/repo (persistent)
     c. ./ml-models-to-validate:/inbox (bind mount)
     d. 5 read-only safety file mounts:
        ./BIBLE.md:/app/BIBLE.md:ro
        ./ouroboros/safety.py:/app/ouroboros/safety.py:ro
        ./ouroboros/tools/registry.py:/app/ouroboros/tools/registry.py:ro
        ./prompts/SAFETY.md:/app/prompts/SAFETY.md:ro
        ./ouroboros/validation/sandbox.py:/app/ouroboros/validation/sandbox.py:ro
   - Deploy resources: limits cpus 4, memory 6G; reservations cpus 2, memory 2G
   - Security: cap_drop ALL, cap_add SYS_ADMIN, no-new-privileges
   - Named volumes: valoboros-data, valoboros-repo

3. .env.example — As specified in plan §5:
   - OPENROUTER_API_KEY= (required, with comment)
   - OUROBOROS_NETWORK_PASSWORD= (optional)
   - OPENAI_API_KEY= (optional)
   - ANTHROPIC_API_KEY= (optional)
   - TOTAL_BUDGET=50 (optional)

4. .dockerignore — As specified in plan §9.2:
   - .venv/, .git/, __pycache__/, *.pyc, validation_data/,
     ml-models-to-validate/, .env, aux_notes/, tests/, *.egg-info/,
     dist/, build/

### Files to modify:

5. launcher.py — Fix _sync_core_files() for read-only Docker mounts.
   Find the `shutil.copy2(src, dst)` call inside the sync loop and wrap it:

   Change:
   ```python
   if src.exists():
       dst.parent.mkdir(parents=True, exist_ok=True)
       shutil.copy2(src, dst)
   ```

   To:
   ```python
   if src.exists():
       dst.parent.mkdir(parents=True, exist_ok=True)
       try:
           shutil.copy2(src, dst)
       except PermissionError:
           log.debug("Skipped sync for read-only file: %s", rel)
   ```

   Read launcher.py first to find the exact location (around line 236).

### Verify

```bash
# 1. Verify all files exist
test -f Dockerfile && echo "Dockerfile: OK" || echo "Dockerfile: MISSING"
test -f docker-compose.yml && echo "docker-compose.yml: OK" || echo "docker-compose.yml: MISSING"
test -f .env.example && echo ".env.example: OK" || echo ".env.example: MISSING"
test -f .dockerignore && echo ".dockerignore: OK" || echo ".dockerignore: MISSING"

# 2. Verify Dockerfile syntax
docker build --check . 2>&1 || echo "(docker build --check not supported, try full build)"

# 3. Verify docker-compose syntax
docker compose config --quiet 2>&1 && echo "docker-compose.yml: valid" || echo "docker-compose.yml: INVALID"

# 4. Verify launcher.py has the PermissionError fix
grep -q "PermissionError" launcher.py && echo "launcher.py: fix OK" || echo "launcher.py: fix MISSING"

# 5. Verify read-only mounts are in compose
grep -c ":ro" docker-compose.yml | xargs -I{} echo "Read-only mounts: {}"

# 6. Run existing tests to confirm nothing broke
.venv/bin/python -m pytest tests/test_validation_types.py tests/test_sandbox.py tests/test_check_registry.py tests/test_intake.py tests/test_integration.py --tb=short -q
```

All checks must pass.
```

---

## Prompt 2 of 2: README Docker Section + Full Build Test

```
Read the Docker deployment plan in aux_notes/docker_deployment_plan.md,
sections §6 (Launch Commands), §10 (Persistent Data), §13 (Quick Start).

This prompt adds Docker documentation to the README and runs a full build test.

### Files to modify:

1. README.md — Add a "Docker Deployment" section AFTER the existing
   "Run from Source" section and BEFORE the "Build" section.

   Content should include:

   ## Docker Deployment

   The recommended way to run Valoboros on a server. Docker provides
   filesystem isolation, resource limits, and read-only safety file mounts.

   ### Quick Start

   ```bash
   git clone https://github.com/Mosyamac2/valoboros-desktop-4.10.2.git
   cd valoboros-desktop-4.10.2
   cp .env.example .env
   nano .env  # add your OPENROUTER_API_KEY
   docker compose up -d --build
   ```

   Then open `http://your-server:8765` in your browser.

   ### Drop models for validation

   ```bash
   cp /path/to/model.zip ml-models-to-validate/
   ```

   ### View logs and reports

   ```bash
   docker compose logs -f --tail=100
   docker compose exec valoboros cat /data/validations/<bundle_id>/results/report.md
   ```

   ### Security features

   - Safety-critical files (BIBLE.md, safety.py, registry.py, SAFETY.md, sandbox.py)
     are bind-mounted as **read-only** — the agent physically cannot modify them
   - Container runs as non-root user (`valoboros`, UID 1000)
   - All Linux capabilities dropped except `SYS_ADMIN` (needed for sandbox `unshare`)
   - Resource limits: 6 GB RAM, 4 CPU (configurable in docker-compose.yml)

   ### Environment variables

   | Variable | Required | Description |
   |----------|----------|-------------|
   | `OPENROUTER_API_KEY` | Yes | LLM API key |
   | `OUROBOROS_NETWORK_PASSWORD` | No | Password for remote web UI access |
   | `TOTAL_BUDGET` | No | API spend limit (default: $50) |

   ### Persistent data

   Data survives container rebuilds via Docker volumes:
   - `valoboros-data` — validations, knowledge base, settings, logs
   - `valoboros-repo` — agent's self-modified code repository

   To backup:
   ```bash
   docker run --rm -v valoboros-data:/data -v $(pwd)/backups:/backup \
       alpine tar czf /backup/valoboros-data-$(date +%Y%m%d).tar.gz /data
   ```

### Verify

```bash
# 1. Verify README has Docker section
grep -q "Docker Deployment" README.md && echo "README: OK" || echo "README: MISSING"
grep -q "docker compose up" README.md && echo "README quick start: OK" || echo "README quick start: MISSING"
grep -q "read-only" README.md && echo "README security: OK" || echo "README security: MISSING"

# 2. Try building the Docker image (this is the real test)
# Only run this if Docker is installed on the machine:
if command -v docker &> /dev/null; then
    echo "Docker found — attempting build..."
    docker build -t valoboros-test . 2>&1 | tail -5
    if [ $? -eq 0 ]; then
        echo "BUILD: SUCCESS"
        # Quick smoke test: container starts and healthcheck passes
        docker run -d --name valoboros-smoke \
            -e OPENROUTER_API_KEY=test \
            -p 18765:8765 \
            valoboros-test
        sleep 5
        curl -s -o /dev/null -w "%{http_code}" http://localhost:18765/ | grep -q "200" \
            && echo "SMOKE TEST: OK" || echo "SMOKE TEST: server not responding (may need API key)"
        docker stop valoboros-smoke && docker rm valoboros-smoke
        docker rmi valoboros-test
    else
        echo "BUILD: FAILED"
    fi
else
    echo "Docker not installed — skipping build test"
fi

# 3. Run full validation test suite to confirm nothing broke
.venv/bin/python -m pytest tests/test_validation_types.py tests/test_sandbox.py tests/test_check_registry.py tests/test_seed_checks.py tests/test_stage_orchestrators.py tests/test_intake.py tests/test_synthesis_report.py tests/test_effectiveness.py tests/test_improvement_cycle.py tests/test_integration.py tests/test_dependency_extractor.py tests/test_watcher.py tests/test_reflection_engine.py tests/test_methodology_planner.py tests/test_literature_and_evolution.py tests/test_project_structure.py tests/test_model_researcher.py tests/test_research_pipeline_integration.py --tb=short -q
```

All checks and tests must pass. The Docker build test is optional (requires
Docker on the development machine) — the README and code changes are the
primary deliverables.
```

---

## Summary

| Prompt | Creates | Modifies | Key deliverables |
|--------|---------|----------|-----------------|
| 1 | `Dockerfile`, `docker-compose.yml`, `.env.example`, `.dockerignore` | `launcher.py` (3-line fix) | Full Docker setup, safety mounts, resource limits |
| 2 | — | `README.md` | Docker deployment docs, optional build test |
| **Total** | **4 new files** | **2 modified** | **~145 LOC** |

Only 2 prompts because the plan is mostly new files (no complex code logic),
and the only code change is a 3-line try/except.
