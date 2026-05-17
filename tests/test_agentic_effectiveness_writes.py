"""Phase 7 tests — effectiveness tracker writes from agentic revalidation.

The synthetic-bundle pattern from test_agentic_revalidation.py is reused.
After AgenticRevalidationPipeline.run() lands, we read back the
EffectivenessTracker jsonl files and verify the right rows were appended.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ouroboros.validation.agentic_revalidation import AgenticRevalidationPipeline
from ouroboros.validation.effectiveness import EffectivenessTracker
from ouroboros.validation.types import ValidationConfig


_TINY_RUN_ALL = '''
import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--tests", default="all")
parser.add_argument("--output", required=True)
args = parser.parse_args()

verdict = os.environ.get("VERDICT_OVERRIDE", "pass")
metric = float(os.environ.get("METRIC_OVERRIDE", "0.85"))

results = {
    "schema_version": "1",
    "bundle_id": os.environ.get("BUNDLE_ID", "bundle-eff"),
    "tests": [
        {"id": "quant1", "name": "OOS AUC", "block": "quantitative",
         "verdict": verdict, "metric": {"AUC": metric},
         "evidence": f"AUC={metric}", "error": None},
    ],
    "summary": {
        "n_pass": 1 if verdict == "pass" else 0,
        "n_warn": 1 if verdict == "warn" else 0,
        "n_fail": 1 if verdict == "fail" else 0,
        "n_deferred": 0, "n_error": 0,
    },
}
with open(args.output, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
sys.exit(0)
'''


def _seed_bundle(
    tmp_path: Path,
    original_verdict: str,
    original_auc: float,
) -> Path:
    # Tracker writes to bundle_dir.parent — so the parent has to be the
    # "validations" dir; we set up a containing data/ root that mimics
    # ~/Ouroboros/data/ structure.
    data_root = tmp_path / "data"
    validations = data_root / "validations"
    validations.mkdir(parents=True)
    bundle = validations / "bundle-eff"
    (bundle / "raw" / "data").mkdir(parents=True)
    (bundle / "raw" / "model_code").mkdir(parents=True)
    (bundle / "methodology" / "validation_project").mkdir(parents=True)
    (bundle / "methodology" / "validation_project" / "run_all.py").write_text(
        _TINY_RUN_ALL, encoding="utf-8"
    )
    (bundle / "methodology" / "validation_project" / "requirements.txt").write_text(
        "", encoding="utf-8"
    )
    (bundle / "methodology" / "methodology.md").write_text("# m\n", encoding="utf-8")

    (bundle / "results").mkdir(parents=True)
    summary = {
        "n_pass": 1 if original_verdict == "pass" else 0,
        "n_warn": 1 if original_verdict == "warn" else 0,
        "n_fail": 1 if original_verdict == "fail" else 0,
        "n_deferred": 0, "n_error": 0,
    }
    (bundle / "results" / "results.json").write_text(
        json.dumps({
            "schema_version": "1",
            "bundle_id": "bundle-eff",
            "tests": [
                {"id": "quant1", "name": "OOS AUC", "block": "quantitative",
                 "verdict": original_verdict, "metric": {"AUC": original_auc},
                 "evidence": f"AUC={original_auc}", "error": None},
            ],
            "summary": summary,
        }, indent=2),
        encoding="utf-8",
    )

    (bundle / "improvement" / "implementation").mkdir(parents=True)
    (bundle / "improvement" / "implementation" / "model.py").write_text(
        "# improved\n", encoding="utf-8"
    )
    return bundle


def _run_async(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_tracker_records_recommendation_outcome_for_applied_rec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When an applied recommendation produces a fail→pass transition with
    a positive AUC delta, the tracker must record:
      - one recommendation row with check_id, bundle_id, before/after
        metrics, positive lift
      - one TP finding row (source='improvement_inferred', weight 0.5)
    """
    bundle = _seed_bundle(tmp_path, original_verdict="fail", original_auc=0.55)

    monkeypatch.setenv("VERDICT_OVERRIDE", "pass")
    monkeypatch.setenv("METRIC_OVERRIDE", "0.82")
    monkeypatch.setenv("BUNDLE_ID", "bundle-eff")

    pipeline = AgenticRevalidationPipeline(
        bundle_id="bundle-eff",
        bundle_dir=bundle,
        config=ValidationConfig(),
        python_executable=sys.executable,
        execution_timeout_sec=30,
    )

    result = _run_async(pipeline.run(
        recommendations_applied=["QUANTITATIVE.quant1"],
        recommendations_skipped=[],
    ))

    assert result.verdict == "improved"

    # Tracker writes go to bundle.parent which is the validations dir,
    # which is `tmp_path/data/validations/`. The tracker mounts on the
    # parent of the bundle, which is the validations dir. Inside that
    # dir, the tracker writes validation_recommendations.jsonl + findings.
    rec_file = bundle.parent / "validation_recommendations.jsonl"
    find_file = bundle.parent / "validation_findings.jsonl"
    assert rec_file.exists(), "tracker did not write recommendations file"
    rec_rows = [json.loads(l) for l in rec_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rec_rows) == 1
    row = rec_rows[0]
    assert row["check_id"] == "QUANTITATIVE.quant1"
    assert row["bundle_id"] == "bundle-eff"
    assert row["metric_before"] == {"quant1.AUC": pytest.approx(0.55)}
    assert row["metric_after"] == {"quant1.AUC": pytest.approx(0.82)}
    assert row["lift"] == pytest.approx(0.27, abs=1e-4)

    # Findings file: one TP row
    assert find_file.exists()
    find_rows = [json.loads(l) for l in find_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(find_rows) == 1
    fr = find_rows[0]
    assert fr["check_id"] == "QUANTITATIVE.quant1"
    assert fr["verdict"] == "true_positive"
    assert fr["source"] == "improvement_inferred"
    assert fr["weight"] == pytest.approx(0.5)

    # Sanity: the tracker also exposes stats consistent with what we wrote
    tracker = EffectivenessTracker(bundle.parent)
    stats = tracker.get_recommendation_stats("QUANTITATIVE.quant1")
    assert stats.recommendations_produced == 1
    assert stats.recommendations_improved == 1
    assert stats.mean_improvement_lift == pytest.approx(0.27, abs=1e-4)


def test_tracker_records_false_positive_when_improvement_regresses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When an applied recommendation regresses a previously-passing test
    (pass→warn), tracker must record:
      - one recommendation row with NEGATIVE lift
      - one FP finding row (source='improvement_inferred', weight 0.3)
    """
    bundle = _seed_bundle(tmp_path, original_verdict="pass", original_auc=0.85)

    monkeypatch.setenv("VERDICT_OVERRIDE", "warn")
    monkeypatch.setenv("METRIC_OVERRIDE", "0.50")
    monkeypatch.setenv("BUNDLE_ID", "bundle-eff")

    pipeline = AgenticRevalidationPipeline(
        bundle_id="bundle-eff",
        bundle_dir=bundle,
        config=ValidationConfig(),
        python_executable=sys.executable,
        execution_timeout_sec=30,
    )

    result = _run_async(pipeline.run(
        recommendations_applied=["QUANTITATIVE.quant1"],
        recommendations_skipped=[],
    ))

    assert result.verdict == "degraded"

    rec_file = bundle.parent / "validation_recommendations.jsonl"
    rec_rows = [json.loads(l) for l in rec_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rec_rows) == 1
    assert rec_rows[0]["lift"] < 0
    find_rows = [
        json.loads(l)
        for l in (bundle.parent / "validation_findings.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert len(find_rows) == 1
    assert find_rows[0]["verdict"] == "false_positive"
    assert find_rows[0]["source"] == "improvement_inferred"
    assert find_rows[0]["weight"] == pytest.approx(0.3)
