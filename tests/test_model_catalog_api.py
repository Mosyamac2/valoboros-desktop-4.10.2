"""Model catalog API tests — subscription-only.

After the OAuth migration there is only one cloud provider, so the catalog
returns a static list of Anthropic models reachable through the subscription.
"""

import asyncio
import json

from ouroboros import model_catalog_api


def _run_endpoint(monkeypatch, settings: dict) -> dict:
    monkeypatch.setattr(model_catalog_api, "load_settings", lambda: settings)
    response = asyncio.run(model_catalog_api.api_model_catalog(None))
    return json.loads(response.body.decode("utf-8"))


def test_model_catalog_returns_static_anthropic_models_when_auth_present(monkeypatch):
    payload = _run_endpoint(monkeypatch, {"CLAUDE_CODE_OAUTH_TOKEN": "sk-oauth"})
    assert payload["errors"] == []
    ids = {item["id"] for item in payload["items"]}
    assert "anthropic/claude-opus-4.6" in ids
    assert "anthropic/claude-sonnet-4.6" in ids
    assert all(item["provider_id"] == "claude_code_oauth" for item in payload["items"])


def test_model_catalog_returns_error_when_no_auth(monkeypatch):
    payload = _run_endpoint(monkeypatch, {})
    assert payload["items"]  # static list still returned
    assert payload["errors"]
    assert payload["errors"][0]["provider_id"] == "claude_code_oauth"


def test_model_catalog_accepts_legacy_anthropic_api_key(monkeypatch):
    payload = _run_endpoint(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant"})
    assert payload["errors"] == []
