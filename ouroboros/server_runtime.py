"""Helpers shared by server startup, onboarding, and WebSocket liveness."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable


def _truthy_setting(value) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "on"}


def _setting_text(settings: dict, key: str) -> str:
    return str(settings.get(key, "") or "").strip()


def has_remote_provider(settings: dict) -> bool:
    """Return True when the Anthropic subscription (or legacy API key) is set."""
    return bool(
        _setting_text(settings, "CLAUDE_CODE_OAUTH_TOKEN")
        or _setting_text(settings, "ANTHROPIC_API_KEY")
    )


def has_local_model_source(settings: dict) -> bool:
    """Return True when a local model source has been configured."""
    return bool(_setting_text(settings, "LOCAL_MODEL_SOURCE"))


def has_local_routing(settings: dict) -> bool:
    """Return True when any model slot is configured to use the local server."""
    return any(
        _truthy_setting(settings.get(k))
        for k in ("USE_LOCAL_MAIN", "USE_LOCAL_CODE", "USE_LOCAL_LIGHT", "USE_LOCAL_FALLBACK")
    )


def has_startup_ready_provider(settings: dict) -> bool:
    """Return True when startup/onboarding should consider runtime configured."""
    return has_remote_provider(settings) or has_local_routing(settings)


def has_supervisor_provider(settings: dict) -> bool:
    """Return True when the runtime has enough provider config to start supervisor."""
    return has_remote_provider(settings) or has_local_routing(settings)


def apply_runtime_provider_defaults(settings: dict) -> tuple[dict, bool, list[str]]:
    """No-op under OAuth: there is only one cloud provider, so per-provider
    auto-defaults are not needed. Kept as a stable callable for compatibility.
    """
    return dict(settings), False, []


def setup_remote_if_configured(settings: dict, log) -> None:
    """Set up GitHub remote and migrate credentials if configured."""
    slug = settings.get("GITHUB_REPO", "")
    token = settings.get("GITHUB_TOKEN", "")
    if not slug or not token:
        return
    from supervisor.git_ops import configure_remote, migrate_remote_credentials

    remote_ok, remote_msg = configure_remote(slug, token)
    if not remote_ok:
        log.warning("Remote configuration failed on startup: %s", remote_msg)
        return
    mig_ok, mig_msg = migrate_remote_credentials()
    if not mig_ok:
        log.warning("Credential migration failed on startup: %s", mig_msg)


async def ws_heartbeat_loop(
    has_clients_fn: Callable[[], bool],
    broadcast_fn: Callable[[dict], Awaitable[None]],
    interval_sec: float = 15.0,
) -> None:
    """Keep embedded clients active and give watchdogs a steady liveness signal."""
    while True:
        await asyncio.sleep(interval_sec)
        if not has_clients_fn():
            continue
        await broadcast_fn({
            "type": "heartbeat",
            "ts": datetime.now(timezone.utc).isoformat(),
        })
