"""Tests for the web_search tool — Claude WebSearch via SDK.

Before BIBLE v5.1 web_search called OpenAI Responses API directly. After the
OAuth migration it routes through ``ouroboros.gateways.claude_code_chat``
with ``allowed_tools=["WebSearch"]``. The OpenAI path is gone.
"""

import json
import types

import ouroboros.tools.search as search_module


def test_web_search_returns_answer_from_claude_websearch(monkeypatch):
    """A successful Claude WebSearch returns ``{"answer": "..."}``."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth")

    def fake_web_search(query_text, model, timeout=300.0):
        return "fresh answer", {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "cost": 0.0,
            "notional_cost": 0.001,
            "provider": "claude_code_oauth",
            "resolved_model": "anthropic/claude-sonnet-4.6",
        }

    fake_gw = types.SimpleNamespace(web_search=fake_web_search)
    monkeypatch.setattr(
        "ouroboros.gateways.claude_code_chat.web_search",
        fake_web_search,
        raising=True,
    )
    ctx = types.SimpleNamespace(pending_events=[], emit_progress_fn=None)
    result = json.loads(search_module._web_search(ctx, "latest news"))

    assert result == {"answer": "fresh answer"}
    assert ctx.pending_events
    event = ctx.pending_events[0]
    assert event["model_category"] == "websearch"
    assert event["provider"] == "claude_code_oauth"
    assert event["source"] == "web_search"


def test_web_search_surfaces_gateway_error(monkeypatch):
    """Gateway errors are returned as ``{"error": "..."}``."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth")

    def boom(*_a, **_kw):
        raise RuntimeError("rate-limited")

    monkeypatch.setattr(
        "ouroboros.gateways.claude_code_chat.web_search",
        boom,
        raising=True,
    )

    ctx = types.SimpleNamespace(pending_events=[], emit_progress_fn=None)
    result = json.loads(search_module._web_search(ctx, "anything"))
    assert "error" in result
    assert "rate-limited" in result["error"]
