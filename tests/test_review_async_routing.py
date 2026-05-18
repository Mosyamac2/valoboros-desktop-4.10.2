"""Review async routing tests — single-model under OAuth.

The legacy tests exercised reviewer diversity across model families. After
BIBLE v5.1, review runs against a single Claude model. The remaining
contract worth testing is that ``_multi_model_review_async`` correctly
calls the LLM, parses the structured JSON output, and emits usage events.
"""

from __future__ import annotations

import asyncio
import pathlib


def test_multi_model_review_async_uses_single_claude_model(monkeypatch, tmp_path):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools import review as review_module

    calls = []

    class FakeLLMClient:
        def __init__(self, *args, **kwargs):
            pass

        async def chat_async(self, **kwargs):
            calls.append(kwargs)
            return (
                {"content": '[{"item":"check","verdict":"PASS","severity":"advisory","reason":"ok"}]'},
                {
                    "provider": "claude_code_oauth",
                    "resolved_model": "anthropic/claude-opus-4.7",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "cached_tokens": 7,
                    "cache_write_tokens": 2,
                    "cost": 0.0,
                    "notional_cost": 0.01,
                },
            )

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-test-oauth")
    monkeypatch.setattr(review_module, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(review_module, "_load_bible", lambda: "Bible")

    ctx = ToolContext(repo_dir=pathlib.Path(tmp_path), drive_root=pathlib.Path(tmp_path))
    result = asyncio.run(
        review_module._multi_model_review_async(
            "review target",
            "review instructions",
            ["anthropic/claude-opus-4.7"],
            ctx,
        )
    )

    # Only one LLM call should be made even if multiple models were passed in.
    assert len(calls) == 1
    assert calls[0]["model"] == "anthropic/claude-opus-4.7"
    assert calls[0]["temperature"] == 0.2
    assert result["results"][0]["cached_tokens"] == 7
    assert result["results"][0]["cache_write_tokens"] == 2
    assert ctx.pending_events
    assert ctx.pending_events[0]["provider"] == "claude_code_oauth"


def test_multi_model_review_async_collapses_multiple_models_to_first(monkeypatch, tmp_path):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools import review as review_module

    calls = []

    class FakeLLMClient:
        def __init__(self, *args, **kwargs):
            pass

        async def chat_async(self, **kwargs):
            calls.append(kwargs)
            return (
                {"content": '[{"item":"check","verdict":"PASS","severity":"advisory","reason":"ok"}]'},
                {"provider": "claude_code_oauth", "resolved_model": kwargs["model"],
                 "prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0},
            )

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-test-oauth")
    monkeypatch.setattr(review_module, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(review_module, "_load_bible", lambda: "Bible")

    ctx = ToolContext(repo_dir=pathlib.Path(tmp_path), drive_root=pathlib.Path(tmp_path))
    asyncio.run(
        review_module._multi_model_review_async(
            "x", "y",
            [
                "anthropic/claude-opus-4.7",
                "anthropic/claude-sonnet-4.6",
                "anthropic/claude-haiku-4.5",
            ],
            ctx,
        )
    )

    assert len(calls) == 1
    assert calls[0]["model"] == "anthropic/claude-opus-4.7"
