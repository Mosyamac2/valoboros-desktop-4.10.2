"""Phase 9 tests — AgenticEvolutionProposer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ouroboros.validation.agentic_evolver import AgenticEvolutionProposer
from ouroboros.validation.types import EvolutionProposal, ReflectionResult


def test_proposer_emits_three_target_kinds_from_a_realistic_reflection(
    tmp_path: Path,
) -> None:
    """A reflection result with one motif + one false positive + one
    confirmed recurring failure (TP, not FP-candidate) must produce
    three proposals with the right target_kinds and persist them."""
    reflection = ReflectionResult(total_validations_analyzed=5)
    reflection.patterns_found = [
        {
            "kind": "candidate_false_positive",
            "check_id": "QUALITATIVE.qsmells",
            "frequency": 5,
            "description": "qsmells failed in 5 bundles with zero TPs",
        },
        {
            "kind": "methodological_motif",
            "name_key": "oos_auc_on_holdout",
            "frequency": 4,
            "metrics": ["AUC"],
            "description": "Methodologies asked for 'oos_auc_on_holdout' in 4/5 bundles",
        },
        {
            "kind": "recurring_failure",
            "check_id": "QUANTITATIVE.quant3",
            "frequency": 3,
            "description": "quant3 failed in 3 bundles (TP-confirmed in tracker)",
        },
    ]

    knowledge = tmp_path / "knowledge"
    proposer = AgenticEvolutionProposer(knowledge_dir=knowledge)

    proposals = proposer.propose(reflection)

    assert len(proposals) == 3
    by_kind: dict[str, EvolutionProposal] = {p.target_kind: p for p in proposals}
    assert set(by_kind.keys()) == {"prompt", "helper"}

    # ── FP proposal targets phase_a_methodology.md ───────────────────
    fp = next(p for p in proposals if "fp." in p.proposal_id)
    assert fp.target_kind == "prompt"
    assert fp.target_path == (
        "ouroboros/validation/agentic_prompts/phase_a_methodology.md"
    )
    assert "QUALITATIVE.qsmells" in fp.directive
    assert "structural artifact" in fp.directive.lower()
    assert fp.confidence >= 0.5
    assert fp.source_pattern_kinds == ["candidate_false_positive"]
    assert fp.source_pattern_count == 5

    # ── Motif proposal targets agentic_helpers ───────────────────────
    motif = next(p for p in proposals if "motif." in p.proposal_id)
    assert motif.target_kind == "helper"
    assert motif.target_path.startswith(
        "ouroboros/validation/agentic_helpers/motif_oos_auc_on_holdout"
    )
    assert "oos_auc_on_holdout" in motif.directive
    assert "AUC" in motif.directive

    # ── Recurring-failure proposal targets the methodology prompt ────
    recur = next(p for p in proposals if "recur." in p.proposal_id)
    assert recur.target_kind == "prompt"
    assert "QUANTITATIVE.quant3" in recur.directive
    assert "ALWAYS include" in recur.directive

    # Persisted as jsonl, one row per ≥-min-confidence proposal
    proposals_file = knowledge / "evolution_proposals.jsonl"
    assert proposals_file.exists()
    rows = [
        json.loads(l)
        for l in proposals_file.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert len(rows) == 3
    assert all(r["target_kind"] in {"prompt", "helper"} for r in rows)
    assert all(0.0 <= r["confidence"] <= 1.0 for r in rows)


def test_proposer_suppresses_recurring_failure_when_also_FP_candidate(
    tmp_path: Path,
) -> None:
    """If the same check_id is BOTH recurring_failure and
    candidate_false_positive, the proposer must emit ONLY the FP proposal
    (the FP directive subsumes the recurring-failure directive)."""
    reflection = ReflectionResult(total_validations_analyzed=4)
    reflection.patterns_found = [
        {
            "kind": "candidate_false_positive",
            "check_id": "QUALITATIVE.qsmells",
            "frequency": 4,
            "description": "qsmells in 4 bundles, no TPs",
        },
        {
            "kind": "recurring_failure",
            "check_id": "QUALITATIVE.qsmells",
            "frequency": 4,
            "description": "qsmells failed in 4 bundles",
        },
    ]

    knowledge = tmp_path / "knowledge"
    proposer = AgenticEvolutionProposer(knowledge_dir=knowledge)

    proposals = proposer.propose(reflection)

    assert len(proposals) == 1
    assert proposals[0].source_pattern_kinds == ["candidate_false_positive"]
    assert proposals[0].target_kind == "prompt"

    rows = [
        json.loads(l)
        for l in (knowledge / "evolution_proposals.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert len(rows) == 1
