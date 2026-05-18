"""Source-evolution executor — Plan v2 Phase 10.

Consume :class:`EvolutionProposal` records and run the 7-step evolution
protocol against the validator's own source code. The four target_kind
values map to four allow-listed path prefixes; everything else is denied.

This module **does not bypass** the 7-step protocol — it routes proposals
into the same gates the rest of the agent's self-modification uses
(claude_code_edit + smoke tests + review + Bible check + commit). The
module's value is the typed routing + deterministic allow-list, both of
which give the consciousness loop a safe call site.

Two execution modes:

* ``dry_run=True`` (default for tests): plan the edit without invoking the
  Claude Code SDK. Returns a ``EvolutionAttempt`` describing what would
  happen — useful for inspection in CI and unit tests.
* ``dry_run=False``: call :func:`ouroboros.tools.shell.claude_code_edit`
  with the proposal's directive. The SDK's PreToolUse hooks enforce
  path safety; if the SDK is unavailable (no claude-agent-sdk installed)
  the helper returns a ``not_executed`` outcome.

Commit semantics live in the existing agent task pipeline — this helper
is per-proposal preparation, not the commit gate.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ouroboros.validation.types import EvolutionProposal

log = logging.getLogger(__name__)


# Allow-list: every target_path must start with one of these. Mirror of the
# allow-list in v2 plan §6 Piece 4.
_ALLOW_LIST_PREFIXES = (
    "ouroboros/validation/agentic_prompts/",
    "ouroboros/validation/agentic_helpers/",
    "ouroboros/validation/agentic_system_prompt.py",
    "ouroboros/validation/checks/",
)

# Deny-list: nothing on the source-evolution path may touch these. Mirror of
# SAFETY_CRITICAL_PATHS in :mod:`ouroboros.tools.registry`.
_DENY_LIST = frozenset({
    "BIBLE.md",
    "ouroboros/safety.py",
    "ouroboros/tools/registry.py",
    "prompts/SAFETY.md",
    "ouroboros/validation/sandbox.py",
})


_TARGET_KIND_PATH_PREFIX = {
    "prompt": "ouroboros/validation/agentic_prompts/",
    "helper": "ouroboros/validation/agentic_helpers/",
    "system_prompt": "ouroboros/validation/agentic_system_prompt.py",
    "pre_check": "ouroboros/validation/checks/",
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class EvolutionAttempt:
    """Outcome of one source-evolution attempt against a single proposal."""

    proposal_id: str
    target_path: str
    target_kind: str
    outcome: str                       # "planned" | "applied" | "denied" | "not_executed" | "failed"
    reason: str = ""
    dry_run: bool = True
    sdk_result: Optional[dict[str, Any]] = None
    started_at: str = ""
    finished_at: str = ""
    changed_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SourceEvolutionExecutor:
    """Run typed source-evolution attempts against allow-listed paths.

    The executor does NOT auto-commit. Commits go through the existing
    agent task pipeline + 7-step protocol — this is the "claude_code_edit
    invocation" step of that protocol, packaged for the consciousness
    loop to call uniformly.
    """

    def __init__(
        self,
        repo_dir: Path | str,
        knowledge_dir: Optional[Path | str] = None,
        editor: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.repo_dir = Path(repo_dir).resolve()
        if knowledge_dir is None:
            self.knowledge_dir = Path(
                os.environ.get(
                    "OUROBOROS_KNOWLEDGE_DIR",
                    Path.home() / "Ouroboros" / "data" / "memory" / "knowledge",
                )
            )
        else:
            self.knowledge_dir = Path(knowledge_dir).resolve()
        # Allow injecting a fake editor for tests
        self._editor = editor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def attempt(
        self,
        proposal: EvolutionProposal,
        dry_run: bool = True,
    ) -> EvolutionAttempt:
        """Validate the proposal's target_path and (if not dry-run) drive
        ``claude_code_edit`` to apply the directive.

        Always returns an :class:`EvolutionAttempt` — never raises on a
        denied path / missing SDK. Failure surfaces in
        ``attempt.outcome``.
        """
        result = EvolutionAttempt(
            proposal_id=proposal.proposal_id,
            target_path=proposal.target_path,
            target_kind=proposal.target_kind,
            outcome="planned",
            dry_run=dry_run,
            started_at=_utcnow_iso(),
        )

        # 1. Allow-list / deny-list / prefix-match check
        denial = self._validate_target(proposal)
        if denial:
            result.outcome = "denied"
            result.reason = denial
            result.finished_at = _utcnow_iso()
            self._persist_attempt(result)
            return result

        if dry_run:
            result.outcome = "planned"
            result.reason = "dry-run; no edit invoked"
            result.finished_at = _utcnow_iso()
            self._persist_attempt(result)
            return result

        # 2. Real run — call claude_code_edit via the injectable editor.
        try:
            editor = self._editor or _default_editor
        except Exception:
            editor = _default_editor  # safety net

        try:
            sdk_result = editor(
                cwd=str(self.repo_dir),
                target_path=proposal.target_path,
                directive=proposal.directive,
                proposal_id=proposal.proposal_id,
            )
        except Exception as exc:
            result.outcome = "failed"
            result.reason = f"{type(exc).__name__}: {exc}"
            result.finished_at = _utcnow_iso()
            self._persist_attempt(result)
            return result

        if not isinstance(sdk_result, dict):
            sdk_result = {"raw": str(sdk_result)[:500]}
        result.sdk_result = sdk_result

        if sdk_result.get("not_executed"):
            result.outcome = "not_executed"
            result.reason = sdk_result.get("error", "SDK unavailable")
        elif sdk_result.get("success") is False:
            result.outcome = "failed"
            result.reason = sdk_result.get("error", "claude_code_edit failed")
        else:
            result.outcome = "applied"
            result.changed_files = list(sdk_result.get("changed_files", []))

        result.finished_at = _utcnow_iso()
        self._persist_attempt(result)
        return result

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_target(self, proposal: EvolutionProposal) -> str:
        """Return a non-empty reason string if the proposal must be denied;
        empty string if it passes safety checks.

        Order:
        1. Deny-list (hard SAFETY-CRITICAL files)
        2. target_kind ↔ target_path prefix consistency
        3. Allow-list prefix match
        4. No path-traversal (.., absolute, symlink escape)
        """
        target = proposal.target_path or ""
        # 1. Deny-list — exact path match
        if target in _DENY_LIST:
            return f"{target!r} is a safety-critical file; source-evolution forbidden"

        # 2. target_kind ↔ target_path prefix consistency
        expected_prefix = _TARGET_KIND_PATH_PREFIX.get(proposal.target_kind, "")
        if not expected_prefix:
            return f"unknown target_kind {proposal.target_kind!r}"
        if not target.startswith(expected_prefix):
            return (
                f"target_path {target!r} does not match target_kind "
                f"{proposal.target_kind!r}'s expected prefix {expected_prefix!r}"
            )

        # 3. Allow-list — must start with one of the four prefixes
        if not any(target.startswith(p) for p in _ALLOW_LIST_PREFIXES):
            return f"target_path {target!r} is outside the evolution allow-list"

        # 4. Path safety — no traversal, no absolute
        if target.startswith("/") or ".." in target.split("/"):
            return f"target_path {target!r} contains path-traversal segments"

        # 5. (Soft) — target must resolve under repo_dir if it exists.
        #    Missing files are OK for the "helper" kind (we're creating one).
        resolved = (self.repo_dir / target).resolve()
        try:
            resolved.relative_to(self.repo_dir)
        except ValueError:
            return f"target_path {target!r} resolves outside repo {self.repo_dir}"

        return ""

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_attempt(self, attempt: EvolutionAttempt) -> None:
        try:
            self.knowledge_dir.mkdir(parents=True, exist_ok=True)
            path = self.knowledge_dir / "evolution_attempts.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(attempt.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("Could not persist evolution attempt: %s", exc)


# ---------------------------------------------------------------------------
# Default editor — wraps claude_code_edit with a guarded import
# ---------------------------------------------------------------------------

def _default_editor(
    cwd: str,
    target_path: str,
    directive: str,
    proposal_id: str,
) -> dict[str, Any]:
    """Invoke claude_code_edit from ouroboros.tools.shell with safety hooks.

    Returns a dict shaped like::

        {"not_executed": True, "error": "..."}   # SDK unavailable
        {"success": True, "changed_files": [...]}# applied
        {"success": False, "error": "..."}       # apply failed

    All exceptions are caught and surfaced as ``not_executed``.
    """
    try:
        # Late import — shell.py pulls in the gateway which pulls in the SDK.
        from ouroboros.tools.shell import claude_code_edit  # type: ignore
    except ImportError as exc:
        return {"not_executed": True, "error": f"SDK unavailable: {exc}"}

    prompt = (
        f"[Source evolution] Proposal {proposal_id} targets {target_path}. "
        f"Apply this directive precisely and conservatively:\n\n"
        f"{directive}\n\n"
        f"Constraints: only modify {target_path}. Preserve existing content "
        f"unless the directive explicitly mandates removal. After editing, "
        f"do not run any tests yourself — the agent task pipeline gates "
        f"the commit separately."
    )
    try:
        out = claude_code_edit(  # type: ignore[arg-type]
            prompt=prompt,
            cwd=cwd,
            max_turns=8,
        )
    except Exception as exc:
        return {"not_executed": True, "error": f"{type(exc).__name__}: {exc}"}
    if isinstance(out, dict):
        return out
    return {"raw": str(out)[:500]}
