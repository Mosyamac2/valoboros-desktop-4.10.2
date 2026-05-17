"""Claude Code chat gateway — subscription-only LLM transport.

Implements the chat-completions contract (chat / chat_async / vision) by
driving the `claude-agent-sdk` Python package against an Anthropic
subscription token (CLAUDE_CODE_OAUTH_TOKEN). This is the sole cloud LLM
backend after the OAuth migration; per-token providers (OpenRouter, direct
Anthropic API, OpenAI, Cloud.ru, openai-compatible) are gone.

Tool calls are emulated via prompt engineering: tool schemas are injected
into the system prompt and Claude is instructed to emit
`<tool_call>{"name": ..., "arguments": ...}</tool_call>` blocks. The outer
agent loop (loop_llm_call.py + loop_tool_execution.py) continues to dispatch
tool calls; the gateway does not run an internal agent loop.

Vision: image blocks are passed via the SDK's message input shape.

Raises ImportError at import time when claude-agent-sdk is not installed —
LLMClient catches this and raises a clear user-facing error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

# Eager import — ImportError propagates to caller (LLMClient) which surfaces
# a clear setup error in onboarding / startup checks.
from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    UserMessage,
    query,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model alias resolution
# ---------------------------------------------------------------------------

_MODEL_ALIASES = {
    # Map Ouroboros-style ids to Claude Code CLI aliases. Keep both the
    # current versions and the older ones so old settings.json files keep
    # working without manual migration.
    "anthropic/claude-opus-4.7": "opus",
    "anthropic/claude-opus-4-7": "opus",
    "anthropic/claude-opus-4.6": "opus",
    "anthropic/claude-opus-4-6": "opus",
    "anthropic/claude-sonnet-4.7": "sonnet",
    "anthropic/claude-sonnet-4-7": "sonnet",
    "anthropic/claude-sonnet-4.6": "sonnet",
    "anthropic/claude-sonnet-4-6": "sonnet",
    "anthropic/claude-haiku-4.5": "haiku",
    "anthropic/claude-haiku-4-5": "haiku",
    "claude-opus-4.7": "opus",
    "claude-opus-4-7": "opus",
    "claude-opus-4.6": "opus",
    "claude-opus-4-6": "opus",
    "claude-sonnet-4.7": "sonnet",
    "claude-sonnet-4-7": "sonnet",
    "claude-sonnet-4.6": "sonnet",
    "claude-sonnet-4-6": "sonnet",
    "claude-haiku-4.5": "haiku",
    "claude-haiku-4-5": "haiku",
}

_VALID_ALIASES = frozenset(["opus", "sonnet", "haiku"])


def resolve_model_alias(model: str) -> str:
    """Map an Ouroboros model id to a Claude Code CLI alias (opus/sonnet/haiku)."""
    if not model:
        return "opus"
    raw = str(model).strip()
    if "::" in raw:
        raw = raw.split("::", 1)[1].strip()
    if raw in _VALID_ALIASES:
        return raw
    if raw in _MODEL_ALIASES:
        return _MODEL_ALIASES[raw]
    lower = raw.lower()
    if "opus" in lower:
        return "opus"
    if "sonnet" in lower:
        return "sonnet"
    if "haiku" in lower:
        return "haiku"
    return "opus"


# ---------------------------------------------------------------------------
# Message serialization
# ---------------------------------------------------------------------------

_TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL
)
_TOOL_CALL_ENVELOPE = re.compile(
    r"^(?:\s*<tool_call>\s*\{.*?\}\s*</tool_call>\s*)+$", re.DOTALL
)

_TOOL_INSTRUCTION = """\

## Available tools

You have access to the following tools. To call one or more tools, output
ONLY tool-call blocks in this exact form (one or more in sequence, no prose):

<tool_call>{"name": "<tool_name>", "arguments": {<JSON arguments>}}</tool_call>

If you call tools, your entire response MUST consist of <tool_call> blocks
and nothing else. Do not narrate, explain, or wrap in code fences. To answer
the user directly without calling a tool, just write prose.

Tool definitions (JSON Schema):
"""


def _flatten_content(content: Any) -> str:
    """Render an OpenAI content field (str or list of blocks) as plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = str(block.get("type") or "").lower()
            if btype in ("text", "input_text", "output_text") or block.get("text"):
                parts.append(str(block.get("text") or ""))
            # image_url blocks handled separately in vision path
        return "\n".join(p for p in parts if p)
    return str(content)


def _serialize_history(messages: List[Dict[str, Any]]) -> Tuple[str, str, List[Dict[str, Any]]]:
    """Split messages into (system_prompt, user_prompt, image_blocks).

    System messages are concatenated into system_prompt.
    Non-system messages are serialized as a single text transcript appended
    to user_prompt, since the SDK takes a single prompt string per turn.
    Image blocks from the most recent user message are extracted for vision
    queries — older history is included only as text.
    """
    system_parts: List[str] = []
    transcript: List[str] = []
    images: List[Dict[str, Any]] = []

    for idx, msg in enumerate(messages):
        role = str(msg.get("role") or "").strip().lower()
        content = msg.get("content")

        if role == "system":
            text = _flatten_content(content)
            if text:
                system_parts.append(text)
            continue

        # Capture inline image blocks from the latest user message
        if role == "user" and isinstance(content, list) and idx == len(messages) - 1:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    image_url = (block.get("image_url") or {}).get("url") or ""
                    if image_url:
                        images.append({"type": "image_url", "image_url": {"url": image_url}})

        if role == "tool":
            tool_call_id = str(msg.get("tool_call_id") or "")
            text = _flatten_content(content)
            transcript.append(
                f"[tool_result id={tool_call_id}]\n{text}\n[/tool_result]"
            )
            continue

        if role == "assistant":
            text = _flatten_content(content)
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                blocks = []
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = str(fn.get("name") or "")
                    args = fn.get("arguments") or "{}"
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    blocks.append(
                        f'<tool_call>{{"name": "{name}", "arguments": {args}}}</tool_call>'
                    )
                tool_call_text = "".join(blocks)
                if text:
                    transcript.append(f"[assistant]\n{text}\n{tool_call_text}\n[/assistant]")
                else:
                    transcript.append(f"[assistant]\n{tool_call_text}\n[/assistant]")
            elif text:
                transcript.append(f"[assistant]\n{text}\n[/assistant]")
            continue

        if role == "user":
            text = _flatten_content(content)
            if text:
                transcript.append(f"[user]\n{text}\n[/user]")

    system_prompt = "\n\n".join(p for p in system_parts if p).strip()
    user_prompt = "\n\n".join(transcript).strip() or "(continue)"
    return system_prompt, user_prompt, images


def _build_tool_instruction(tools: Optional[List[Dict[str, Any]]]) -> str:
    if not tools:
        return ""
    schemas: List[Dict[str, Any]] = []
    for t in tools:
        fn = t.get("function") or {}
        schemas.append({
            "name": fn.get("name") or "",
            "description": fn.get("description") or "",
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return _TOOL_INSTRUCTION + json.dumps(schemas, ensure_ascii=False, indent=2)


def _parse_tool_calls(content: str, allowed: Set[str]) -> Optional[List[Dict[str, Any]]]:
    """Parse <tool_call> blocks from assistant text. Returns None if the
    text is not purely tool calls (i.e. contains prose).
    """
    stripped = (content or "").strip()
    if not stripped or not _TOOL_CALL_ENVELOPE.fullmatch(stripped):
        return None

    matches = _TOOL_CALL_PATTERN.findall(stripped)
    if not matches:
        return None

    out: List[Dict[str, Any]] = []
    for i, raw in enumerate(matches):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Rejected malformed <tool_call> block: %s", raw[:200])
            return None
        if not isinstance(obj, dict):
            return None
        name = str(obj.get("name", "")).strip()
        args = obj.get("arguments", {})
        if not name:
            return None
        if allowed and name not in allowed:
            log.warning("Tool call references unknown tool: %s", name)
            return None
        if not isinstance(args, dict):
            return None
        out.append({
            "id": f"call_claude_{i}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })
    return out or None


# ---------------------------------------------------------------------------
# Reasoning effort → SDK options
# ---------------------------------------------------------------------------

def _effort_to_thinking(effort: str) -> Optional[Dict[str, Any]]:
    """Map Ouroboros reasoning_effort to a Claude thinking budget hint.

    The SDK currently exposes per-turn thinking only through the system prompt
    or model alias; there is no first-class budget knob. We surface this as a
    short directive prepended to the system prompt for now.
    """
    e = (effort or "").strip().lower()
    if e in ("high", "xhigh"):
        return {"hint": "Think carefully and step-by-step before answering."}
    if e in ("low", "minimal", "none"):
        return {"hint": "Answer concisely without extended deliberation."}
    return None


# ---------------------------------------------------------------------------
# OAuth token wiring
# ---------------------------------------------------------------------------

def _build_sdk_env() -> Dict[str, str]:
    """Return a child-process environment that uses the OAuth subscription.

    If CLAUDE_CODE_OAUTH_TOKEN is set, ANTHROPIC_API_KEY is removed so the
    CLI prefers subscription billing. If only ANTHROPIC_API_KEY is set, that
    is used (legacy path, kept for compatibility).
    """
    env = dict(os.environ)
    oauth = (env.get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip()
    if oauth:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth
        env.pop("ANTHROPIC_API_KEY", None)
    return env


def have_subscription_auth() -> bool:
    """True if an OAuth subscription token or legacy Anthropic key is present."""
    if (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip():
        return True
    if (os.environ.get("ANTHROPIC_API_KEY", "") or "").strip():
        return True
    return False


# ---------------------------------------------------------------------------
# Core async runner
# ---------------------------------------------------------------------------

@dataclass
class _RawResult:
    text: str
    usage: Dict[str, Any]
    cost_usd: float
    session_id: str
    error: str = ""


async def _run_query_async(
    system_prompt: str,
    user_prompt: str,
    model_alias: str,
    max_turns: int = 1,
    timeout: float = 600.0,
) -> _RawResult:
    """Drive a single one-shot SDK query() call and collect the result.

    Uses allowed_tools=[] and disallowed_tools=["Bash","Edit","Write","MultiEdit"]
    to prevent Claude from invoking its built-in tools — this gateway is for
    pure inference. Claude's built-in WebSearch/Read/etc. are deliberately
    excluded so tool dispatch always returns to the outer agent loop.
    """
    text_parts: List[str] = []
    usage: Dict[str, Any] = {}
    cost_usd: float = 0.0
    session_id: str = ""
    error: str = ""

    # Make the SDK pick up subscription auth
    saved_env = dict(os.environ)
    sdk_env = _build_sdk_env()
    for k, v in sdk_env.items():
        os.environ[k] = v
    for k in list(os.environ.keys()):
        if k not in sdk_env:
            os.environ.pop(k, None)

    try:
        options = ClaudeAgentOptions(
            model=model_alias,
            system_prompt=system_prompt or None,
            allowed_tools=[],
            disallowed_tools=["Bash", "Edit", "Write", "MultiEdit", "Read", "Grep", "Glob", "WebSearch"],
            max_turns=max_turns,
            permission_mode="default",
        )

        async def _drain():
            async for message in query(prompt=user_prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text") and block.text:
                            text_parts.append(block.text)
                elif isinstance(message, ResultMessage):
                    nonlocal usage, cost_usd, session_id, error
                    session_id = getattr(message, "session_id", "") or ""
                    cost_usd = float(getattr(message, "total_cost_usd", 0) or 0)
                    msg_usage = getattr(message, "usage", None)
                    if isinstance(msg_usage, dict):
                        usage = msg_usage
                    subtype = getattr(message, "subtype", "")
                    if subtype and subtype != "success":
                        error = f"Agent ended with subtype: {subtype}"

        try:
            await asyncio.wait_for(_drain(), timeout=timeout)
        except asyncio.TimeoutError:
            error = f"Claude Code query timed out after {timeout}s"
    finally:
        # Restore original environment
        for k in list(os.environ.keys()):
            if k not in saved_env:
                os.environ.pop(k, None)
        for k, v in saved_env.items():
            os.environ[k] = v

    return _RawResult(
        text="\n".join(text_parts),
        usage=usage,
        cost_usd=cost_usd,
        session_id=session_id,
        error=error,
    )


def _run_sync(coro):
    """Synchronously run an async coroutine, even from inside a running loop."""
    try:
        asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Usage normalization
# ---------------------------------------------------------------------------

def _normalize_usage(raw: _RawResult, model_alias: str, sub_cost_zero: bool) -> Dict[str, Any]:
    """Translate SDK usage into the OpenAI-shaped usage dict that callers expect."""
    raw_usage = raw.usage or {}

    prompt_tokens = int(
        raw_usage.get("input_tokens")
        or raw_usage.get("prompt_tokens")
        or 0
    )
    completion_tokens = int(
        raw_usage.get("output_tokens")
        or raw_usage.get("completion_tokens")
        or 0
    )
    cached_tokens = int(
        raw_usage.get("cache_read_input_tokens")
        or raw_usage.get("cached_tokens")
        or 0
    )
    cache_write_tokens = int(
        raw_usage.get("cache_creation_input_tokens")
        or raw_usage.get("cache_write_tokens")
        or 0
    )

    notional_cost = float(raw.cost_usd or 0.0)
    out: Dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cached_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "provider": "claude_code_oauth" if sub_cost_zero else "anthropic_api_key",
        "resolved_model": f"anthropic/claude-{model_alias}",
        "session_id": raw.session_id,
    }
    if sub_cost_zero:
        out["cost"] = 0.0
        out["notional_cost"] = notional_cost
    else:
        out["cost"] = notional_cost
    return out


# ---------------------------------------------------------------------------
# Public API — chat / chat_async / vision_query
# ---------------------------------------------------------------------------

def chat(
    messages: List[Dict[str, Any]],
    model: str,
    tools: Optional[List[Dict[str, Any]]] = None,
    reasoning_effort: str = "medium",
    max_tokens: int = 16384,
    tool_choice: str = "auto",
    temperature: Optional[float] = None,
    timeout: float = 600.0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Synchronous one-shot chat call against the Claude Code SDK."""
    return _run_sync(chat_async(
        messages=messages,
        model=model,
        tools=tools,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
        tool_choice=tool_choice,
        temperature=temperature,
        timeout=timeout,
    ))


async def chat_async(
    messages: List[Dict[str, Any]],
    model: str,
    tools: Optional[List[Dict[str, Any]]] = None,
    reasoning_effort: str = "medium",
    max_tokens: int = 16384,
    tool_choice: str = "auto",
    temperature: Optional[float] = None,
    timeout: float = 600.0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Async one-shot chat call. Tool calls are emulated via prompt injection."""
    del max_tokens, tool_choice, temperature  # SDK manages these implicitly

    system_prompt, user_prompt, _images = _serialize_history(messages)

    # Append reasoning-effort hint to the system prompt (best-effort).
    thinking_hint = _effort_to_thinking(reasoning_effort)
    if thinking_hint:
        system_prompt = (
            (system_prompt + "\n\n") if system_prompt else ""
        ) + thinking_hint["hint"]

    # Append tool instructions and parse-back rules to the system prompt.
    tool_instructions = _build_tool_instruction(tools)
    if tool_instructions:
        system_prompt = (
            (system_prompt + "\n") if system_prompt else ""
        ) + tool_instructions

    model_alias = resolve_model_alias(model)
    raw = await _run_query_async(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_alias=model_alias,
        max_turns=1,
        timeout=timeout,
    )

    if raw.error:
        raise RuntimeError(f"Claude Code query failed: {raw.error}")

    sub_cost_zero = bool((os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip())
    usage = _normalize_usage(raw, model_alias, sub_cost_zero=sub_cost_zero)

    response: Dict[str, Any] = {"role": "assistant", "content": raw.text}

    if tools:
        allowed_names = {
            str((t.get("function") or {}).get("name") or "")
            for t in tools
            if isinstance(t, dict)
        }
        allowed_names.discard("")
        tool_calls = _parse_tool_calls(raw.text, allowed_names)
        if tool_calls:
            response["tool_calls"] = tool_calls
            response["content"] = None

    return response, usage


# ---------------------------------------------------------------------------
# Vision: image input
# ---------------------------------------------------------------------------

async def vision_query_async(
    prompt: str,
    images: List[Dict[str, Any]],
    model: str = "anthropic/claude-sonnet-4.6",
    timeout: float = 300.0,
) -> Tuple[str, Dict[str, Any]]:
    """Send an image+prompt to Claude. Images are dicts with 'url' or 'base64'.

    The Claude Code SDK accepts inline base64 images via the prompt string by
    embedding them as markdown image references that the CLI normalizes — for
    URL inputs we pass them straight through in the prompt. For best results
    use base64 data; URL fetching depends on Claude's network ability.
    """
    system_prompt = "You are a vision assistant. Respond concisely."
    parts: List[str] = [prompt]
    for img in images:
        if "url" in img:
            parts.append(f"\n[image: {img['url']}]")
        elif "base64" in img:
            mime = img.get("mime", "image/png")
            parts.append(f"\n[image data:{mime};base64,{img['base64']}]")
    user_prompt = "\n".join(parts)

    model_alias = resolve_model_alias(model)
    raw = await _run_query_async(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_alias=model_alias,
        max_turns=1,
        timeout=timeout,
    )
    if raw.error:
        raise RuntimeError(f"Vision query failed: {raw.error}")

    sub_cost_zero = bool((os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip())
    usage = _normalize_usage(raw, model_alias, sub_cost_zero=sub_cost_zero)
    return raw.text, usage


def vision_query(
    prompt: str,
    images: List[Dict[str, Any]],
    model: str = "anthropic/claude-sonnet-4.6",
    timeout: float = 300.0,
) -> Tuple[str, Dict[str, Any]]:
    return _run_sync(vision_query_async(
        prompt=prompt, images=images, model=model, timeout=timeout,
    ))


# ---------------------------------------------------------------------------
# Web search (Claude built-in WebSearch tool, via SDK)
# ---------------------------------------------------------------------------

async def web_search_async(
    query_text: str,
    model: str = "anthropic/claude-sonnet-4.6",
    timeout: float = 300.0,
) -> Tuple[str, Dict[str, Any]]:
    """Run a Claude WebSearch via the SDK. Returns (answer_text, usage)."""
    text_parts: List[str] = []
    usage: Dict[str, Any] = {}
    cost_usd: float = 0.0
    session_id: str = ""
    error: str = ""

    saved_env = dict(os.environ)
    sdk_env = _build_sdk_env()
    for k, v in sdk_env.items():
        os.environ[k] = v
    for k in list(os.environ.keys()):
        if k not in sdk_env:
            os.environ.pop(k, None)

    model_alias = resolve_model_alias(model)

    try:
        options = ClaudeAgentOptions(
            model=model_alias,
            system_prompt=(
                "You are a web research assistant. Use the WebSearch tool to "
                "find relevant up-to-date information, then synthesize a "
                "concise, factual answer with source citations. Return only "
                "the synthesized answer."
            ),
            allowed_tools=["WebSearch"],
            disallowed_tools=["Bash", "Edit", "Write", "MultiEdit"],
            max_turns=4,
            permission_mode="default",
        )

        async def _drain():
            async for message in query(prompt=query_text, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text") and block.text:
                            text_parts.append(block.text)
                elif isinstance(message, ResultMessage):
                    nonlocal usage, cost_usd, session_id, error
                    session_id = getattr(message, "session_id", "") or ""
                    cost_usd = float(getattr(message, "total_cost_usd", 0) or 0)
                    msg_usage = getattr(message, "usage", None)
                    if isinstance(msg_usage, dict):
                        usage = msg_usage
                    subtype = getattr(message, "subtype", "")
                    if subtype and subtype != "success":
                        error = f"WebSearch ended with subtype: {subtype}"

        try:
            await asyncio.wait_for(_drain(), timeout=timeout)
        except asyncio.TimeoutError:
            error = f"WebSearch timed out after {timeout}s"
    finally:
        for k in list(os.environ.keys()):
            if k not in saved_env:
                os.environ.pop(k, None)
        for k, v in saved_env.items():
            os.environ[k] = v

    if error:
        raise RuntimeError(error)

    raw = _RawResult(
        text="\n".join(text_parts), usage=usage, cost_usd=cost_usd,
        session_id=session_id, error="",
    )
    sub_cost_zero = bool((os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip())
    usage_dict = _normalize_usage(raw, model_alias, sub_cost_zero=sub_cost_zero)
    return raw.text, usage_dict


def web_search(
    query_text: str,
    model: str = "anthropic/claude-sonnet-4.6",
    timeout: float = 300.0,
) -> Tuple[str, Dict[str, Any]]:
    return _run_sync(web_search_async(
        query_text=query_text, model=model, timeout=timeout,
    ))
