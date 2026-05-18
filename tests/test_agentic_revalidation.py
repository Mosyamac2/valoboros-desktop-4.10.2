"""Phase 6 tests — AgenticRevalidationPipeline.

The pipeline subprocesses out to a real ``run_all.py``. The tests lay
down a tiny synthetic validation_project whose runner reads its verdict
from an env var, so we can drive the improved-bundle outcome to whatever
the test needs (improvement / regression / mixed) without coupling to a
real ML stack.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from ouroboros.validation.agentic_revalidation import AgenticRevalidationPipeline
from ouroboros.validation.types import RevalidationResult, ValidationConfig


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_TINY_RUN_ALL = '''
"""Synthetic validation_project runner for tests.

Reads VERDICT_OVERRIDE / METRIC_OVERRIDE from env and writes results.json
with two tests (q1 qualitative, quant1 quantitative). Defaults model the
"original" bundle pre-improvement.
"""
import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests", default="all")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    verdict = os.environ.get("VERDICT_OVERRIDE", "fail")
    metric = float(os.environ.get("METRIC_OVERRIDE", "0.55"))

    results = {
        "schema_version": "1",
        "bundle_id": os.environ.get("BUNDLE_ID", "bundle-synth"),
        "tests": [
            {"id": "q1", "name": "target column", "block": "qualitative",
             "verdict": "pass", "metric": None,
             "evidence": "ok", "error": None},
            {"id": "quant1", "name": "OOS AUC", "block": "quantitative",
             "verdict": verdict, "metric": {"AUC": metric},
             "evidence": f"AUC={metric}", "error": None},
        ],
        "summary": {
            "n_pass": 1 + (1 if verdict == "pass" else 0),
            "n_warn": 1 if verdict == "warn" else 0,
            "n_fail": 1 if verdict == "fail" else 0,
            "n_deferred": 0,
            "n_error": 0,
        },
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _seed_bundle_with_original_results(
    tmp_path: Path,
    original_verdict: str = "fail",
    original_auc: float = 0.55,
) -> Path:
    """Create a bundle on disk with raw/, methodology/, results/results.json
    (the original Phase C output) and a synthetic validation_project."""
    bundle = tmp_path / "bundle-synth"
    (bundle / "raw" / "data").mkdir(parents=True)
    (bundle / "raw" / "model_code").mkdir(parents=True)
    (bundle / "raw" / "data" / "sample.csv").write_text(
        "x,y\n1,0\n2,1\n", encoding="utf-8"
    )
    (bundle / "raw" / "model_code" / "model.py").write_text(
        "# original (unfixed) model\n", encoding="utf-8"
    )

    (bundle / "methodology" / "validation_project").mkdir(parents=True)
    (bundle / "methodology" / "validation_project" / "run_all.py").write_text(
        _TINY_RUN_ALL, encoding="utf-8"
    )
    (bundle / "methodology" / "validation_project" / "requirements.txt").write_text(
        "# none\n", encoding="utf-8"
    )
    (bundle / "methodology" / "methodology.md").write_text(
        "# Methodology\n", encoding="utf-8"
    )

    # Original Phase C results — corresponds to original_verdict / auc
    (bundle / "results").mkdir(parents=True)
    summary = {
        "n_pass": 1 + (1 if original_verdict == "pass" else 0),
        "n_warn": 1 if original_verdict == "warn" else 0,
        "n_fail": 1 if original_verdict == "fail" else 0,
        "n_deferred": 0,
        "n_error": 0,
    }
    original_results = {
        "schema_version": "1",
        "bundle_id": "bundle-synth",
        "tests": [
            {"id": "q1", "name": "target column", "block": "qualitative",
             "verdict": "pass", "metric": None,
             "evidence": "ok", "error": None},
            {"id": "quant1", "name": "OOS AUC", "block": "quantitative",
             "verdict": original_verdict, "metric": {"AUC": original_auc},
             "evidence": f"AUC={original_auc}", "error": None},
        ],
        "summary": summary,
    }
    (bundle / "results" / "results.json").write_text(
        json.dumps(original_results, indent=2), encoding="utf-8"
    )

    # The improver would have copied raw/model_code/ into improvement/implementation/
    # We set this up here directly with a tweaked model.
    (bundle / "improvement" / "implementation").mkdir(parents=True)
    (bundle / "improvement" / "implementation" / "model.py").write_text(
        "# improved model\n", encoding="utf-8"
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

def test_revalidation_detects_improvement_and_records_categorical_lift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bundle starts with quant1 failing at AUC=0.55. After improvement,
    the synthetic runner emits pass with AUC=0.82. The pipeline must
    report:
      - verdict='improved'
      - categorical_lift['fail_to_pass'] == 1
      - per_test_deltas: quant1 verdict_before=fail, verdict_after=pass
      - metric_deltas['quant1.AUC'] ≈ 0.27
      - source='agentic-v2'
    """
    bundle = _seed_bundle_with_original_results(
        tmp_path, original_verdict="fail", original_auc=0.55
    )

    # Drive the synthetic runner to emit pass/0.82
    monkeypatch.setenv("VERDICT_OVERRIDE", "pass")
    monkeypatch.setenv("METRIC_OVERRIDE", "0.82")
    monkeypatch.setenv("BUNDLE_ID", "bundle-synth")

    pipeline = AgenticRevalidationPipeline(
        bundle_id="bundle-synth",
        bundle_dir=bundle,
        config=ValidationConfig(),
        python_executable=sys.executable,
        execution_timeout_sec=30,
    )

    result: RevalidationResult = _run_async(pipeline.run(
        recommendations_applied=["QUANTITATIVE.quant1"],
        recommendations_skipped=[],
    ))

    assert result.source == "agentic-v2"
    assert result.verdict == "improved"
    assert result.categorical_lift.get("fail_to_pass") == 1
    assert result.categorical_lift.get("unchanged_pass") == 1
    # per_test_deltas
    quant_entry = next(e for e in result.per_test_deltas if e["id"] == "quant1")
    assert quant_entry["verdict_before"] == "fail"
    assert quant_entry["verdict_after"] == "pass"
    assert quant_entry["metric_delta"] == {"AUC": pytest.approx(0.27, abs=1e-4)}
    # Flattened metric_deltas
    assert result.metric_deltas["quant1.AUC"] == pytest.approx(0.27, abs=1e-4)
    # Improved metrics carried over
    assert result.improved_metrics["quant1.AUC"] == pytest.approx(0.82)
    # On-disk persistence
    reval_dir = bundle / "improvement" / "revalidation"
    assert (reval_dir / "revalidation_result.json").exists()
    assert (reval_dir / "results_improved.json").exists()
    assert (reval_dir / "execution.log").exists()
    # Workdir actually got built with a symlink to the improved model
    workdir = bundle / "improvement" / "revalidation_workdir"
    assert (workdir / "raw" / "model_code").is_symlink()
    # And the symlink target is the improved model
    target = (workdir / "raw" / "model_code").resolve()
    assert target == (bundle / "improvement" / "implementation").resolve()


def test_revalidation_handles_regression_with_degraded_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bundle starts with quant1 passing at AUC=0.85. After 'improvement'
    the model is actually worse: AUC drops to 0.40 (warn) AND a previously
    passing test now warns. The pipeline must report:
      - verdict='degraded' (no improvements, regressions present)
      - categorical_lift['pass_to_warn'] >= 1
      - Negative metric_delta on quant1.AUC
    """
    bundle = _seed_bundle_with_original_results(
        tmp_path, original_verdict="pass", original_auc=0.85
    )

    monkeypatch.setenv("VERDICT_OVERRIDE", "warn")
    monkeypatch.setenv("METRIC_OVERRIDE", "0.40")
    monkeypatch.setenv("BUNDLE_ID", "bundle-synth")

    pipeline = AgenticRevalidationPipeline(
        bundle_id="bundle-synth",
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
    assert result.categorical_lift.get("pass_to_warn") == 1
    quant_entry = next(e for e in result.per_test_deltas if e["id"] == "quant1")
    assert quant_entry["verdict_before"] == "pass"
    assert quant_entry["verdict_after"] == "warn"
    assert quant_entry["metric_delta"]["AUC"] == pytest.approx(-0.45, abs=1e-4)
    assert result.metric_deltas["quant1.AUC"] < 0
    # Improvement lift must be negative
    assert result.improvement_lift < 0
