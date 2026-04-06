# --- Build stage ---
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install \
    -r requirements.txt \
    pandas nbformat nbconvert arxiv pyarrow openpyxl

# --- Runtime stage ---
FROM python:3.12-slim

# System deps for sandbox (unshare), git, and Node.js (for Claude Code CLI)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    util-linux \
    nodejs \
    npm \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/* /root/.npm

# Create non-root user
RUN useradd -m -u 1000 valoboros
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Copy project source
COPY . /app/

# Create data directories and make entrypoint executable
RUN mkdir -p /data /repo \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R valoboros:valoboros /app /data /repo

# Environment defaults
ENV OUROBOROS_APP_ROOT=/opt/ouroboros \
    OUROBOROS_REPO_DIR=/repo \
    OUROBOROS_DATA_DIR=/data \
    OUROBOROS_SERVER_HOST=0.0.0.0 \
    OUROBOROS_SERVER_PORT=8765 \
    OUROBOROS_VALIDATION_INBOX_DIR=ml-models-to-validate \
    OUROBOROS_VALIDATION_SANDBOX_MEM_MB=2048 \
    OUROBOROS_FILE_BROWSER_DEFAULT=/data \
    PYTHONUNBUFFERED=1

USER valoboros

EXPOSE 8765

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/')" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
