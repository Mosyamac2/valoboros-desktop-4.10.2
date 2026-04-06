#!/bin/bash
# Valoboros Docker entrypoint — bootstrap repo if empty, then start server

set -e

REPO_DIR="${OUROBOROS_REPO_DIR:-/repo}"
APP_DIR="/app"

# Bootstrap: if repo is empty (no server.py), copy the full codebase from /app
if [ ! -f "$REPO_DIR/server.py" ]; then
    echo "[entrypoint] Bootstrapping repo from app bundle..."

    # Copy everything except Docker/build artifacts
    cp -a "$APP_DIR/VERSION" "$REPO_DIR/" 2>/dev/null || true
    cp -a "$APP_DIR/BIBLE.md" "$REPO_DIR/" 2>/dev/null || true
    cp -a "$APP_DIR/README.md" "$REPO_DIR/" 2>/dev/null || true
    cp -a "$APP_DIR/CLAUDE.md" "$REPO_DIR/" 2>/dev/null || true
    cp -a "$APP_DIR/server.py" "$REPO_DIR/" 2>/dev/null || true
    cp -a "$APP_DIR/pyproject.toml" "$REPO_DIR/" 2>/dev/null || true
    cp -a "$APP_DIR/requirements.txt" "$REPO_DIR/" 2>/dev/null || true
    cp -a "$APP_DIR/Makefile" "$REPO_DIR/" 2>/dev/null || true

    for dir in ouroboros supervisor prompts web webview docs tests assets; do
        if [ -d "$APP_DIR/$dir" ]; then
            cp -a "$APP_DIR/$dir" "$REPO_DIR/"
        fi
    done

    # Initialize git repo
    cd "$REPO_DIR"
    if [ ! -d ".git" ]; then
        git init
        git config user.name "Valoboros"
        git config user.email "valoboros@local"
        git add -A
        git commit -m "Initial commit from Docker app bundle" || true
        git branch -M ouroboros
        git branch ouroboros-stable || true
        echo "[entrypoint] Repo bootstrapped with full codebase on branch 'ouroboros'"
    fi
else
    echo "[entrypoint] Repo already bootstrapped (server.py exists)"
    # Always sync web UI + prompts from /app to /repo (these should match the image)
    echo "[entrypoint] Syncing web UI and prompts from app bundle..."
    cp -a "$APP_DIR/web" "$REPO_DIR/"
    cp -a "$APP_DIR/prompts" "$REPO_DIR/"
    cp -a "$APP_DIR/BIBLE.md" "$REPO_DIR/" 2>/dev/null || true
    cp -a "$APP_DIR/CLAUDE.md" "$REPO_DIR/" 2>/dev/null || true
    cp -a "$APP_DIR/ouroboros/validation" "$REPO_DIR/ouroboros/" 2>/dev/null || true
    cp -a "$APP_DIR/ouroboros/server_validation_api.py" "$REPO_DIR/ouroboros/" 2>/dev/null || true
    cp -a "$APP_DIR/ouroboros/tools/model_intake.py" "$REPO_DIR/ouroboros/tools/" 2>/dev/null || true
    cp -a "$APP_DIR/ouroboros/tools/validation.py" "$REPO_DIR/ouroboros/tools/" 2>/dev/null || true
    cp -a "$APP_DIR/ouroboros/tools/validation_feedback.py" "$REPO_DIR/ouroboros/tools/" 2>/dev/null || true
    cp -a "$APP_DIR/ouroboros/context.py" "$REPO_DIR/ouroboros/" 2>/dev/null || true
fi

# Start the server
cd "$APP_DIR"
exec python server.py
