"""
Ouroboros — LLM pricing and cost estimation.

After the OAuth migration, the cloud backend is the Anthropic subscription
(`claude_code_oauth` provider). Subscription calls have ``cost=0`` from the
agent's budget perspective; the SDK still reports a *notional* per-token
cost which we keep for forecasting and observability under ``notional_cost``.

Static Anthropic prices are retained for notional-cost estimation when the
SDK does not return ``total_cost_usd`` (rare).
"""

from __future__ import annotations

import os
import queue
import threading
from typing import Any, Dict, Optional, Tuple

import logging

from ouroboros.provider_models import normalize_model_identity
from ouroboros.utils import utc_now_iso

log = logging.getLogger(__name__)

# Anthropic pricing (per 1M tokens, input/cached_input/output) for notional cost.
MODEL_PRICING_STATIC: Dict[str, Tuple[float, float, float]] = {
    "anthropic/claude-opus-4.6": (5.0, 0.5, 25.0),
    "anthropic/claude-opus-4-6": (5.0, 0.5, 25.0),
    "anthropic/claude-opus-4.7": (5.0, 0.5, 25.0),
    "anthropic/claude-opus-4": (15.0, 1.5, 75.0),
    "anthropic/claude-sonnet-4": (3.0, 0.30, 15.0),
    "anthropic/claude-sonnet-4.6": (3.0, 0.30, 15.0),
    "anthropic/claude-sonnet-4-6": (3.0, 0.30, 15.0),
    "anthropic/claude-sonnet-4.5": (3.0, 0.30, 15.0),
    "anthropic/claude-sonnet-4.7": (3.0, 0.30, 15.0),
    "anthropic/claude-haiku-4.5": (0.80, 0.08, 4.0),
    "anthropic/claude-haiku-4-5": (0.80, 0.08, 4.0),
}

_cached_pricing: Optional[Dict[str, Tuple[float, float, float]]] = None
_pricing_lock = threading.Lock()


def get_pricing() -> Dict[str, Tuple[float, float, float]]:
    """Return the static Anthropic pricing table."""
    global _cached_pricing
    with _pricing_lock:
        if _cached_pricing is None:
            _cached_pricing = dict(MODEL_PRICING_STATIC)
        return _cached_pricing


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Estimate notional cost from token counts. Returns 0 if model unknown.

    Used as a *notional* number when the subscription SDK does not return a
    cost. Subscription budget enforcement does not use this value — budgets
    are zero under OAuth.
    """
    del cache_write_tokens  # cache writes are billed at input rate
    model_pricing = get_pricing()
    pricing = model_pricing.get(model)
    if not pricing:
        best_match = None
        best_length = 0
        for key, val in model_pricing.items():
            if model and model.startswith(key):
                if len(key) > best_length:
                    best_match = val
                    best_length = len(key)
        pricing = best_match
    if not pricing:
        return 0.0
    input_price, cached_price, output_price = pricing
    regular_input = max(0, prompt_tokens - cached_tokens)
    cost = (
        regular_input * input_price / 1_000_000
        + cached_tokens * cached_price / 1_000_000
        + completion_tokens * output_price / 1_000_000
    )
    return round(cost, 6)


def _normalize_model_name(model: str) -> str:
    text = str(model or "").strip()
    if text.endswith(" (local)"):
        return text[:-8]
    return text


def _normalize_model_identity(model: str) -> str:
    return normalize_model_identity(_normalize_model_name(model))


def infer_api_key_type(model: str, provider: Optional[str] = None) -> str:
    """Infer the auth source used for a call.

    Returns one of:
      - "claude_code_oauth"  — Anthropic subscription (OAuth token)
      - "anthropic_api_key"  — legacy per-token Anthropic API key
      - "local"              — local llama-cpp-python server
      - "unknown"
    """
    provider_name = str(provider or "").strip().lower()
    if provider_name in {"claude_code_oauth", "anthropic_api_key", "local"}:
        return provider_name
    if str(model or "").endswith(" (local)"):
        return "local"
    # Default for cloud calls: OAuth subscription (the only cloud backend now).
    if (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip():
        return "claude_code_oauth"
    if (os.environ.get("ANTHROPIC_API_KEY", "") or "").strip():
        return "anthropic_api_key"
    return "unknown"


def infer_model_category(model: str) -> str:
    """Infer model category by comparing against configured model env vars."""
    normalized = _normalize_model_identity(model)
    configured = {
        "main": os.environ.get("OUROBOROS_MODEL", ""),
        "code": os.environ.get("OUROBOROS_MODEL_CODE", ""),
        "light": os.environ.get("OUROBOROS_MODEL_LIGHT", ""),
        "fallback": os.environ.get("OUROBOROS_MODEL_FALLBACK", ""),
    }
    for cat, val in configured.items():
        if val and normalized == _normalize_model_identity(val):
            return cat
    return "other"


def emit_llm_usage_event(
    event_queue: Optional[queue.Queue],
    task_id: str,
    model: str,
    usage: Dict[str, Any],
    cost: float,
    category: str = "task",
    provider: Optional[str] = None,
    source: str = "loop",
) -> None:
    """Emit llm_usage event to the event queue.

    Under OAuth, ``cost`` is 0 by design. ``notional_cost`` from the usage
    dict (if present) is forwarded for observability.
    """
    if not event_queue:
        return
    try:
        resolved_provider = provider or (
            "local" if str(model or "").endswith(" (local)") else "claude_code_oauth"
        )
        event = {
            "type": "llm_usage",
            "ts": utc_now_iso(),
            "task_id": task_id,
            "model": model,
            "api_key_type": infer_api_key_type(model, resolved_provider),
            "model_category": infer_model_category(model),
            "provider": resolved_provider,
            "source": source,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "cached_tokens": int(usage.get("cached_tokens") or 0),
            "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
            "cost": cost,
            "cost_estimated": not bool(usage.get("cost")),
            "usage": usage,
            "category": category,
        }
        notional = usage.get("notional_cost")
        if notional is not None:
            event["notional_cost"] = float(notional)
        event_queue.put_nowait(event)
    except Exception:
        log.debug("Failed to put llm_usage event to queue", exc_info=True)
