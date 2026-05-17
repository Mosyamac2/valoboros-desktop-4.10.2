"""
Ouroboros — LLM client (subscription-only).

After the OAuth migration this module is a thin facade. The cloud LLM
transport lives in `ouroboros/gateways/claude_code_chat.py` and runs against
an Anthropic subscription via `CLAUDE_CODE_OAUTH_TOKEN`. Per-token providers
(OpenRouter / direct OpenAI / direct Anthropic / Cloud.ru / openai-compatible)
have been removed. The local llama-cpp-python path remains as a manual
fallback when the subscription is unavailable or `USE_LOCAL_*=true`.

Public contract preserved for the rest of the codebase:
  - LLMClient().chat(...)            sync chat completion
  - LLMClient().chat_async(...)      async chat completion
  - LLMClient().vision_query(...)    image+prompt query
  - LLMClient().default_model()      string id
  - LLMClient().available_models()   list of ids
  - add_usage(total, usage)          aggregator
  - normalize_reasoning_effort(...)
  - reasoning_rank(...)
  - LocalContextTooLargeError
  - DEFAULT_LIGHT_MODEL
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

DEFAULT_LIGHT_MODEL = "anthropic/claude-sonnet-4.6"


class LocalContextTooLargeError(RuntimeError):
    """Raised when a local model cannot fit context without silent truncation."""


# ---------------------------------------------------------------------------
# Reasoning effort helpers (used by config + tools)
# ---------------------------------------------------------------------------

def normalize_reasoning_effort(value: str, default: str = "medium") -> str:
    allowed = {"none", "minimal", "low", "medium", "high", "xhigh"}
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def reasoning_rank(value: str) -> int:
    order = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5}
    return int(order.get(str(value or "").strip().lower(), 3))


def add_usage(total: Dict[str, Any], usage: Dict[str, Any]) -> None:
    """Accumulate usage from one LLM call into a running total."""
    for k in ("prompt_tokens", "completion_tokens", "total_tokens",
              "cached_tokens", "cache_write_tokens"):
        total[k] = int(total.get(k) or 0) + int(usage.get(k) or 0)
    if usage.get("cost"):
        total["cost"] = float(total.get("cost") or 0) + float(usage["cost"])
    if usage.get("notional_cost"):
        total["notional_cost"] = float(total.get("notional_cost") or 0) + float(usage["notional_cost"])


# ---------------------------------------------------------------------------
# Local-model context-management helpers (kept for the llama-cpp fallback)
# ---------------------------------------------------------------------------

def _estimate_message_chars(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            total += sum(
                len(str(block.get("text", "")))
                for block in content if isinstance(block, dict)
            )
        else:
            total += len(str(content or ""))
    return total


def _split_markdown_sections(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    lines = str(text or "").splitlines()
    preamble: List[str] = []
    sections: List[Tuple[str, str]] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []
    for line in lines:
        if line.startswith("## "):
            if current_title is None:
                preamble = current_lines[:]
            else:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = line[3:].strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_title is None:
        return "\n".join(lines).strip(), []
    sections.append((current_title, "\n".join(current_lines).strip()))
    return "\n".join(preamble).strip(), sections


def _compact_markdown_sections(text: str, preserve_titles: Set[str], reason: str) -> str:
    preamble, sections = _split_markdown_sections(text)
    if not sections:
        return text
    parts: List[str] = []
    if preamble:
        parts.append(preamble)
    for title, section in sections:
        if title in preserve_titles:
            parts.append(section)
            continue
        omitted_chars = max(0, len(section))
        parts.append(
            f"## {title}\n\n"
            f"[Compacted for local-model context: omitted {omitted_chars} chars. {reason}]"
        )
    return "\n\n".join(p for p in parts if p).strip()


def _compact_local_static_text(text: str) -> str:
    return _compact_markdown_sections(
        text,
        preserve_titles={"BIBLE.md"},
        reason="Use a larger-context model or read the source file directly if this section becomes necessary.",
    )


def _compact_local_semi_stable_text(text: str) -> str:
    return _compact_markdown_sections(
        text,
        preserve_titles={"Scratchpad", "Identity"},
        reason="Scratchpad and Identity were preserved; non-core memory sections were compacted for local execution.",
    )


def _compact_local_dynamic_text(text: str) -> str:
    return _compact_markdown_sections(
        text,
        preserve_titles={"Drive state", "Runtime context", "Health Invariants"},
        reason="Recent/history-heavy sections were compacted for local execution.",
    )


def _compact_local_system_text(text: str) -> str:
    return _compact_markdown_sections(
        text,
        preserve_titles={
            "BIBLE.md", "Scratchpad", "Identity", "Drive state",
            "Runtime context", "Health Invariants", "Recent observations",
            "Background consciousness info",
        },
        reason="Non-core sections were compacted for local execution.",
    )


# ---------------------------------------------------------------------------
# LLMClient — subscription-only facade
# ---------------------------------------------------------------------------

class LLMClient:
    """Subscription-only LLM client.

    Cloud calls go through `ouroboros.gateways.claude_code_chat` against an
    Anthropic OAuth subscription token. Local calls go to the llama-cpp
    server when `use_local=True` (or the per-call USE_LOCAL_* override
    selects the local backend).
    """

    def __init__(self, api_key: Optional[str] = None, base_url: str = ""):
        # api_key / base_url retained as parameters for backward compatibility
        # but ignored. The OAuth token is read from CLAUDE_CODE_OAUTH_TOKEN.
        del api_key, base_url
        self._local_client = None
        self._local_port: Optional[int] = None

    # ------------------------------------------------------------------
    # Local llama-cpp client
    # ------------------------------------------------------------------

    def _get_local_client(self):
        port = int(os.environ.get("LOCAL_MODEL_PORT", "8766"))
        if self._local_client is None or self._local_port != port:
            from openai import OpenAI
            self._local_client = OpenAI(
                base_url=f"http://127.0.0.1:{port}/v1",
                api_key="local",
                max_retries=0,
            )
            self._local_port = port
        return self._local_client

    # ------------------------------------------------------------------
    # Public chat API
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        reasoning_effort: str = "medium",
        max_tokens: int = 16384,
        tool_choice: str = "auto",
        use_local: bool = False,
        temperature: Optional[float] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Single LLM call. Returns: (response_message, usage_dict)."""
        if use_local:
            return self._chat_local(messages, tools, max_tokens, tool_choice)
        return self._chat_subscription(
            messages, model, tools, reasoning_effort,
            max_tokens, tool_choice, temperature,
        )

    async def chat_async(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        reasoning_effort: str = "medium",
        max_tokens: int = 16384,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Async chat call. Tool calls supported via prompt emulation."""
        from ouroboros.gateways.claude_code_chat import chat_async as _gw_chat_async
        return await _gw_chat_async(
            messages=messages,
            model=model,
            tools=tools,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            temperature=temperature,
        )

    # ------------------------------------------------------------------
    # Subscription cloud path (delegates to the gateway)
    # ------------------------------------------------------------------

    def _chat_subscription(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]],
        reasoning_effort: str,
        max_tokens: int,
        tool_choice: str,
        temperature: Optional[float],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        from ouroboros.gateways.claude_code_chat import chat as _gw_chat
        return _gw_chat(
            messages=messages,
            model=model,
            tools=tools,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            temperature=temperature,
        )

    # ------------------------------------------------------------------
    # Local llama-cpp path (kept for manual fallback)
    # ------------------------------------------------------------------

    def _prepare_messages_for_local_context(
        self,
        messages: List[Dict[str, Any]],
        ctx_len: int,
        max_tokens: int,
    ) -> List[Dict[str, Any]]:
        available_tokens = max(256, ctx_len - max_tokens - 64)
        target_chars = available_tokens * 3
        total_chars = _estimate_message_chars(messages)
        if total_chars <= target_chars:
            return messages

        compacted = copy.deepcopy(messages)
        for msg in compacted:
            if msg.get("role") != "system":
                continue
            content = msg.get("content")
            if isinstance(content, list):
                for idx, block in enumerate(content):
                    if not isinstance(block, dict) or block.get("type") != "text":
                        continue
                    block_text = str(block.get("text", ""))
                    if idx == 0:
                        block["text"] = _compact_local_static_text(block_text)
                    elif idx == 1:
                        block["text"] = _compact_local_semi_stable_text(block_text)
                    else:
                        block["text"] = _compact_local_dynamic_text(block_text)
            elif isinstance(content, str):
                msg["content"] = _compact_local_system_text(content)
            break

        compacted_chars = _estimate_message_chars(compacted)
        if compacted_chars <= target_chars:
            return compacted
        raise LocalContextTooLargeError(
            f"Local model context too large after safe compaction "
            f"({compacted_chars} chars > target {target_chars})."
        )

    @staticmethod
    def _strip_cache_control(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Strip cache_control hints and flatten tool-role list content."""
        cleaned = copy.deepcopy(messages)
        for msg in cleaned:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            if msg.get("role") == "tool":
                msg["content"] = "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
            else:
                for block in content:
                    if isinstance(block, dict):
                        block.pop("cache_control", None)
        return cleaned

    def _chat_local(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: int,
        tool_choice: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Send a chat request to the local llama-cpp-python server."""
        client = self._get_local_client()
        clean_messages = self._strip_cache_control(messages)

        local_max = min(max_tokens, 2048)
        ctx_len = 0
        try:
            from ouroboros.local_model import get_manager
            ctx_len = get_manager().get_context_length()
            if ctx_len > 0:
                local_max = min(max_tokens, max(256, ctx_len // 4))
        except Exception:
            pass

        if ctx_len > 0:
            clean_messages = self._prepare_messages_for_local_context(
                clean_messages, ctx_len, local_max,
            )

        for msg in clean_messages:
            content = msg.get("content")
            if isinstance(content, list):
                msg["content"] = "\n\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )

        clean_tools = None
        if tools:
            clean_tools = [
                {k: v for k, v in t.items() if k != "cache_control"}
                for t in tools
            ]

        kwargs: Dict[str, Any] = {
            "model": "local-model",
            "messages": clean_messages,
            "max_tokens": local_max,
        }
        if clean_tools:
            kwargs["tools"] = clean_tools
            kwargs["tool_choice"] = tool_choice

        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(**kwargs)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                err = str(exc)
                if "context_length_exceeded" in err:
                    raise LocalContextTooLargeError(err) from exc
                if attempt == 2:
                    log.warning("Local model request failed: %s", exc)
                    raise
                log.warning(
                    "Local model request failed (attempt %d/3): %s",
                    attempt + 1, exc,
                )
                time.sleep(0.5 * (attempt + 1))
        if last_exc is not None:
            raise last_exc

        resp_dict = resp.model_dump()
        usage = resp_dict.get("usage") or {}
        choices = resp_dict.get("choices") or [{}]
        msg = (choices[0] if choices else {}).get("message") or {}

        if not msg.get("tool_calls") and msg.get("content") and clean_tools:
            allowed_tool_names = {
                str(t.get("function", {}).get("name", "")).strip()
                for t in clean_tools if isinstance(t, dict)
            }
            msg = _parse_tool_calls_from_content(msg, allowed_tool_names)

        usage["cost"] = 0.0
        usage["provider"] = "local"
        return msg, usage

    # ------------------------------------------------------------------
    # Vision
    # ------------------------------------------------------------------

    def vision_query(
        self,
        prompt: str,
        images: List[Dict[str, Any]],
        model: str = "anthropic/claude-sonnet-4.6",
        max_tokens: int = 2048,
        reasoning_effort: str = "none",
    ) -> Tuple[str, Dict[str, Any]]:
        """Image+prompt query.

        Builds an OpenAI-shaped multi-part message (text + image_url blocks)
        and routes through ``self.chat()``. The chat path delegates to the
        Claude Code subscription gateway which transports image blocks as
        inline references. We use ``self.chat`` rather than calling the
        gateway directly so callers that monkey-patch ``chat`` for testing
        continue to intercept vision calls.
        """
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            if "url" in img:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": img["url"]},
                })
            elif "base64" in img:
                mime = img.get("mime", "image/png")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img['base64']}"},
                })
            else:
                log.warning("vision_query: skipping image with unknown format: %s", list(img.keys()))

        messages = [{"role": "user", "content": content}]
        response_msg, usage = self.chat(
            messages=messages,
            model=model,
            tools=None,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
        )
        text = response_msg.get("content") or ""
        return text, usage

    # ------------------------------------------------------------------
    # Model discovery
    # ------------------------------------------------------------------

    # Backwards-compat shim: tests call `LLMClient._parse_tool_calls_from_content(...)`.
    # The function moved to module scope in v4.11.0; this attribute keeps the old
    # access path working without changing call sites.
    _parse_tool_calls_from_content = staticmethod(
        lambda msg, allowed=None: _parse_tool_calls_from_content(msg, allowed)
    )

    def default_model(self) -> str:
        return os.environ.get("OUROBOROS_MODEL", "anthropic/claude-opus-4.7")

    def available_models(self) -> List[str]:
        main = os.environ.get("OUROBOROS_MODEL", "anthropic/claude-opus-4.7")
        code = os.environ.get("OUROBOROS_MODEL_CODE", "")
        light = os.environ.get("OUROBOROS_MODEL_LIGHT", "")
        models = [main]
        if code and code != main:
            models.append(code)
        if light and light != main and light != code:
            models.append(light)
        return models


# ---------------------------------------------------------------------------
# Local-model tool-call parsing (preserved for llama-cpp fallback)
# ---------------------------------------------------------------------------

def _parse_tool_calls_from_content(
    msg: Dict[str, Any],
    allowed_tool_names: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Parse <tool_call> XML tags from local-model content into structured tool_calls."""
    content = str(msg.get("content", "") or "")
    stripped = content.strip()
    if not stripped:
        return msg

    full_pattern = re.compile(
        r"^(?:\s*<tool_call>\s*\{.*?\}\s*</tool_call>\s*)+$",
        re.DOTALL,
    )
    if not full_pattern.fullmatch(stripped):
        return msg

    matches = re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", stripped, re.DOTALL)
    if not matches:
        return msg

    allowed = {name for name in (allowed_tool_names or set()) if name}
    tool_calls = []
    for i, raw in enumerate(matches):
        try:
            raw_stripped = raw.strip()
            try:
                obj = json.loads(raw_stripped)
            except json.JSONDecodeError:
                if raw_stripped.startswith("{{") and raw_stripped.endswith("}}"):
                    obj = json.loads(raw_stripped[1:-1])
                else:
                    raise
            if not isinstance(obj, dict):
                raise ValueError("tool_call payload must be an object")
            name = str(obj.get("name", "")).strip()
            args = obj.get("arguments", {})
            if not name:
                raise ValueError("tool_call missing function name")
            if allowed and name not in allowed:
                raise ValueError(f"unknown tool '{name}'")
            if not isinstance(args, dict):
                raise ValueError("tool_call arguments must be an object")
            tool_calls.append({
                "id": f"call_local_{i}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args),
                },
            })
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("Rejected local <tool_call> block: %s (%s)", raw[:200], exc)
            return msg

    if not tool_calls:
        return msg

    msg = dict(msg)
    msg["tool_calls"] = tool_calls
    msg["content"] = None
    return msg


# ---------------------------------------------------------------------------
# Backwards-compat shim: fetch_openrouter_pricing returns empty (removed)
# ---------------------------------------------------------------------------

def fetch_openrouter_pricing() -> Dict[str, Tuple[float, float, float]]:
    """Legacy stub. OpenRouter pricing no longer fetched."""
    return {}
