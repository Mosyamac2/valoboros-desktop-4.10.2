"""LLMClient construction / refresh tests under the subscription backend.

The legacy tests exercised OpenRouter API-key refresh on the OpenAI-compatible
client. That client is gone (the cloud transport now delegates to
``ouroboros.gateways.claude_code_chat``). What remains worth testing is that
LLMClient is cheap to construct repeatedly and ignores legacy constructor
kwargs.
"""

from __future__ import annotations

import sys
import types
import unittest


def _install_mock_sdk():
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


class TestLlmClientLifecycle(unittest.TestCase):
    def test_repeated_construction_is_safe(self):
        from ouroboros.llm import LLMClient
        a, b = LLMClient(), LLMClient()
        self.assertIsNotNone(a.default_model())
        self.assertIsNotNone(b.default_model())

    def test_constructor_ignores_legacy_kwargs(self):
        from ouroboros.llm import LLMClient
        client = LLMClient(api_key="legacy-ignored", base_url="legacy-ignored")
        self.assertEqual(client.default_model(), client.default_model())
