"""Model-catalog endpoint helpers (subscription-only).

After the OAuth migration the catalog returns a static, hand-curated list of
Anthropic models accessible via the Claude Code subscription. There is no
live catalog fetch because the subscription auth does not expose a model
listing endpoint distinct from the CLI.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from ouroboros.config import load_settings


# Curated list of Anthropic models reachable through the OAuth subscription.
# Update when new families ship. ``id`` matches OUROBOROS_MODEL convention.
_STATIC_CATALOG: list[dict[str, str]] = [
    {
        "id": "anthropic/claude-opus-4.6",
        "name": "Claude Opus 4.6",
        "display_name": "Claude Opus 4.6",
        "alias": "opus",
    },
    {
        "id": "anthropic/claude-sonnet-4.6",
        "name": "Claude Sonnet 4.6",
        "display_name": "Claude Sonnet 4.6",
        "alias": "sonnet",
    },
    {
        "id": "anthropic/claude-haiku-4.5",
        "name": "Claude Haiku 4.5",
        "display_name": "Claude Haiku 4.5",
        "alias": "haiku",
    },
]


def _build_entry(model_id: str, display_name: str, alias: str) -> dict[str, str]:
    return {
        "provider_id": "claude_code_oauth",
        "provider": "Anthropic (subscription)",
        "source": "Claude Code Subscription",
        "id": model_id,
        "name": display_name,
        "value": model_id,
        "label": f"Anthropic · {display_name}",
        "alias": alias,
    }


async def api_model_catalog(_request: Request) -> JSONResponse:
    settings = load_settings()
    has_auth = bool(
        str(settings.get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip()
        or str(settings.get("ANTHROPIC_API_KEY", "") or "").strip()
    )

    items: list[dict[str, str]] = [
        _build_entry(model["id"], model["display_name"], model["alias"])
        for model in _STATIC_CATALOG
    ]
    errors: list[dict[str, str]] = []
    if not has_auth:
        errors.append({
            "provider_id": "claude_code_oauth",
            "error": (
                "No Claude auth configured. Set CLAUDE_CODE_OAUTH_TOKEN "
                "(preferred) or ANTHROPIC_API_KEY in Settings."
            ),
        })
    return JSONResponse({"items": items, "errors": errors})
