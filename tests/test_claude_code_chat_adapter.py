"""Tests for the claude_code_chat gateway (chat-completions adapter).

The gateway eagerly imports ``claude_agent_sdk`` at module load. We install
a lightweight mock when the real SDK is absent so the gateway can be
imported and unit-tested without network/SDK availability.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest


def _ensure_sdk_mock():
    if "claude_agent_sdk" in sys.modules:
        return
    mock_sdk = types.ModuleType("claude_agent_sdk")
    mock_sdk.ClaudeAgentOptions = type("ClaudeAgentOptions", (), {"__init__": lambda self, **kw: None})
    mock_sdk.ClaudeSDKClient = type("ClaudeSDKClient", (), {})
    mock_sdk.HookMatcher = type("HookMatcher", (), {"__init__": lambda self, **kw: None})
    mock_sdk.AssistantMessage = type("AssistantMessage", (), {})
    mock_sdk.ResultMessage = type("ResultMessage", (), {})
    mock_sdk.UserMessage = type("UserMessage", (), {})

    async def _empty_query(**_kw):
        if False:
            yield None
    mock_sdk.query = _empty_query
    sys.modules["claude_agent_sdk"] = mock_sdk


_ensure_sdk_mock()


from ouroboros.gateways import claude_code_chat as gw  # noqa: E402


# ---------------------------------------------------------------------------
# Model alias resolution
# ---------------------------------------------------------------------------

class TestResolveModelAlias:
    @pytest.mark.parametrize("input_id,expected", [
        ("anthropic/claude-opus-4.7", "opus"),
        ("anthropic/claude-sonnet-4.6", "sonnet"),
        ("anthropic/claude-haiku-4.5", "haiku"),
        ("anthropic::claude-opus-4-7", "opus"),
        ("claude-opus-4.7", "opus"),
        ("opus", "opus"),
        ("sonnet", "sonnet"),
        ("haiku", "haiku"),
    ])
    def test_known_aliases(self, input_id, expected):
        assert gw.resolve_model_alias(input_id) == expected

    def test_unknown_defaults_to_opus(self):
        assert gw.resolve_model_alias("some-unknown-model") == "opus"

    def test_empty_string_defaults_to_opus(self):
        assert gw.resolve_model_alias("") == "opus"


# ---------------------------------------------------------------------------
# Message serialization
# ---------------------------------------------------------------------------

class TestSerializeHistory:
    def test_system_messages_are_concatenated_into_system_prompt(self):
        messages = [
            {"role": "system", "content": "system-1"},
            {"role": "system", "content": "system-2"},
            {"role": "user", "content": "hi"},
        ]
        system, user, images = gw._serialize_history(messages)
        assert "system-1" in system
        assert "system-2" in system
        assert "hi" in user
        assert images == []

    def test_tool_results_render_with_call_id(self):
        messages = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function",
                                                  "function": {"name": "x", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result body"},
        ]
        _system, user, _images = gw._serialize_history(messages)
        assert "[tool_result id=call_1]" in user
        assert "result body" in user
        assert "<tool_call>" in user

    def test_inline_image_blocks_captured_from_last_user_message(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": "https://example/x.png"}},
                ],
            },
        ]
        _system, _user, images = gw._serialize_history(messages)
        assert images and images[0]["image_url"]["url"] == "https://example/x.png"


# ---------------------------------------------------------------------------
# Tool-call parsing
# ---------------------------------------------------------------------------

class TestParseToolCalls:
    def test_pure_tool_call_block_is_extracted(self):
        content = '<tool_call>{"name": "echo", "arguments": {"text": "hi"}}</tool_call>'
        calls = gw._parse_tool_calls(content, {"echo"})
        assert calls
        assert calls[0]["function"]["name"] == "echo"
        assert '"text"' in calls[0]["function"]["arguments"]

    def test_prose_mixed_with_tool_call_returns_none(self):
        content = 'Here is a call: <tool_call>{"name": "echo", "arguments": {}}</tool_call> followed by prose'
        assert gw._parse_tool_calls(content, {"echo"}) is None

    def test_unknown_tool_name_returns_none(self):
        content = '<tool_call>{"name": "ghost", "arguments": {}}</tool_call>'
        assert gw._parse_tool_calls(content, {"echo"}) is None

    def test_multiple_calls_in_sequence(self):
        content = (
            '<tool_call>{"name": "a", "arguments": {}}</tool_call>'
            '<tool_call>{"name": "b", "arguments": {"x": 1}}</tool_call>'
        )
        calls = gw._parse_tool_calls(content, {"a", "b"})
        assert calls is not None
        assert [c["function"]["name"] for c in calls] == ["a", "b"]

    def test_empty_content_returns_none(self):
        assert gw._parse_tool_calls("", {"x"}) is None


# ---------------------------------------------------------------------------
# Usage normalization
# ---------------------------------------------------------------------------

class TestNormalizeUsage:
    def test_subscription_sets_cost_zero_and_notional(self):
        raw = gw._RawResult(
            text="hi", session_id="s1", cost_usd=0.12,
            usage={
                "input_tokens": 100, "output_tokens": 50,
                "cache_read_input_tokens": 20, "cache_creation_input_tokens": 10,
            },
        )
        out = gw._normalize_usage(raw, "opus", sub_cost_zero=True)
        assert out["prompt_tokens"] == 100
        assert out["completion_tokens"] == 50
        assert out["cached_tokens"] == 20
        assert out["cache_write_tokens"] == 10
        assert out["cost"] == 0.0
        assert out["notional_cost"] == 0.12
        assert out["provider"] == "claude_code_oauth"
        assert out["resolved_model"] == "anthropic/claude-opus"

    def test_api_key_mode_keeps_real_cost(self):
        raw = gw._RawResult(text="", session_id="", cost_usd=0.05, usage={"input_tokens": 10})
        out = gw._normalize_usage(raw, "sonnet", sub_cost_zero=False)
        assert out["cost"] == 0.05
        assert out["provider"] == "anthropic_api_key"


# ---------------------------------------------------------------------------
# Auth detection
# ---------------------------------------------------------------------------

class TestHaveSubscriptionAuth:
    def test_oauth_present(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth")
        assert gw.have_subscription_auth() is True

    def test_api_key_present(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        assert gw.have_subscription_auth() is True

    def test_neither_present(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert gw.have_subscription_auth() is False


# ---------------------------------------------------------------------------
# SDK env construction
# ---------------------------------------------------------------------------

class TestBuildSdkEnv:
    def test_oauth_token_unsets_api_key(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        env = gw._build_sdk_env()
        assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-oauth"
        assert "ANTHROPIC_API_KEY" not in env

    def test_no_oauth_keeps_api_key(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        env = gw._build_sdk_env()
        assert env.get("ANTHROPIC_API_KEY") == "sk-ant"


# ---------------------------------------------------------------------------
# Tool instruction injection
# ---------------------------------------------------------------------------

class TestBuildToolInstruction:
    def test_no_tools_returns_empty(self):
        assert gw._build_tool_instruction(None) == ""
        assert gw._build_tool_instruction([]) == ""

    def test_tools_become_json_schemas(self):
        tools = [
            {"function": {"name": "foo", "description": "Foo tool",
                          "parameters": {"type": "object", "properties": {}}}},
        ]
        instruction = gw._build_tool_instruction(tools)
        assert "<tool_call>" in instruction
        assert "foo" in instruction
        assert "Foo tool" in instruction
