"""Server runtime helpers — subscription-only.

After the OAuth migration (BIBLE v5.1) there is one cloud provider, so the
multi-provider auto-defaults flow collapsed into a no-op. Tests now cover:
  - presence detection for the OAuth token / Anthropic API key / local routing
  - that `apply_runtime_provider_defaults` is a no-op (kept for callable stability)
"""

from ouroboros.server_runtime import (
    apply_runtime_provider_defaults,
    has_startup_ready_provider,
    has_supervisor_provider,
)


def test_has_startup_ready_provider_accepts_oauth_token():
    assert has_startup_ready_provider({"CLAUDE_CODE_OAUTH_TOKEN": "sk-oauth"})


def test_has_startup_ready_provider_accepts_legacy_anthropic_key():
    assert has_startup_ready_provider({"ANTHROPIC_API_KEY": "sk-ant"})


def test_has_startup_ready_provider_accepts_local_routing():
    assert has_startup_ready_provider({"USE_LOCAL_MAIN": True})


def test_has_startup_ready_provider_rejects_local_source_alone():
    """A configured local source without any routing slot is not enough."""
    assert not has_startup_ready_provider(
        {"LOCAL_MODEL_SOURCE": "Qwen/Qwen2.5-7B-Instruct-GGUF"}
    )


def test_has_startup_ready_provider_rejects_empty_settings():
    assert not has_startup_ready_provider({})


def test_has_supervisor_provider_requires_remote_credentials_or_local_routing():
    assert has_supervisor_provider({"CLAUDE_CODE_OAUTH_TOKEN": "sk-oauth"})
    assert has_supervisor_provider({"ANTHROPIC_API_KEY": "sk-ant"})
    assert has_supervisor_provider({"USE_LOCAL_MAIN": True})
    assert has_supervisor_provider({"USE_LOCAL_FALLBACK": "True"})
    assert not has_supervisor_provider(
        {"LOCAL_MODEL_SOURCE": "Qwen/Qwen2.5-7B-Instruct-GGUF"}
    )


def test_apply_runtime_provider_defaults_is_noop_under_oauth():
    """Under a single-provider backend the auto-default migration is a no-op.

    The function is kept callable for legacy launcher integration.
    """
    settings = {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-oauth",
        "OUROBOROS_MODEL": "anthropic/claude-opus-4.6",
    }
    normalized, changed, changed_keys = apply_runtime_provider_defaults(settings)
    assert not changed
    assert changed_keys == []
    assert normalized == settings
