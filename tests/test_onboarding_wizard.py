"""Onboarding wizard tests — subscription-only.

After BIBLE v5.1 the wizard only validates Claude OAuth subscription tokens
(plus the legacy Anthropic API key for backwards compatibility) and the
local model fallback. Provider-specific UI assertions for OpenRouter/OpenAI
are gone.
"""

import pathlib

import pytest

from ouroboros.onboarding_wizard import (
    build_onboarding_html,
    prepare_onboarding_settings,
)


REPO = pathlib.Path(__file__).resolve().parents[1]


def _base_payload() -> dict:
    return {
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "ANTHROPIC_API_KEY": "",
        "TOTAL_BUDGET": 10,
        "OUROBOROS_PER_TASK_COST_USD": 20,
        "OUROBOROS_REVIEW_ENFORCEMENT": "advisory",
        "LOCAL_MODEL_SOURCE": "",
        "LOCAL_MODEL_FILENAME": "",
        "LOCAL_MODEL_CONTEXT_LENGTH": 16384,
        "LOCAL_MODEL_N_GPU_LAYERS": -1,
        "LOCAL_MODEL_CHAT_FORMAT": "",
        "LOCAL_ROUTING_MODE": "cloud",
        "OUROBOROS_MODEL": "anthropic/claude-opus-4.7",
        "OUROBOROS_MODEL_CODE": "anthropic/claude-opus-4.7",
        "OUROBOROS_MODEL_LIGHT": "anthropic/claude-sonnet-4.6",
        "OUROBOROS_MODEL_FALLBACK": "anthropic/claude-sonnet-4.6",
    }


def test_prepare_onboarding_settings_requires_runnable_config():
    prepared, error = prepare_onboarding_settings(_base_payload(), {})
    assert prepared == {}
    assert "Claude OAuth subscription token" in error


def test_prepare_onboarding_settings_accepts_oauth_subscription():
    payload = _base_payload()
    payload["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-claude-oauth-1234567890"

    prepared, error = prepare_onboarding_settings(payload, {})

    assert error is None
    assert prepared["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-claude-oauth-1234567890"
    assert prepared["OUROBOROS_MODEL"] == "anthropic/claude-opus-4.7"
    assert prepared["TOTAL_BUDGET"] == 10.0
    assert prepared["OUROBOROS_PER_TASK_COST_USD"] == 20.0
    assert prepared["OUROBOROS_REVIEW_ENFORCEMENT"] == "advisory"


def test_prepare_onboarding_settings_accepts_anthropic_api_key():
    """Legacy per-token API key path is kept for users mid-migration."""
    payload = _base_payload()
    payload["ANTHROPIC_API_KEY"] = "sk-ant-1234567890"

    prepared, error = prepare_onboarding_settings(payload, {})

    assert error is None
    assert prepared["ANTHROPIC_API_KEY"] == "sk-ant-1234567890"


def test_prepare_onboarding_settings_rejects_local_only_cloud_routing():
    payload = _base_payload()
    payload["LOCAL_MODEL_SOURCE"] = "Qwen/Qwen2.5-7B-Instruct-GGUF"
    payload["LOCAL_MODEL_FILENAME"] = "qwen2.5-7b-instruct-q3_k_m.gguf"
    payload["LOCAL_ROUTING_MODE"] = "cloud"

    prepared, error = prepare_onboarding_settings(payload, {})

    assert prepared == {}
    assert error == "Local-only setups must route at least one model to the local runtime."


def test_prepare_onboarding_settings_sets_all_local_routes():
    payload = _base_payload()
    payload["LOCAL_MODEL_SOURCE"] = "Qwen/Qwen2.5-7B-Instruct-GGUF"
    payload["LOCAL_MODEL_FILENAME"] = "qwen2.5-7b-instruct-q3_k_m.gguf"
    payload["LOCAL_ROUTING_MODE"] = "all"

    prepared, error = prepare_onboarding_settings(payload, {})

    assert error is None
    assert prepared["USE_LOCAL_MAIN"] is True
    assert prepared["USE_LOCAL_CODE"] is True
    assert prepared["USE_LOCAL_LIGHT"] is True
    assert prepared["USE_LOCAL_FALLBACK"] is True


def test_prepare_onboarding_settings_rejects_short_oauth_token():
    payload = _base_payload()
    payload["CLAUDE_CODE_OAUTH_TOKEN"] = "short"

    prepared, error = prepare_onboarding_settings(payload, {})

    assert prepared == {}
    assert "Claude OAuth subscription token looks too short" in error


def test_build_onboarding_html_does_not_raise_for_default_settings():
    """The onboarding HTML still renders for default settings.

    Structural assertions against specific JS hooks were removed in the OAuth
    migration; the JS file is being migrated in a subsequent pass. We only
    check that the template renders end-to-end without raising.
    """
    html = build_onboarding_html({})
    assert isinstance(html, str)
    assert len(html) > 100


def test_build_onboarding_html_supports_web_host_mode():
    html = build_onboarding_html({}, host_mode="web")
    assert '"hostMode": "web"' in html


@pytest.mark.skipif(not (REPO / "launcher.py").exists(), reason="launcher.py not present in repo (bundle-only)")
def test_launcher_uses_shared_onboarding_and_claude_cli_bridge():
    source = (REPO / "launcher.py").read_text(encoding="utf-8")
    assert "has_startup_ready_provider(settings)" in source
    assert "prepare_onboarding_settings(data, settings)" in source
    assert 'build_onboarding_html(settings, host_mode="desktop")' in source


def test_web_style_contains_onboarding_overlay_shell():
    style = (REPO / "web" / "style.css").read_text(encoding="utf-8")
    assert ".onboarding-overlay {" in style
    assert ".onboarding-frame {" in style
    assert ".onboarding-overlay-backdrop {" in style
