"""One-shot bootstrap for the agent's self-modifying repository.

Materializes ``$OUROBOROS_REPO_DIR`` (default ``~/Ouroboros/repo``) by
calling :func:`launcher.bootstrap_repo`, then configures the GitHub
remote on it using ``GITHUB_TOKEN`` / ``GITHUB_REPO`` from the merged
Ouroboros settings (``~/Ouroboros/data/settings.json`` plus env vars).
The remote URL embeds the token using GitHub's ``x-access-token`` form,
so subsequent ``git push`` calls from ``supervisor/git_ops`` succeed
without a credential helper.

Idempotent. Safe to run multiple times. Does NOT start the agent.

Usage::

    python scripts/bootstrap_ouroboros_repo.py
    python scripts/bootstrap_ouroboros_repo.py --push     # also push 'ouroboros' branch
    python scripts/bootstrap_ouroboros_repo.py --remote-url https://github.com/Foo/bar.git --push
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import subprocess
import sys
from urllib.parse import urlparse, urlunparse

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

log = logging.getLogger("bootstrap_ouroboros_repo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _embed_token(remote_url: str, token: str) -> str:
    """Insert ``x-access-token:TOKEN`` into the userinfo of an HTTPS URL.

    GitHub HTTPS auth accepts ``https://x-access-token:<TOKEN>@host/...``.
    Non-HTTPS URLs (ssh, git://) are returned unchanged — token-embedding
    does not apply to them.
    """
    parsed = urlparse(remote_url)
    if parsed.scheme not in {"http", "https"}:
        return remote_url
    netloc = f"x-access-token:{token}@{parsed.hostname or ''}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _redact_token(url: str) -> str:
    parsed = urlparse(url)
    if parsed.username:
        netloc = f"{parsed.username}:***@{parsed.hostname or ''}"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    return url


def _run(cmd: list[str], cwd: pathlib.Path, check: bool = True) -> subprocess.CompletedProcess:
    log.info("$ %s  (cwd=%s)", " ".join(cmd), cwd)
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=check)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote-url", default=None,
                        help="HTTPS URL of the GitHub repo to push the agent's "
                             "ouroboros branch to. If omitted, uses GITHUB_REPO "
                             "from settings; if that is empty, the project repo's "
                             "origin URL is used.")
    parser.add_argument("--push", action="store_true",
                        help="After configuring the remote, push the 'ouroboros' branch "
                             "(force-with-lease) so subsequent self-modifications can ride it.")
    parser.add_argument("--branch", default="ouroboros",
                        help="Branch to push (default: ouroboros).")
    args = parser.parse_args()

    from launcher import bootstrap_repo, REPO_DIR
    from ouroboros.config import load_settings

    bootstrap_repo()
    if not REPO_DIR.exists():
        log.error("Bootstrap did not create %s", REPO_DIR)
        return 1

    settings = load_settings()
    token = (settings.get("GITHUB_TOKEN") or "").strip()
    if not token:
        log.error("GITHUB_TOKEN missing from settings — set it in "
                  "~/Ouroboros/data/settings.json or as an env var before bootstrapping the remote.")
        return 2

    remote_url = (args.remote_url or settings.get("GITHUB_REPO") or "").strip()
    if not remote_url:
        # Fall back to the project's own origin.
        proj = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(REPO), capture_output=True, text=True,
        )
        if proj.returncode != 0 or not proj.stdout.strip():
            log.error("No remote URL provided and project repo has no 'origin'.")
            return 3
        remote_url = proj.stdout.strip()
        log.info("Using project repo's origin as agent remote: %s", remote_url)

    authed_url = _embed_token(remote_url, token)

    # set-url is idempotent; add only if no origin yet.
    existing = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=str(REPO_DIR), capture_output=True, text=True,
    )
    if existing.returncode == 0 and existing.stdout.strip():
        _run(["git", "remote", "set-url", "origin", authed_url], cwd=REPO_DIR)
    else:
        _run(["git", "remote", "add", "origin", authed_url], cwd=REPO_DIR)
    log.info("Agent repo origin → %s", _redact_token(authed_url))

    if args.push:
        # The agent's repo lives on the 'ouroboros' branch by convention
        # (supervisor/git_ops.BRANCH_DEV). Push it under the same name on
        # the remote so self-modifications land on a side branch, never master.
        try:
            _run(["git", "push", "-u", "--force-with-lease",
                  "origin", f"{args.branch}:{args.branch}"], cwd=REPO_DIR)
            log.info("Pushed %s to origin/%s", args.branch, args.branch)
        except subprocess.CalledProcessError as exc:
            log.error("git push failed: %s\n%s", exc, exc.stderr)
            return 4

    log.info("Bootstrap complete. Agent repo at %s", REPO_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
