"""LLM provider routing tests — subscription-only.

After the OAuth migration (BIBLE v5.1, 2026-05-17) the multi-provider
routing in llm.py was removed. The remaining surface is:
  - LLMClient delegates cloud calls to the claude_code_chat gateway
  - default_model() / available_models() reflect env-configured Claude ids
  - local llama-cpp path remains for offline fallback
"""

from __future__ import annotations

import sys
import types

import pytest


def _install_mock_sdk():
    """Install a lightweight mock of claude_agent_sdk if the real one is absent."""
    if "claude_agent_sdk" not in sys.modules:
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


_install_mock_sdk()


from ouroboros.llm import LLMClient  # noqa: E402


def test_default_model_reads_env(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MODEL", "anthropic/claude-opus-4.7")
    assert LLMClient().default_model() == "anthropic/claude-opus-4.7"


def test_default_model_falls_back_to_constant(monkeypatch):
    monkeypatch.delenv("OUROBOROS_MODEL", raising=False)
    assert LLMClient().default_model() == "anthropic/claude-opus-4.7"


def test_available_models_dedupes(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MODEL", "anthropic/claude-opus-4.7")
    monkeypatch.setenv("OUROBOROS_MODEL_CODE", "anthropic/claude-opus-4.7")
    monkeypatch.setenv("OUROBOROS_MODEL_LIGHT", "anthropic/claude-sonnet-4.6")
    models = LLMClient().available_models()
    assert models == ["anthropic/claude-opus-4.7", "anthropic/claude-sonnet-4.6"]


def test_llm_client_construction_ignores_legacy_args():
    """LLMClient kept the legacy ``api_key``/``base_url`` constructor signature
    so call sites that still pass these arguments don't blow up. They are
    intentionally ignored under the subscription backend."""
    client = LLMClient(api_key="ignored", base_url="ignored")
    assert client.default_model()
