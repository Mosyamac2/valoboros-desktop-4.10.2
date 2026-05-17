"""Agentic validation runner — Plan v2.

For each bundle, drive a Claude Code SDK session through four phases:

  Phase A — methodology design       → ./methodology/methodology.md
  Phase B — Python project authoring → ./methodology/validation_project/
  Phase C — execution + interpretation → ./results/results.json + interpretation.md
  Phase D — report prettification    → ./results/report.md

Phase 1 of the v2 plan implements **Phase A only** (skeleton). Subsequent
sub-phases land B/C/D. Phases B-D raise ``NotImplementedError`` for now so
that misuse is loud, not silent.

Each phase is its own ``ClaudeSDKClient`` session — separate transcript file,
separate budget accounting, separate reset semantics. The system prompt is
shared (built once per bundle) so prompt caching keeps the cost amortized.

Safety:
  - ``PreToolUse`` hook from :mod:`ouroboros.gateways.claude_code` blocks
    writes outside the bundle's cwd and to the SAFETY-CRITICAL files.
  - ``allowed_tools`` is fixed (no MultiEdit, no NotebookEdit).
  - Tool budget per phase is hard-capped via ``max_turns``.

Auditability:
  - Every assistant message is appended to
    ``{bundle_dir}/_agentic_transcripts/phase_<x>.jsonl``.
  - Full ``AgenticValidationResult`` is written to
    ``{bundle_dir}/_agentic_transcripts/result.json`` at session end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ouroboros.validation.agentic_prompts import load_phase_prompt
from ouroboros.validation.agentic_system_prompt import build_validator_system_prompt

# Safety-critical files (mirror of registry.py SAFETY_CRITICAL_PATHS).
# Kept inline so the agentic runner doesn't have to import the gateway —
# the gateway eagerly imports claude_agent_sdk, which is OK at runtime but
# breaks any unit test on a machine where the SDK isn't installed.
_SAFETY_CRITICAL = frozenset([
    "BIBLE.md",
    "ouroboros/safety.py",
    "ouroboros/tools/registry.py",
    "prompts/SAFETY.md",
    "ouroboros/validation/sandbox.py",
])
from ouroboros.validation.types import (
    AgenticPhaseResult,
    AgenticValidationResult,
    ValidationConfig,
)

log = logging.getLogger(__name__)


# Per-phase tool / turn budgets (§4 of closed_feedback_loop_plan.md).
# These are deliberately generous — the cap is meaningful only when something
# pathological happens (infinite Read/Grep loop).
_PHASE_BUDGETS = {
    "A": {"max_turns": 25, "allowed_tools": ["Read", "Glob", "Grep", "Write"]},
    "B": {"max_turns": 60, "allowed_tools": ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]},
    "C": {"max_turns": 25, "allowed_tools": ["Read", "Write", "Bash", "Glob", "Grep"]},
    "D": {"max_turns": 10, "allowed_tools": ["Read", "Write"]},
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_bundle_path_guard(cwd: str):
    """PreToolUse hook factory: deny writes outside ``cwd`` or to safety-critical files.

    Mirrors :func:`ouroboros.gateways.claude_code.make_path_guard` but lives in
    the validation package so we don't trigger the gateway's eager
    ``claude-agent-sdk`` import during unit tests.
    """
    cwd_resolved = Path(cwd).resolve()

    async def path_guard(input_data: dict, tool_use_id: str, context: Any) -> dict:
        tool_name = input_data.get("tool_name", "")
        if tool_name not in ("Edit", "Write", "MultiEdit"):
            return {}
        tool_input = input_data.get("tool_input", {})
        file_path = tool_input.get("file_path", "") or tool_input.get("path", "")
        if not file_path:
            return {}

        target = Path(file_path)
        if not target.is_absolute():
            target = cwd_resolved / target
        target = target.resolve()

        try:
            target.relative_to(cwd_resolved)
        except ValueError:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"SAFETY: write to {file_path!r} blocked — resolves outside "
                        f"the bundle workdir ({cwd})."
                    ),
                }
            }

        rel = os.path.normpath(os.path.relpath(str(target), str(cwd_resolved)))
        if rel in _SAFETY_CRITICAL:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"SAFETY: {rel} is a safety-critical file and cannot be "
                        "modified by a delegated session."
                    ),
                }
            }
        return {}

    return path_guard


def _serialise_block(block: Any) -> dict[str, Any]:
    """Best-effort dict view of an SDK content block for the transcript."""
    out: dict[str, Any] = {"type": type(block).__name__}
    for attr in ("text", "tool_use_id", "name", "input", "content", "is_error"):
        if hasattr(block, attr):
            val = getattr(block, attr)
            try:
                json.dumps(val)
                out[attr] = val
            except (TypeError, ValueError):
                out[attr] = repr(val)[:2000]
    return out


class AgenticValidator:
    """Drive a per-bundle Claude Code SDK session through the four phases.

    Construction is cheap (just paths + config). ``run_phase_a`` /
    ``run_phase_b`` / ``run_phase_c`` / ``run_phase_d`` are independent
    coroutines so callers can run them one at a time and inspect intermediate
    artifacts. ``run`` chains all four.
    """

    def __init__(
        self,
        bundle_id: str,
        bundle_dir: Path | str,
        model_type: str = "unknown",
        config: Optional[ValidationConfig] = None,
        pre_check_summary: Optional[dict[str, Any]] = None,
        knowledge_dir: Optional[Path | str] = None,
        repo_dir: Optional[Path | str] = None,
        phase_b_model: Optional[str] = None,
    ) -> None:
        self.bundle_id = bundle_id
        self.bundle_dir = Path(bundle_dir).resolve()
        self.model_type = model_type or "unknown"
        self.config = config or ValidationConfig()
        self.pre_check_summary = pre_check_summary
        self.knowledge_dir = Path(knowledge_dir) if knowledge_dir else None
        self.repo_dir = Path(repo_dir) if repo_dir else None
        # Phase B is mechanical writing; Sonnet handles it cheaply under
        # subscription rate-limit pressure. Defaults to Opus 4.7 for parity
        # with the other phases. See §8 of closed_feedback_loop_plan.md.
        self.phase_b_model = phase_b_model or os.environ.get(
            "OUROBOROS_VALIDATION_PHASE_B_MODEL", "opus"
        )

        self._transcripts_dir = self.bundle_dir / "_agentic_transcripts"
        self._transcripts_dir.mkdir(parents=True, exist_ok=True)

        # Layout per §3 of the plan.
        (self.bundle_dir / "methodology").mkdir(parents=True, exist_ok=True)
        (self.bundle_dir / "results").mkdir(parents=True, exist_ok=True)

        self._system_prompt: Optional[str] = None

    # ------------------------------------------------------------------
    # System prompt — built lazily, shared across phases
    # ------------------------------------------------------------------

    def system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = build_validator_system_prompt(
                bundle_dir=self.bundle_dir,
                model_type=self.model_type,
                pre_check_summary=self.pre_check_summary,
                knowledge_dir=self.knowledge_dir,
                repo_dir=self.repo_dir,
            )
        return self._system_prompt

    # ------------------------------------------------------------------
    # Phase A
    # ------------------------------------------------------------------

    async def run_phase_a(self) -> AgenticPhaseResult:
        """Phase A — methodology design.

        Reads ``./raw/``. Writes ``./methodology/methodology.md``.
        """
        user_prompt = load_phase_prompt("a")
        return await self._run_phase(
            phase="A",
            user_prompt=user_prompt,
            model="opus",
            expected_outputs=[self.bundle_dir / "methodology" / "methodology.md"],
        )

    # ------------------------------------------------------------------
    # Phase B / C / D placeholders — filled in by phases 2-4 of the plan
    # ------------------------------------------------------------------

    async def run_phase_b(self) -> AgenticPhaseResult:
        raise NotImplementedError(
            "Phase B (project authoring) lands in sub-phase 2 of plan v2; "
            "the runner skeleton currently only implements Phase A."
        )

    async def run_phase_c(self) -> AgenticPhaseResult:
        raise NotImplementedError(
            "Phase C (execution) lands in sub-phase 3 of plan v2."
        )

    async def run_phase_d(self) -> AgenticPhaseResult:
        raise NotImplementedError(
            "Phase D (report prettification) lands in sub-phase 4 of plan v2."
        )

    # ------------------------------------------------------------------
    # Full chain
    # ------------------------------------------------------------------

    async def run(self) -> AgenticValidationResult:
        """Chain A → B → C → D. Persists the aggregate result on disk.

        Sub-phase 1 only runs Phase A. Later phases will extend this.
        """
        agg = AgenticValidationResult(
            bundle_id=self.bundle_id,
            bundle_dir=str(self.bundle_dir),
            model_type=self.model_type,
            started_at=_utcnow_iso(),
        )

        try:
            phase_a = await self.run_phase_a()
            agg.phases.append(phase_a)
            agg.total_cost_usd += phase_a.cost_usd
            agg.total_turns += phase_a.turns
            agg.success = phase_a.success
        except Exception as exc:
            agg.success = False
            agg.error = f"phase_a:{type(exc).__name__}:{exc}"
            log.exception("Agentic Phase A failed for bundle %s", self.bundle_id)

        agg.finished_at = _utcnow_iso()
        self._persist_aggregate(agg)
        return agg

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _run_phase(
        self,
        phase: str,
        user_prompt: str,
        model: str,
        expected_outputs: list[Path],
    ) -> AgenticPhaseResult:
        """Drive one phase via ClaudeSDKClient and persist its transcript.

        Defensive: imports the SDK lazily so unit tests can run on machines
        without ``claude-agent-sdk`` installed (the runner skeleton tests
        mock this method).
        """
        budget = _PHASE_BUDGETS[phase]
        transcript_path = self._transcripts_dir / f"phase_{phase.lower()}.jsonl"
        transcript_path.unlink(missing_ok=True)

        sdk = _try_import_sdk()
        if sdk is None:
            return AgenticPhaseResult(
                phase=phase,
                success=False,
                transcript_path=str(transcript_path),
                error="claude-agent-sdk not installed; cannot run agentic phase",
            )

        options = sdk["ClaudeAgentOptions"](
            cwd=str(self.bundle_dir),
            model=model,
            permission_mode="acceptEdits",
            allowed_tools=budget["allowed_tools"],
            max_turns=budget["max_turns"],
            system_prompt=self.system_prompt(),
            hooks={
                "PreToolUse": [
                    sdk["HookMatcher"](
                        matcher="Edit|Write|MultiEdit",
                        hooks=[_make_bundle_path_guard(str(self.bundle_dir))],
                    ),
                ],
            },
        )

        result = AgenticPhaseResult(
            phase=phase,
            success=True,
            transcript_path=str(transcript_path),
        )
        text_parts: list[str] = []
        turns = 0

        try:
            async with sdk["ClaudeSDKClient"](options=options) as client:
                await client.query(user_prompt)
                async for message in client.receive_response():
                    self._append_transcript(transcript_path, message)
                    if isinstance(message, sdk["AssistantMessage"]):
                        turns += 1
                        for block in getattr(message, "content", []) or []:
                            if hasattr(block, "text") and block.text:
                                text_parts.append(block.text)
                    elif isinstance(message, sdk["ResultMessage"]):
                        result.session_id = getattr(message, "session_id", "") or ""
                        result.cost_usd = float(
                            getattr(message, "total_cost_usd", 0) or 0
                        )
                        subtype = getattr(message, "subtype", "")
                        if subtype and subtype != "success":
                            result.success = False
                            result.error = f"Agent ended with subtype: {subtype}"
        except Exception as exc:
            result.success = False
            result.error = f"{type(exc).__name__}: {exc}"
            log.exception("Phase %s failed for bundle %s", phase, self.bundle_id)

        result.result_text = "\n".join(text_parts) if text_parts else ""
        result.turns = turns

        # Verify expected outputs landed on disk. Missing files turn a
        # nominally-successful SDK run into a failed phase — Claude Code
        # sometimes ends a turn before its Write tool flushes (race), or
        # interprets the prompt loosely and writes nothing.
        missing = [p for p in expected_outputs if not p.exists()]
        if missing:
            result.success = False
            if not result.error:
                result.error = (
                    "Phase finished but expected outputs are missing: "
                    + ", ".join(str(p.relative_to(self.bundle_dir)) for p in missing)
                )
        else:
            result.files_written = [
                str(p.relative_to(self.bundle_dir)) for p in expected_outputs
            ]

        return result

    @staticmethod
    def _append_transcript(transcript_path: Path, message: Any) -> None:
        """Persist one SDK message to the per-phase jsonl transcript."""
        entry: dict[str, Any] = {
            "ts": _utcnow_iso(),
            "kind": type(message).__name__,
        }
        content = getattr(message, "content", None)
        if isinstance(content, list):
            entry["content"] = [_serialise_block(b) for b in content]
        elif content is not None:
            entry["content"] = repr(content)[:2000]
        # Surface result-level metadata for ResultMessages
        for attr in ("session_id", "total_cost_usd", "subtype", "duration_ms", "usage"):
            if hasattr(message, attr):
                try:
                    entry[attr] = getattr(message, attr)
                except Exception:
                    pass
        try:
            with transcript_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            log.warning("transcript write failed at %s: %s", transcript_path, exc)

    def _persist_aggregate(self, agg: AgenticValidationResult) -> None:
        out = self._transcripts_dir / "result.json"
        try:
            out.write_text(
                json.dumps(agg.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("aggregate result write failed at %s: %s", out, exc)


def _try_import_sdk() -> Optional[dict[str, Any]]:
    """Late import of claude-agent-sdk so tests can patch this function."""
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            HookMatcher,
            ResultMessage,
        )
    except ImportError as exc:
        log.warning("claude-agent-sdk unavailable: %s", exc)
        return None
    return {
        "AssistantMessage": AssistantMessage,
        "ClaudeAgentOptions": ClaudeAgentOptions,
        "ClaudeSDKClient": ClaudeSDKClient,
        "HookMatcher": HookMatcher,
        "ResultMessage": ResultMessage,
    }


# ---------------------------------------------------------------------------
# Convenience entrypoint
# ---------------------------------------------------------------------------

async def run_agentic_validation(
    bundle_id: str,
    bundle_dir: Path | str,
    model_type: str = "unknown",
    config: Optional[ValidationConfig] = None,
    pre_check_summary: Optional[dict[str, Any]] = None,
    knowledge_dir: Optional[Path | str] = None,
    repo_dir: Optional[Path | str] = None,
) -> AgenticValidationResult:
    """One-call convenience wrapper around :class:`AgenticValidator`."""
    validator = AgenticValidator(
        bundle_id=bundle_id,
        bundle_dir=bundle_dir,
        model_type=model_type,
        config=config,
        pre_check_summary=pre_check_summary,
        knowledge_dir=knowledge_dir,
        repo_dir=repo_dir,
    )
    return await validator.run()


def run_agentic_validation_sync(*args: Any, **kwargs: Any) -> AgenticValidationResult:
    """Synchronous wrapper for scripts that can't drive async themselves."""
    return asyncio.run(run_agentic_validation(*args, **kwargs))
