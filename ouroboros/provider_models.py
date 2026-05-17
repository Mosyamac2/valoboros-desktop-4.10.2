"""Model identifier normalization for the subscription-only backend.

After the OAuth migration the only cloud provider is Anthropic (via Claude
Code subscription). This module is a small shim that keeps the public
``normalize_model_identity`` function used by ``pricing.py`` and a few
defaults still referenced by the onboarding wizard.
"""

from __future__ import annotations


ANTHROPIC_DIRECT_DEFAULTS = {
    "main": "anthropic/claude-opus-4.7",
    "code": "anthropic/claude-opus-4.7",
    "light": "anthropic/claude-sonnet-4.6",
    "fallback": "anthropic/claude-sonnet-4.6",
}

# Retained for onboarding_wizard's bootstrap until it's fully rewritten;
# the OpenAI defaults are no longer reachable but the constant import would
# break older settings.json restores otherwise.
OPENAI_DIRECT_DEFAULTS = {
    "main": "anthropic/claude-opus-4.7",
    "code": "anthropic/claude-opus-4.7",
    "light": "anthropic/claude-sonnet-4.6",
    "fallback": "anthropic/claude-sonnet-4.6",
}

_ANTHROPIC_MODEL_ALIASES = {
    "claude-opus-4.7": "claude-opus-4-7",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
}


def normalize_anthropic_model_id(model_id: str) -> str:
    text = str(model_id or "").strip()
    return _ANTHROPIC_MODEL_ALIASES.get(text, text)


def migrate_model_value(provider: str, value: str) -> str:
    """Legacy helper. Under OAuth there is one provider; the value is returned
    as-is. Kept callable for backwards-compatible callers.
    """
    del provider
    return str(value or "").strip()


def normalize_model_identity(model: str) -> str:
    text = str(model or "").strip()
    if text.endswith(" (local)"):
        text = text[:-8]
    if text.startswith("anthropic::"):
        return f"anthropic/{normalize_anthropic_model_id(text[len('anthropic::'):])}"
    if text.startswith("anthropic/"):
        return f"anthropic/{normalize_anthropic_model_id(text[len('anthropic/'):])}"
    return text
