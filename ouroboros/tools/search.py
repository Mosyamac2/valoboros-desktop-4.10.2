"""Web search tool — Claude built-in WebSearch via claude-agent-sdk."""

from __future__ import annotations

import json
import logging
import os
from typing import List

from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.utils import utc_now_iso

log = logging.getLogger(__name__)

DEFAULT_SEARCH_MODEL = "anthropic/claude-sonnet-4.6"


def _web_search(
    ctx: ToolContext,
    query: str,
    model: str = "",
    search_context_size: str = "",
    reasoning_effort: str = "",
) -> str:
    """Run a Claude WebSearch through the subscription gateway.

    The ``search_context_size`` and ``reasoning_effort`` parameters are kept
    in the tool schema for backwards compatibility but are not forwarded —
    the SDK manages search depth via WebSearch's own heuristics.
    """
    del search_context_size, reasoning_effort

    active_model = model or os.environ.get("OUROBOROS_WEBSEARCH_MODEL", DEFAULT_SEARCH_MODEL)

    try:
        from ouroboros.gateways.claude_code_chat import web_search as _gw_web_search
    except ImportError as e:
        return json.dumps({
            "error": (
                "web_search requires the claude-agent-sdk package. "
                f"Install with: pip install claude-agent-sdk>=0.1.50. ({e})"
            ),
        })

    if hasattr(ctx, "emit_progress_fn") and ctx.emit_progress_fn:
        try:
            ctx.emit_progress_fn(f"🔍 Searching: {query[:100]}")
        except Exception:
            pass

    try:
        text, usage = _gw_web_search(query_text=query, model=active_model)
    except Exception as e:
        log.warning("Claude WebSearch failed: %s", e, exc_info=True)
        return json.dumps({"error": f"Claude WebSearch failed: {repr(e)}"}, ensure_ascii=False)

    if hasattr(ctx, "pending_events"):
        try:
            ctx.pending_events.append({
                "type": "llm_usage",
                "provider": str(usage.get("provider") or "claude_code_oauth"),
                "model": active_model,
                "api_key_type": str(usage.get("provider") or "claude_code_oauth"),
                "model_category": "websearch",
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "usage": usage,
                "cost": float(usage.get("cost") or 0.0),
                "notional_cost": float(usage.get("notional_cost") or 0.0),
                "source": "web_search",
                "ts": utc_now_iso(),
                "category": "task",
            })
        except Exception:
            log.debug("Failed to emit web_search cost event", exc_info=True)

    return json.dumps({"answer": text or "(no answer)"}, ensure_ascii=False, indent=2)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("web_search", {
            "name": "web_search",
            "description": (
                "Search the web via Claude's built-in WebSearch tool through the "
                "subscription gateway. Returns a synthesized answer with citations. "
                f"Default model: {DEFAULT_SEARCH_MODEL}. Override via OUROBOROS_WEBSEARCH_MODEL."
            ),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "Search query"},
                "model": {"type": "string", "description": f"Claude model id (default: {DEFAULT_SEARCH_MODEL})"},
                "search_context_size": {"type": "string", "enum": ["low", "medium", "high"],
                                        "description": "Ignored (kept for compat)."},
                "reasoning_effort": {"type": "string", "enum": ["low", "medium", "high"],
                                     "description": "Ignored (kept for compat)."},
            }, "required": ["query"]},
        }, _web_search, timeout_sec=540),
    ]
