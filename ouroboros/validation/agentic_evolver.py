"""Agentic methodology evolver — Plan v2 Phase 9.

Consumes :class:`ReflectionResult` (from
:mod:`ouroboros.validation.agentic_reflection`) and produces
:class:`EvolutionProposal` records that target the validator's own source
code via the four target kinds documented in v2 plan §6 Piece 3:

* **prompt**: append a directive to
  ``ouroboros/validation/agentic_prompts/phase_a_methodology.md`` (or
  another phase prompt).
* **helper**: add or extend a reusable Python function in
  ``ouroboros/validation/agentic_helpers/<module>.py`` that future
  Phase B's can import.
* **system_prompt**: extend the inlined context produced by
  ``ouroboros/validation/agentic_system_prompt.py``.
* **pre_check**: add/retire a deterministic pre-check in
  ``ouroboros/validation/checks/<module>.py``.

Output proposals are persisted to ``knowledge/evolution_proposals.jsonl``
where the agent's consciousness loop / source-evolution task can find
them. The proposer **does not modify source code** — that's the
7-step protocol's job, gated by smoke tests + multi-model review.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ouroboros.validation.types import (
    EvolutionProposal,
    ReflectionResult,
    ValidationConfig,
)

log = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(text: str, max_len: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:max_len] or "x"


class AgenticEvolutionProposer:
    """Convert reflection patterns into typed evolution proposals."""

    def __init__(
        self,
        knowledge_dir: Path | str,
        config: Optional[ValidationConfig] = None,
        min_confidence_to_persist: float = 0.5,
    ) -> None:
        self.knowledge_dir = Path(knowledge_dir).resolve()
        self.config = config or ValidationConfig()
        self.min_confidence_to_persist = min_confidence_to_persist

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def propose(self, reflection: ReflectionResult) -> list[EvolutionProposal]:
        """Build a list of :class:`EvolutionProposal` from the reflection
        result. Side effect: appends each proposal at confidence ≥
        ``min_confidence_to_persist`` to
        ``knowledge/evolution_proposals.jsonl``.

        Returns ALL proposals (including below-threshold ones) so callers
        / tests can inspect the full set.
        """
        out: list[EvolutionProposal] = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for pattern in reflection.patterns_found:
            kind = pattern.get("kind", "")
            if kind == "candidate_false_positive":
                proposal = self._propose_for_false_positive(pattern, today, reflection)
            elif kind == "methodological_motif":
                proposal = self._propose_for_motif(pattern, today, reflection)
            elif kind == "recurring_failure":
                # Only propose for genuine recurring failures (TP confirmed
                # by the tracker, i.e. NOT also in candidate_false_positive).
                proposal = self._propose_for_recurring_failure(
                    pattern, today, reflection
                )
            else:
                continue
            if proposal is not None:
                out.append(proposal)

        self._persist(out)
        return out

    # ------------------------------------------------------------------
    # Per-pattern proposal builders
    # ------------------------------------------------------------------

    def _propose_for_false_positive(
        self, pattern: dict[str, Any], today: str, reflection: ReflectionResult,
    ) -> Optional[EvolutionProposal]:
        check_id = pattern.get("check_id", "")
        freq = pattern.get("frequency", 0)
        # Confidence scales with frequency: 3 bundles → 0.55, 5 → 0.75, 10 → 1.0.
        confidence = min(1.0, 0.4 + 0.07 * freq)
        directive = (
            f"Add a directive to phase_a_methodology.md: when planning the "
            f"validation methodology for a new bundle, if you would otherwise "
            f"include a test matching {check_id!r}, first check whether the "
            f"finding is a STRUCTURAL ARTIFACT of how the bundle was "
            f"packaged (e.g. hardcoded `/kaggle/input/` paths, dataset "
            f"separators, vendor preambles). Across {freq} past bundles "
            f"this check failed with zero confirmed true-positive impact "
            f"on metrics. Either skip it OR justify in writing why it's a "
            f"real defect for THIS bundle."
        )
        return EvolutionProposal(
            proposal_id=f"evo.{today}.fp.{_slug(check_id)}",
            target_kind="prompt",
            target_path="ouroboros/validation/agentic_prompts/phase_a_methodology.md",
            rationale=pattern.get("description", ""),
            directive=directive,
            source_pattern_kinds=["candidate_false_positive"],
            source_pattern_count=freq,
            confidence=confidence,
            estimated_effort="trivial",
            created_at=_utcnow_iso(),
        )

    def _propose_for_motif(
        self, pattern: dict[str, Any], today: str, reflection: ReflectionResult,
    ) -> Optional[EvolutionProposal]:
        name_key = pattern.get("name_key") or ""
        freq = pattern.get("frequency", 0)
        metrics = pattern.get("metrics") or []
        # Confidence scales with frequency but caps lower than FP because
        # we're adding new code, not just appending a directive.
        confidence = min(0.9, 0.35 + 0.06 * freq)
        helper_module = f"motif_{_slug(name_key)}.py"
        if metrics:
            metric_blurb = f" computing {', '.join(metrics)}"
        else:
            metric_blurb = ""
        directive = (
            f"Add a helper module ouroboros/validation/agentic_helpers/"
            f"{helper_module} implementing the '{name_key}' test"
            f"{metric_blurb}. Phase B's authored validation_project should "
            f"import this helper instead of reimplementing the logic per "
            f"bundle — the motif appeared in {freq} bundles' methodologies. "
            f"The helper must expose a `run(ctx)` function returning the "
            f"agentic test schema dict (id, verdict, metric, evidence). "
            f"Also append a one-line note to phase_b_implement.md telling "
            f"Phase B to PREFER this helper over re-authoring."
        )
        return EvolutionProposal(
            proposal_id=f"evo.{today}.motif.{_slug(name_key)}",
            target_kind="helper",
            target_path=f"ouroboros/validation/agentic_helpers/{helper_module}",
            rationale=pattern.get("description", ""),
            directive=directive,
            source_pattern_kinds=["methodological_motif"],
            source_pattern_count=freq,
            confidence=confidence,
            estimated_effort="moderate",
            created_at=_utcnow_iso(),
        )

    def _propose_for_recurring_failure(
        self, pattern: dict[str, Any], today: str, reflection: ReflectionResult,
    ) -> Optional[EvolutionProposal]:
        check_id = pattern.get("check_id", "")
        freq = pattern.get("frequency", 0)
        # Only meaningful if this same check_id is NOT also flagged as a
        # candidate false positive — otherwise the FP path already covers it.
        for p in reflection.patterns_found:
            if (p.get("kind") == "candidate_false_positive"
                    and p.get("check_id") == check_id):
                return None

        # Floor of 0.35 so a 3-bundle confirmed recurring failure clears the
        # 0.5 persistence threshold — fewer than 3 bundles is below
        # _MIN_BUNDLES_FOR_PATTERN so the reflection engine never surfaces it.
        confidence = min(0.85, 0.35 + 0.06 * freq)
        directive = (
            f"Phase A's methodology design should ALWAYS include a test "
            f"matching {check_id!r}: it failed in {freq} bundles with at "
            f"least one tracker-confirmed true-positive improvement. "
            f"Append a directive to phase_a_methodology.md so future "
            f"Phase A sessions include this check by default (with the "
            f"appropriate pass_criterion + how_to_inspect lines for the "
            f"current bundle)."
        )
        return EvolutionProposal(
            proposal_id=f"evo.{today}.recur.{_slug(check_id)}",
            target_kind="prompt",
            target_path="ouroboros/validation/agentic_prompts/phase_a_methodology.md",
            rationale=pattern.get("description", ""),
            directive=directive,
            source_pattern_kinds=["recurring_failure"],
            source_pattern_count=freq,
            confidence=confidence,
            estimated_effort="trivial",
            created_at=_utcnow_iso(),
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, proposals: list[EvolutionProposal]) -> None:
        keepers = [p for p in proposals if p.confidence >= self.min_confidence_to_persist]
        if not keepers:
            return
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        path = self.knowledge_dir / "evolution_proposals.jsonl"
        try:
            with path.open("a", encoding="utf-8") as fh:
                for p in keepers:
                    fh.write(json.dumps(p.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("Could not persist evolution proposals: %s", exc)
