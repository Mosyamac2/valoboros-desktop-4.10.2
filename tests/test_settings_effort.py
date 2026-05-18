"""Tests for effort, review models, and review enforcement settings."""
import os
from ouroboros.config import (
    SETTINGS_DEFAULTS,
    apply_settings_to_env,
    resolve_effort,
    get_review_models,
    get_review_enforcement,
)


# ---------------------------------------------------------------------------
# Legacy env var backward compat
# ---------------------------------------------------------------------------

def test_initial_effort_default(monkeypatch):
    """Default effort is 'medium' when env var not set."""
    monkeypatch.delenv("OUROBOROS_EFFORT_TASK", raising=False)
    monkeypatch.delenv("OUROBOROS_INITIAL_REASONING_EFFORT", raising=False)
    assert resolve_effort("task") == "medium"


def test_initial_effort_valid_values(monkeypatch):
    """Valid effort values pass through unchanged via OUROBOROS_EFFORT_TASK."""
    for effort in ("none", "low", "medium", "high"):
        monkeypatch.setenv("OUROBOROS_EFFORT_TASK", effort)
        monkeypatch.delenv("OUROBOROS_INITIAL_REASONING_EFFORT", raising=False)
        assert resolve_effort("task") == effort


def test_initial_effort_invalid_falls_back_to_medium(monkeypatch):
    """Invalid effort values fall back to 'medium'."""
    monkeypatch.setenv("OUROBOROS_EFFORT_TASK", "extreme")
    monkeypatch.delenv("OUROBOROS_INITIAL_REASONING_EFFORT", raising=False)
    assert resolve_effort("task") == "medium"


# ---------------------------------------------------------------------------
# New per-type defaults in SETTINGS_DEFAULTS
# ---------------------------------------------------------------------------

def test_effort_defaults_in_config():
    """All four effort keys have correct defaults in SETTINGS_DEFAULTS."""
    assert SETTINGS_DEFAULTS.get("OUROBOROS_EFFORT_TASK") == "medium"
    assert SETTINGS_DEFAULTS.get("OUROBOROS_EFFORT_EVOLUTION") == "high"
    assert SETTINGS_DEFAULTS.get("OUROBOROS_EFFORT_REVIEW") == "medium"
    assert SETTINGS_DEFAULTS.get("OUROBOROS_EFFORT_CONSCIOUSNESS") == "low"


def test_review_models_default_in_config():
    """OUROBOROS_REVIEW_MODELS has a default value in config.

    Single-model review under BIBLE v5.1: exactly one Claude id is enough.
    """
    val = SETTINGS_DEFAULTS.get("OUROBOROS_REVIEW_MODELS", "")
    assert val  # non-empty
    models = [m.strip() for m in val.split(",") if m.strip()]
    assert len(models) >= 1
    assert all(m.startswith("anthropic/") for m in models)


def test_review_enforcement_default_in_config():
    """OUROBOROS_REVIEW_ENFORCEMENT defaults to advisory."""
    assert SETTINGS_DEFAULTS.get("OUROBOROS_REVIEW_ENFORCEMENT") == "advisory"


# ---------------------------------------------------------------------------
# get_review_models() — single source of truth
# ---------------------------------------------------------------------------

def test_get_review_models_default(monkeypatch):
    """get_review_models() returns the config default when env is unset."""
    monkeypatch.delenv("OUROBOROS_REVIEW_MODELS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("OUROBOROS_MODEL", raising=False)
    models = get_review_models()
    assert isinstance(models, list)
    assert len(models) == 1
    assert models[0].startswith("anthropic/")


def test_get_review_models_custom(monkeypatch):
    """get_review_models() takes the first model from the configured list.

    Multi-model review was collapsed to single-model in BIBLE v5.1; any
    additional entries are ignored.
    """
    monkeypatch.setenv("OUROBOROS_REVIEW_MODELS", "a/b,c/d")
    models = get_review_models()
    assert models == ["a/b"]


def test_get_review_models_empty_env_falls_back_to_default(monkeypatch):
    """Empty env falls back to the default single-model id."""
    monkeypatch.setenv("OUROBOROS_REVIEW_MODELS", "")
    monkeypatch.delenv("OUROBOROS_MODEL", raising=False)
    models = get_review_models()
    assert len(models) == 1
    expected_default = [m.strip() for m in SETTINGS_DEFAULTS["OUROBOROS_REVIEW_MODELS"].split(",") if m.strip()][:1]
    assert models == expected_default


def test_get_review_enforcement_default(monkeypatch):
    """get_review_enforcement() returns the config default when env is unset."""
    monkeypatch.delenv("OUROBOROS_REVIEW_ENFORCEMENT", raising=False)
    assert get_review_enforcement() == "advisory"


def test_get_review_enforcement_custom(monkeypatch):
    """get_review_enforcement() accepts advisory and blocking."""
    monkeypatch.setenv("OUROBOROS_REVIEW_ENFORCEMENT", "advisory")
    assert get_review_enforcement() == "advisory"
    monkeypatch.setenv("OUROBOROS_REVIEW_ENFORCEMENT", "blocking")
    assert get_review_enforcement() == "blocking"


def test_get_review_enforcement_invalid_falls_back(monkeypatch):
    """Unknown values fall back to advisory (the default)."""
    monkeypatch.setenv("OUROBOROS_REVIEW_ENFORCEMENT", "strictest")
    assert get_review_enforcement() == "advisory"


def test_apply_settings_clears_review_models_restores_default(monkeypatch):
    """Clearing OUROBOROS_REVIEW_MODELS in settings restores the default in env."""
    settings = {"OUROBOROS_REVIEW_MODELS": ""}
    apply_settings_to_env(settings)
    env_val = os.environ.get("OUROBOROS_REVIEW_MODELS", "")
    assert env_val == SETTINGS_DEFAULTS["OUROBOROS_REVIEW_MODELS"]
    assert len(get_review_models()) == 1


def test_apply_settings_clears_review_enforcement_restores_default(monkeypatch):
    """Clearing OUROBOROS_REVIEW_ENFORCEMENT restores the default in env."""
    settings = {"OUROBOROS_REVIEW_ENFORCEMENT": ""}
    apply_settings_to_env(settings)
    env_val = os.environ.get("OUROBOROS_REVIEW_ENFORCEMENT", "")
    assert env_val == SETTINGS_DEFAULTS["OUROBOROS_REVIEW_ENFORCEMENT"]
    assert get_review_enforcement() == "advisory"


# ---------------------------------------------------------------------------
# apply_settings_to_env propagation
# ---------------------------------------------------------------------------

def test_apply_settings_to_env_includes_effort_keys():
    """apply_settings_to_env propagates all four effort keys."""
    settings = {
        "OUROBOROS_EFFORT_TASK": "low",
        "OUROBOROS_EFFORT_EVOLUTION": "medium",
        "OUROBOROS_EFFORT_REVIEW": "high",
        "OUROBOROS_EFFORT_CONSCIOUSNESS": "none",
        "OUROBOROS_REVIEW_MODELS": "model-a,model-b",
        "OUROBOROS_REVIEW_ENFORCEMENT": "advisory",
    }
    apply_settings_to_env(settings)
    assert os.environ.get("OUROBOROS_EFFORT_TASK") == "low"
    assert os.environ.get("OUROBOROS_EFFORT_EVOLUTION") == "medium"
    assert os.environ.get("OUROBOROS_EFFORT_REVIEW") == "high"
    assert os.environ.get("OUROBOROS_EFFORT_CONSCIOUSNESS") == "none"
    assert os.environ.get("OUROBOROS_REVIEW_MODELS") == "model-a,model-b"
    assert os.environ.get("OUROBOROS_REVIEW_ENFORCEMENT") == "advisory"
    # cleanup
    for k in ("OUROBOROS_EFFORT_TASK", "OUROBOROS_EFFORT_EVOLUTION",
              "OUROBOROS_EFFORT_REVIEW", "OUROBOROS_EFFORT_CONSCIOUSNESS",
              "OUROBOROS_REVIEW_MODELS", "OUROBOROS_REVIEW_ENFORCEMENT"):
        os.environ.pop(k, None)
