"""Phase 8 tests — agentic cross-bundle reflection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ouroboros.validation.agentic_reflection import AgenticReflectionEngine


def _seed_bundle(
    validations_dir: Path,
    bundle_id: str,
    tests: list[dict],
    methodology_md: str = "# Methodology\n",
) -> Path:
    """Lay down a minimal post-Phase-C bundle on disk."""
    bundle = validations_dir / bundle_id
    (bundle / "results").mkdir(parents=True)
    (bundle / "methodology").mkdir(parents=True)
    (bundle / "results" / "results.json").write_text(
        json.dumps({
            "schema_version": "1",
            "bundle_id": bundle_id,
            "tests": tests,
            "summary": {
                "n_pass": sum(1 for t in tests if t["verdict"] == "pass"),
                "n_warn": sum(1 for t in tests if t["verdict"] == "warn"),
                "n_fail": sum(1 for t in tests if t["verdict"] == "fail"),
                "n_deferred": 0, "n_error": 0,
            },
        }, indent=2),
        encoding="utf-8",
    )
    (bundle / "methodology" / "methodology.md").write_text(methodology_md, encoding="utf-8")
    return bundle


def _seed_finding(validations_dir: Path, check_id: str, bundle_id: str, verdict: str) -> None:
    path = validations_dir / "validation_findings.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "check_id": check_id, "bundle_id": bundle_id,
            "verdict": verdict, "source": "improvement_inferred",
            "weight": 0.5, "timestamp": "2026-05-17T00:00:00Z",
        }) + "\n")


def test_reflection_finds_motifs_and_recurring_failures(tmp_path: Path) -> None:
    """Three bundles all running the same 'OOS AUC' quantitative test
    should surface a methodological motif. A check_id that fails in all
    three should surface as recurring_failure AND (since no TP records
    exist) as candidate_false_positive."""
    validations = tmp_path / "validations"
    validations.mkdir()

    common_tests = [
        {"id": "quant1", "name": "OOS AUC on holdout", "block": "quantitative",
         "verdict": "pass", "metric": {"AUC": 0.82},
         "evidence": "good", "error": None},
        {"id": "qsmells", "name": "code smells in kernel", "block": "qualitative",
         "verdict": "fail", "metric": None,
         "evidence": "hardcoded paths", "error": None},
    ]
    for bid in ("bundle-a", "bundle-b", "bundle-c"):
        _seed_bundle(validations, bid, common_tests)

    engine = AgenticReflectionEngine(
        validations_dir=validations,
        knowledge_dir=tmp_path / "knowledge",
    )

    out = engine.reflect()

    assert out.total_validations_analyzed == 3

    # Motif: 'oos_auc_on_holdout' showed up in all 3 bundles with metric 'AUC'
    motifs = [p for p in out.patterns_found if p["kind"] == "methodological_motif"]
    motif_names = [m["name_key"] for m in motifs]
    assert "oos_auc_on_holdout" in motif_names
    assert "code_smells_in_kernel" in motif_names
    oos = next(m for m in motifs if m["name_key"] == "oos_auc_on_holdout")
    assert oos["frequency"] == 3
    assert oos["metrics"] == ["AUC"]

    # Recurring failure: QUALITATIVE.qsmells failed in all 3
    failures = [p for p in out.patterns_found if p["kind"] == "recurring_failure"]
    fail_ids = [f["check_id"] for f in failures]
    assert "QUALITATIVE.qsmells" in fail_ids
    qsmells = next(f for f in failures if f["check_id"] == "QUALITATIVE.qsmells")
    assert qsmells["frequency"] == 3

    # Candidate false positive: no TP records exist for qsmells
    fps = [p for p in out.patterns_found if p["kind"] == "candidate_false_positive"]
    fp_ids = [p["check_id"] for p in fps]
    assert "QUALITATIVE.qsmells" in fp_ids

    # hot_checks (failed in every bundle)
    assert "QUALITATIVE.qsmells" in out.hot_checks

    # Knowledge digest written
    assert out.knowledge_entries_written == ["agentic_reflection.md"]
    digest = (tmp_path / "knowledge" / "agentic_reflection.md").read_text(encoding="utf-8")
    assert "Candidate structural artifacts" in digest
    assert "Methodological motifs" in digest
    assert "QUALITATIVE.qsmells" in digest


def test_reflection_suppresses_false_positive_when_tracker_confirms_TP(
    tmp_path: Path,
) -> None:
    """If a recurring failure has at least one ``true_positive`` finding
    record in the tracker, it must NOT show up as candidate_false_positive
    (the improvement cycle confirmed the finding was real)."""
    validations = tmp_path / "validations"
    validations.mkdir()

    tests = [
        {"id": "quant1", "name": "AUC", "block": "quantitative",
         "verdict": "fail", "metric": {"AUC": 0.40},
         "evidence": "low", "error": None},
    ]
    for bid in ("bundle-a", "bundle-b", "bundle-c"):
        _seed_bundle(validations, bid, tests)

    # Tracker shows the rec produced a real improvement once
    _seed_finding(validations, "QUANTITATIVE.quant1", "bundle-a", "true_positive")
    # And a false-positive elsewhere (shouldn't matter; TP > 0 is enough)
    _seed_finding(validations, "QUANTITATIVE.quant1", "bundle-b", "false_positive")

    engine = AgenticReflectionEngine(
        validations_dir=validations,
        knowledge_dir=tmp_path / "knowledge",
    )

    out = engine.reflect()

    # recurring_failure still surfaces (it failed everywhere)
    failures = [p for p in out.patterns_found if p["kind"] == "recurring_failure"]
    assert any(f["check_id"] == "QUANTITATIVE.quant1" for f in failures)

    # But candidate_false_positive does NOT — tracker has a TP record
    fps = [p for p in out.patterns_found if p["kind"] == "candidate_false_positive"]
    assert all(p["check_id"] != "QUANTITATIVE.quant1" for p in fps)
