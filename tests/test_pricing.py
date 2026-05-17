"""Tests for ouroboros.pricing under the subscription backend.

After the OAuth migration (BIBLE v5.1):
  - Only Anthropic models remain in MODEL_PRICING_STATIC.
  - infer_api_key_type returns claude_code_oauth / anthropic_api_key / local.
  - emit_llm_usage_event defaults the provider to claude_code_oauth.
  - There is no live OpenRouter pricing fetch.
"""

import os
import queue
from unittest.mock import patch

import pytest

from ouroboros.pricing import (
    MODEL_PRICING_STATIC,
    emit_llm_usage_event,
    estimate_cost,
    get_pricing,
    infer_api_key_type,
    infer_model_category,
)


# --- estimate_cost -----------------------------------------------------------

class TestEstimateCost:

    def test_known_model_no_cache(self):
        # Sonnet 4.6: input=$3/M, cached=$0.30/M, output=$15/M
        cost = estimate_cost(
            "anthropic/claude-sonnet-4.6",
            prompt_tokens=1000, completion_tokens=500, cached_tokens=0,
        )
        expected = 1000 * 3.0 / 1e6 + 500 * 15.0 / 1e6
        assert abs(cost - expected) < 1e-6

    def test_known_model_with_cache(self):
        cost = estimate_cost(
            "anthropic/claude-sonnet-4.6",
            prompt_tokens=10000, completion_tokens=1000,
            cached_tokens=8000,
        )
        expected = (2000 * 3.0 + 8000 * 0.30 + 1000 * 15.0) / 1e6
        assert abs(cost - expected) < 1e-6

    def test_unknown_model_returns_zero(self):
        cost = estimate_cost("unknown/model-xyz", 1000, 500)
        assert cost == 0.0

    def test_zero_tokens(self):
        cost = estimate_cost("anthropic/claude-sonnet-4.6", 0, 0)
        assert cost == 0.0

    def test_prefix_match(self):
        cost = estimate_cost(
            "anthropic/claude-sonnet-4.6:beta",
            prompt_tokens=1000, completion_tokens=0,
        )
        assert cost > 0

    def test_cached_greater_than_prompt_clamped(self):
        cost = estimate_cost(
            "anthropic/claude-sonnet-4.6",
            prompt_tokens=100, completion_tokens=0, cached_tokens=200,
        )
        expected = 200 * 0.30 / 1e6
        assert abs(cost - expected) < 1e-6

    def test_all_static_models_have_three_tuple(self):
        for model, prices in MODEL_PRICING_STATIC.items():
            assert len(prices) == 3
            assert all(isinstance(p, (int, float)) for p in prices)
            assert all(p >= 0 for p in prices)

    def test_haiku_static_pricing_is_registered(self):
        assert MODEL_PRICING_STATIC["anthropic/claude-haiku-4.5"] == (0.80, 0.08, 4.0)


# --- infer_api_key_type ------------------------------------------------------

class TestInferApiKeyType:

    def test_oauth_env_returns_oauth(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert infer_api_key_type("anthropic/claude-opus-4.6") == "claude_code_oauth"

    def test_api_key_env_returns_api_key(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        assert infer_api_key_type("anthropic/claude-opus-4.6") == "anthropic_api_key"

    def test_local_suffix_overrides(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth")
        assert infer_api_key_type("qwen2.5-7b (local)") == "local"

    def test_explicit_provider_override(self):
        assert infer_api_key_type("anthropic/claude-opus-4.6", provider="local") == "local"
        assert infer_api_key_type("anthropic/claude-opus-4.6", provider="claude_code_oauth") == "claude_code_oauth"

    def test_unknown_returns_unknown(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert infer_api_key_type("some-random-model") == "unknown"


# --- infer_model_category ----------------------------------------------------

class TestInferModelCategory:

    def test_matches_main_model(self):
        with patch.dict(os.environ, {"OUROBOROS_MODEL": "anthropic/claude-sonnet-4.6"}):
            assert infer_model_category("anthropic/claude-sonnet-4.6") == "main"

    def test_matches_light_model(self):
        with patch.dict(os.environ, {"OUROBOROS_MODEL_LIGHT": "anthropic/claude-sonnet-4.6"}):
            assert infer_model_category("anthropic/claude-sonnet-4.6") == "light"

    def test_no_match_returns_other(self):
        with patch.dict(os.environ, {}, clear=True):
            assert infer_model_category("unknown/model") == "other"


# --- emit_llm_usage_event ----------------------------------------------------

class TestEmitLlmUsageEvent:

    def test_emits_to_queue_with_oauth_default(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth")
        q = queue.Queue()
        emit_llm_usage_event(
            event_queue=q,
            task_id="test-123",
            model="anthropic/claude-sonnet-4.6",
            usage={"prompt_tokens": 1000, "completion_tokens": 500},
            cost=0.0,
            category="task",
        )
        event = q.get_nowait()
        assert event["type"] == "llm_usage"
        assert event["task_id"] == "test-123"
        assert event["model"] == "anthropic/claude-sonnet-4.6"
        assert event["prompt_tokens"] == 1000
        assert event["completion_tokens"] == 500
        assert event["cost"] == 0.0
        assert event["provider"] == "claude_code_oauth"
        assert event["api_key_type"] == "claude_code_oauth"

    def test_notional_cost_forwarded(self):
        q = queue.Queue()
        emit_llm_usage_event(
            event_queue=q,
            task_id="t",
            model="anthropic/claude-opus-4.6",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "notional_cost": 0.025},
            cost=0.0,
        )
        event = q.get_nowait()
        assert event["notional_cost"] == 0.025

    def test_provider_override_sets_api_key_type(self):
        q = queue.Queue()
        emit_llm_usage_event(
            event_queue=q,
            task_id="t",
            model="qwen-local (local)",
            usage={"prompt_tokens": 0, "completion_tokens": 0},
            cost=0.0,
            provider="local",
        )
        event = q.get_nowait()
        assert event["provider"] == "local"
        assert event["api_key_type"] == "local"

    def test_none_queue_no_error(self):
        emit_llm_usage_event(None, "t", "m", {}, 0.0)

    def test_missing_usage_fields_default_zero(self):
        q = queue.Queue()
        emit_llm_usage_event(q, "t", "m", {}, 0.0)
        event = q.get_nowait()
        assert event["prompt_tokens"] == 0
        assert event["completion_tokens"] == 0
        assert event["cached_tokens"] == 0

    def test_full_queue_no_crash(self):
        q = queue.Queue(maxsize=1)
        q.put("filler")
        emit_llm_usage_event(q, "t", "m", {}, 0.0)


# --- get_pricing -------------------------------------------------------------

class TestGetPricing:

    def setup_method(self):
        import ouroboros.pricing as mod
        mod._cached_pricing = None

    def test_returns_static_pricing(self):
        pricing = get_pricing()
        assert "anthropic/claude-sonnet-4.6" in pricing
        assert len(pricing) >= len(MODEL_PRICING_STATIC)

    def test_pricing_is_cached(self):
        first = get_pricing()
        second = get_pricing()
        assert first is second
