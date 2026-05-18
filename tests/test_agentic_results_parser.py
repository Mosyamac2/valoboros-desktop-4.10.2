"""Phase 5 tests — agentic results parser → legacy ValidationReport bridge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ouroboros.validation.agentic_results_parser import parse_agentic_results
from ouroboros.validation.types import ValidationReport


def _seed_bundle(
    tmp_path: Path,
    tests: list[dict],
    summary: dict,
    interpretation: str = "",
) -> Path:
    """Lay down a minimal post-Phase-C bundle on disk."""
    bundle = tmp_path / "bundle-abc"
    (bundle / "results").mkdir(parents=True)
    (bundle / "results" / "results.json").write_text(
        json.dumps({
            "schema_version": "1",
            "bundle_id": "bundle-abc",
            "tests": tests,
            "summary": summary,
        }, indent=2),
        encoding="utf-8",
    )
    if interpretation:
        (bundle / "results" / "interpretation.md").write_text(
            interpretation, encoding="utf-8"
        )
    return bundle


def test_parse_maps_mixed_verdicts_into_legacy_shape(tmp_path: Path) -> None:
    """A bundle with one failing quant test + one passing qual test + one
    deferred test must produce: 2 stages (QUAL, QUANT), 1 critical finding
    pointing at the failed test, 1 hard recommendation derived from it,
    meta_scores carrying the numeric metric, and overall_verdict='rejected'
    because there is at least one fail."""
    tests = [
        {"id": "q1", "name": "target column", "block": "qualitative",
         "verdict": "pass", "metric": None, "evidence": "single target inferred",
         "error": None},
        {"id": "quant1", "name": "OOS AUC", "block": "quantitative",
         "verdict": "fail", "metric": {"AUC": 0.55},
         "evidence": "AUC 0.55 < 0.70 threshold", "error": None},
        {"id": "quant2", "name": "feature stability", "block": "quantitative",
         "verdict": "deferred", "metric": None,
         "evidence": "second snapshot unavailable", "error": None},
    ]
    summary = {"n_pass": 1, "n_warn": 0, "n_fail": 1, "n_deferred": 1, "n_error": 0}
    interpretation = (
        "# Interpretation\n\n"
        "## Verdict\nReject — OOS AUC below threshold.\n\n"
        "## Soft findings\n"
        "- The model would benefit from 3x more training data.\n"
        "- Domain SME should confirm the target encoding.\n"
    )
    bundle = _seed_bundle(tmp_path, tests, summary, interpretation)

    report: ValidationReport = parse_agentic_results(bundle)

    assert report.bundle_id == "bundle-abc"
    assert report.overall_verdict == "rejected"
    assert [s.stage for s in report.stages] == ["QUAL", "QUANT"]
    qual = report.stages[0]
    quant = report.stages[1]
    assert qual.status == "passed"
    assert quant.status == "failed"
    assert len(qual.checks) == 1
    assert len(quant.checks) == 2
    # Check projection: check_ids are namespaced
    qual_ids = [c.check_id for c in qual.checks]
    quant_ids = [c.check_id for c in quant.checks]
    assert qual_ids == ["QUALITATIVE.q1"]
    assert quant_ids == ["QUANTITATIVE.quant1", "QUANTITATIVE.quant2"]
    # Severity mapping
    assert qual.checks[0].severity == "pass"
    assert quant.checks[0].severity == "critical"   # fail → critical
    assert quant.checks[0].passed is False
    assert quant.checks[1].severity == "info"       # deferred → info
    # Score coercion: AUC dict → 0.55
    assert quant.checks[0].score == pytest.approx(0.55)
    # Critical findings: only the fail
    assert len(report.critical_findings) == 1
    assert report.critical_findings[0].check_id == "QUANTITATIVE.quant1"
    # Hard recommendations: one per failed test
    assert len(report.hard_recommendations) == 1
    rec = report.hard_recommendations[0]
    assert rec.kind == "hard"
    assert rec.finding_check_id == "QUANTITATIVE.quant1"
    assert "0.55" in rec.problem or "AUC" in rec.problem
    # Soft recommendations parsed from interpretation.md
    assert len(report.soft_recommendations) == 2
    soft_texts = [r.recommendation for r in report.soft_recommendations]
    assert any("3x more training data" in s for s in soft_texts)
    assert any("Domain SME" in s for s in soft_texts)
    # meta_scores carries the numeric metric
    assert report.meta_scores.get("AUC") == pytest.approx(0.55)
    # Legacy report.json written alongside
    legacy_path = bundle / "results" / "report.json"
    assert legacy_path.exists()
    blob = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert blob["bundle_id"] == "bundle-abc"
    assert blob["overall_verdict"] == "rejected"


def test_parse_all_passing_bundle_returns_approved_verdict(tmp_path: Path) -> None:
    """A bundle where every test passed must return overall_verdict='approved'
    with zero critical_findings and zero hard recommendations."""
    tests = [
        {"id": "q1", "name": "target", "block": "qualitative",
         "verdict": "pass", "metric": None, "evidence": "ok", "error": None},
        {"id": "quant1", "name": "AUC", "block": "quantitative",
         "verdict": "pass", "metric": {"AUC": 0.85}, "evidence": "AUC 0.85",
         "error": None},
        {"id": "quant2", "name": "RMSE", "block": "quantitative",
         "verdict": "pass", "metric": {"RMSE": 0.12}, "evidence": "RMSE 0.12",
         "error": None},
    ]
    summary = {"n_pass": 3, "n_warn": 0, "n_fail": 0, "n_deferred": 0, "n_error": 0}
    bundle = _seed_bundle(tmp_path, tests, summary, "")

    report = parse_agentic_results(bundle, pre_check_summary={"smells_count": 7})

    assert report.overall_verdict == "approved"
    assert report.critical_findings == []
    assert report.hard_recommendations == []
    assert report.soft_recommendations == []
    # Both numeric metrics carried in meta_scores
    assert report.meta_scores["AUC"] == pytest.approx(0.85)
    assert report.meta_scores["RMSE"] == pytest.approx(0.12)
    # Pre-check summary scalars carried under namespace
    assert report.meta_scores["pre_check_smells_count"] == pytest.approx(7.0)
    # Two stages still produced
    stage_names = [s.stage for s in report.stages]
    assert stage_names == ["QUAL", "QUANT"]
    assert all(s.status == "passed" for s in report.stages)


def test_parse_writes_improvement_plan_from_agentic_findings(tmp_path: Path) -> None:
    """The improvement → revalidation handoff must read the agentic findings.

    Regression for the case where a legacy stage runner wrote
    ``improvement/plan.json`` first (with stale S8.CODE_SMELLS recs) and a
    later agentic pass left it untouched. After parsing, the plan file
    must reflect the agentic ``hard_recommendations``/``soft_recommendations``.
    """
    tests = [
        {"id": "q1", "name": "train/test contamination", "block": "qualitative",
         "verdict": "fail", "metric": None,
         "evidence": "pd.concat([train,test]) before encoders", "error": None},
        {"id": "quant5", "name": "engineered-ratio inf/NaN audit",
         "block": "quantitative", "verdict": "fail",
         "metric": {"max_inf_plus_nan_rate": 0.67},
         "evidence": "0.67 inf/NaN rate across 14 ratios", "error": None},
    ]
    summary = {"n_pass": 0, "n_warn": 0, "n_fail": 2, "n_deferred": 0, "n_error": 0}
    bundle = _seed_bundle(tmp_path, tests, summary, "")

    # Seed a stale legacy plan to be overwritten.
    plan_path = bundle / "improvement" / "plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps({
        "hard": [{"finding_check_id": "S8.CODE_SMELLS",
                  "problem": "stale legacy recommendation",
                  "recommendation": "should be overwritten",
                  "kind": "hard", "implementation_sketch": "",
                  "estimated_metric_impact": {}, "confidence": 0.5,
                  "effort": "trivial", "priority": 1}],
        "soft": [],
    }), encoding="utf-8")

    report = parse_agentic_results(bundle)

    # plan.json now reflects the agentic findings, not the stale legacy one.
    blob = json.loads(plan_path.read_text(encoding="utf-8"))
    hard_ids = [r["finding_check_id"] for r in blob["hard"]]
    assert "S8.CODE_SMELLS" not in hard_ids, (
        "stale legacy recommendation should have been overwritten"
    )
    assert sorted(hard_ids) == sorted(
        r.finding_check_id for r in report.hard_recommendations
    )
    # And the new ids correspond to the failing agentic tests.
    assert set(hard_ids) == {"QUALITATIVE.q1", "QUANTITATIVE.quant5"}


def test_parse_skips_plan_write_when_legacy_report_disabled(tmp_path: Path) -> None:
    """``write_legacy_report=False`` must skip both report.json and plan.json,
    so callers that want to inspect the parsed report without touching disk
    can opt out."""
    tests = [{"id": "q1", "name": "x", "block": "qualitative",
              "verdict": "fail", "metric": None, "evidence": "", "error": None}]
    summary = {"n_pass": 0, "n_warn": 0, "n_fail": 1, "n_deferred": 0, "n_error": 0}
    bundle = _seed_bundle(tmp_path, tests, summary, "")

    parse_agentic_results(bundle, write_legacy_report=False)

    assert not (bundle / "results" / "report.json").exists()
    assert not (bundle / "improvement" / "plan.json").exists()
