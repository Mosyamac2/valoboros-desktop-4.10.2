"""Claude Agent SDK gateway.

Thin adapter wrapping the `claude-agent-sdk` Python package.
Provides two execution paths:
  - edit mode: code editing with safety guards (PreToolUse hooks)
  - read-only mode: advisory review (Read/Grep/Glob only)

This is pure transport — no business logic, no git ops, no validation.
Orchestration (context loading, git stat, validation) lives in callers.

Safety model:
  1. SDK-level: allowed_tools, disallowed_tools, permission_mode,
     PreToolUse hooks for path guards
  2. Post-edit revert (registry.py) remains as defense-in-depth

Raises ImportError early if claude-agent-sdk is not installed,
so callers can fall back to CLI subprocess.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Import SDK eagerly — ImportError propagates to callers for fallback
from claude_agent_sdk import (  # noqa: E402
    ClaudeAgentOptions, ClaudeSDKClient, HookMatcher,
    AssistantMessage, ResultMessage, query,
)

# Safety-critical files (mirrors registry.py SAFETY_CRITICAL_PATHS)
SAFETY_CRITICAL = frozenset([
    "BIBLE.md",
    "ouroboros/safety.py",
    "ouroboros/tools/registry.py",
    "prompts/SAFETY.md",
])


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ClaudeCodeResult:
    """Structured result from a Claude Agent SDK invocation."""

    success: bool
    result_text: str = ""
    session_id: str = ""
    cost_usd: float = 0.0
    usage: Dict[str, int] = field(default_factory=dict)
    error: str = ""
    # Populated by callers after invocation, not by the gateway
    changed_files: List[str] = field(default_factory=list)
    diff_stat: str = ""
    validation_summary: str = ""

    def to_tool_output(self) -> str:
        """Format as structured JSON for the tool response."""
        out: Dict[str, Any] = {
            "success": self.success,
            "result": self.result_text,
        }
        if self.session_id:
            out["session_id"] = self.session_id
        if self.cost_usd:
            out["cost_usd"] = round(self.cost_usd, 6)
        if self.usage:
            out["usage"] = self.usage
        if self.changed_files:
            out["changed_files"] = self.changed_files
        if self.diff_stat:
            out["diff_stat"] = self.diff_stat
        if self.error:
            out["error"] = self.error
        if self.validation_summary:
            out["validation"] = self.validation_summary
        return json.dumps(out, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# PreToolUse hook: path safety guard
# ---------------------------------------------------------------------------

def make_path_guard(cwd: str):
    """Create a PreToolUse hook that blocks writes outside cwd and to safety-critical files."""
    cwd_resolved = pathlib.Path(cwd).resolve()

    async def path_guard(input_data: dict, tool_use_id: str, context: Any) -> dict:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Only guard mutating tools
        if tool_name not in ("Edit", "Write", "MultiEdit"):
            return {}

        # Extract file path from tool input
        file_path = tool_input.get("file_path", "") or tool_input.get("path", "")
        if not file_path:
            return {}

        # Resolve the target path
        target = pathlib.Path(file_path)
        if not target.is_absolute():
            target = cwd_resolved / target
        target = target.resolve()

        # Check: outside cwd?
        try:
            target.relative_to(cwd_resolved)
        except ValueError:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"SAFETY: Write blocked — target path '{file_path}' "
                        f"resolves outside the allowed working directory '{cwd}'."
                    ),
                }
            }

        # Check: safety-critical file?
        rel = os.path.normpath(os.path.relpath(str(target), str(cwd_resolved)))
        if rel in SAFETY_CRITICAL:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"SAFETY: Write blocked — '{rel}' is a safety-critical file. "
                        "These files cannot be modified by delegated edits."
                    ),
                }
            }

        return {}

    return path_guard


def make_readonly_guard():
    """Create a PreToolUse hook that denies ALL mutating tools."""

    async def readonly_guard(input_data: dict, tool_use_id: str, context: Any) -> dict:
        tool_name = input_data.get("tool_name", "")
        if tool_name in ("Edit", "Write", "MultiEdit", "Bash"):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"SAFETY: '{tool_name}' is not allowed in read-only advisory mode. "
                        "Only Read, Grep, Glob are permitted."
                    ),
                }
            }
        return {}

    return readonly_guard


# ---------------------------------------------------------------------------
# Core async runners
# ---------------------------------------------------------------------------

async def _run_edit_async(
    prompt: str,
    cwd: str,
    model: str = "opus",
    max_turns: int = 12,
    budget: Optional[float] = None,
    system_prompt: Optional[str] = None,
) -> ClaudeCodeResult:
    """Run an edit-mode SDK query with safety hooks.

    Uses ClaudeSDKClient because hooks require the client interface.
    """
    path_guard = make_path_guard(cwd)

    options = ClaudeAgentOptions(
        cwd=cwd,
        model=model,
        permission_mode="acceptEdits",
        allowed_tools=["Read", "Edit", "Grep", "Glob"],
        disallowed_tools=["Bash", "MultiEdit"],
        max_turns=max_turns,
        max_budget_usd=budget,
        system_prompt=system_prompt,
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Edit|Write|MultiEdit", hooks=[path_guard]),
            ],
        },
    )

    result = ClaudeCodeResult(success=True)
    text_parts: List[str] = []

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text") and block.text:
                            text_parts.append(block.text)
                elif isinstance(message, ResultMessage):
                    result.session_id = getattr(message, "session_id", "") or ""
                    result.cost_usd = getattr(message, "total_cost_usd", 0) or 0
                    usage = getattr(message, "usage", None)
                    if isinstance(usage, dict):
                        result.usage = usage
                    subtype = getattr(message, "subtype", "")
                    if subtype and subtype != "success":
                        result.success = False
                        result.error = f"Agent ended with subtype: {subtype}"
    except Exception as e:
        result.success = False
        result.error = f"{type(e).__name__}: {e}"

    result.result_text = "\n".join(text_parts) if text_parts else "(no output)"
    return result


async def _run_readonly_async(
    prompt: str,
    cwd: str,
    model: str = "opus",
    max_turns: int = 8,
) -> ClaudeCodeResult:
    """Run a read-only SDK query for advisory review.

    Uses the simpler query() function since no hooks are needed —
    disallowed_tools already blocks mutating operations at the CLI level.
    """
    options = ClaudeAgentOptions(
        cwd=cwd,
        model=model,
        permission_mode="default",  # no auto-approve
        allowed_tools=["Read", "Grep", "Glob"],
        disallowed_tools=["Bash", "Edit", "Write", "MultiEdit"],
        max_turns=max_turns,
    )

    result = ClaudeCodeResult(success=True)
    text_parts: List[str] = []

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text") and block.text:
                        text_parts.append(block.text)
            elif isinstance(message, ResultMessage):
                result.session_id = getattr(message, "session_id", "") or ""
                result.cost_usd = getattr(message, "total_cost_usd", 0) or 0
                usage = getattr(message, "usage", None)
                if isinstance(usage, dict):
                    result.usage = usage
                subtype = getattr(message, "subtype", "")
                if subtype and subtype != "success":
                    result.success = False
                    result.error = f"Agent ended with subtype: {subtype}"
    except Exception as e:
        result.success = False
        result.error = f"{type(e).__name__}: {e}"

    result.result_text = "\n".join(text_parts) if text_parts else "(no output)"
    return result


# ---------------------------------------------------------------------------
# Synchronous entry points (called from tool handlers)
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine from synchronous tool context.

    Worker processes have their own event loops, so asyncio.run() is safe.
    If there's already a running loop (unlikely in workers), falls back
    to creating a new loop in a thread.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        return asyncio.run(coro)
    else:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()


def run_edit(
    prompt: str,
    cwd: str,
    model: str = "opus",
    max_turns: int = 12,
    budget: Optional[float] = None,
    system_prompt: Optional[str] = None,
) -> ClaudeCodeResult:
    """Synchronous entry point for edit-mode SDK.

    Raises ImportError if claude-agent-sdk is not installed (caught at
    module level import above).
    """
    return _run_async(_run_edit_async(
        prompt=prompt,
        cwd=cwd,
        model=model,
        max_turns=max_turns,
        budget=budget,
        system_prompt=system_prompt,
    ))


def run_readonly(
    prompt: str,
    cwd: str,
    model: str = "opus",
    max_turns: int = 8,
) -> ClaudeCodeResult:
    """Synchronous entry point for read-only advisory review."""
    return _run_async(_run_readonly_async(
        prompt=prompt,
        cwd=cwd,
        model=model,
        max_turns=max_turns,
    ))
