"""Phase 11 tests — end-to-end orchestrator across stages 2-6.

We skip Stage 1 (real Claude Code SDK invocation) by pre-seeding two
bundles with valid agentic artifacts. Stages 2-6 then run for real on
that synthetic data: legacy bridge → revalidation v2 → reflection →
evolution proposals → source-evolution attempts (dry-run).

A single integration test verifies that the full loop produces all
expected on-disk artifacts and a coherent end-state.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.run_agentic_validation_demo import amain  # noqa: E402


_TINY_RUN_ALL = '''
import argparse, json, os, sys
parser = argparse.ArgumentParser()
parser.add_argument("--tests", default="all")
parser.add_argument("--output", required=True)
args = parser.parse_args()
verdict = os.environ.get("VERDICT_OVERRIDE", "pass")
metric = float(os.environ.get("METRIC_OVERRIDE", "0.85"))
results = {
    "schema_version": "1",
    "bundle_id": os.environ.get("BUNDLE_ID", "bundle-x"),
    "tests": [
        {"id": "qsmells", "name": "code smells in kernel", "block": "qualitative",
         "verdict": verdict, "metric": None, "evidence": "smells",
         "error": None},
        {"id": "quant1", "name": "OOS AUC on holdout", "block": "quantitative",
         "verdict": verdict, "metric": {"AUC": metric}, "evidence": "auc",
         "error": None},
    ],
    "summary": {
        "n_pass": 2 if verdict == "pass" else 0,
        "n_warn": 2 if verdict == "warn" else 0,
        "n_fail": 2 if verdict == "fail" else 0,
        "n_deferred": 0, "n_error": 0,
    },
}
with open(args.output, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
sys.exit(0)
'''


def _seed_bundle(
    validations_dir: Path, bundle_id: str,
    qsmells_verdict: str, quant_verdict: str, quant_auc: float,
) -> Path:
    bundle = validations_dir / bundle_id
    (bundle / "raw" / "data").mkdir(parents=True)
    (bundle / "raw" / "model_code").mkdir(parents=True)
    (bundle / "methodology" / "validation_project").mkdir(parents=True)
    (bundle / "methodology" / "validation_project" / "run_all.py").write_text(
        _TINY_RUN_ALL, encoding="utf-8"
    )
    (bundle / "methodology" / "validation_project" / "requirements.txt").write_text(
        "", encoding="utf-8"
    )
    (bundle / "methodology" / "methodology.md").write_text(
        "# Methodology\n## Block 1: Qualitative\n### qsmells — code smells\n"
        "## Block 2: Quantitative\n### quant1 — OOS AUC on holdout\n",
        encoding="utf-8",
    )
    (bundle / "results").mkdir(parents=True)
    results = {
        "schema_version": "1",
        "bundle_id": bundle_id,
        "tests": [
            {"id": "qsmells", "name": "code smells in kernel",
             "block": "qualitative", "verdict": qsmells_verdict,
             "metric": None, "evidence": "smells", "error": None},
            {"id": "quant1", "name": "OOS AUC on holdout",
             "block": "quantitative", "verdict": quant_verdict,
             "metric": {"AUC": quant_auc},
             "evidence": f"AUC={quant_auc}", "error": None},
        ],
        "summary": {
            "n_pass": (1 if qsmells_verdict == "pass" else 0)
                     + (1 if quant_verdict == "pass" else 0),
            "n_warn": 0,
            "n_fail": (1 if qsmells_verdict == "fail" else 0)
                     + (1 if quant_verdict == "fail" else 0),
            "n_deferred": 0, "n_error": 0,
        },
    }
    (bundle / "results" / "results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    (bundle / "results" / "interpretation.md").write_text(
        "# Interpretation\n\n## Verdict\nMixed.\n", encoding="utf-8"
    )
    # Improved bundle directory so revalidation runs
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


def test_demo_orchestrator_runs_all_post_validation_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: seed 3 bundles, run stages 2-6, verify every stage
    landed its expected artifact."""
    data_dir = tmp_path / "data"
    validations = data_dir / "validations"
    validations.mkdir(parents=True)
    knowledge = data_dir / "memory" / "knowledge"

    # Three bundles, two of which fail on qsmells → recurring failure
    # candidate. Improvement (synthetic runner) emits pass for everything,
    # so revalidation reports fail→pass transitions for the failures.
    _seed_bundle(validations, "bundle-a",
                 qsmells_verdict="fail", quant_verdict="pass", quant_auc=0.85)
    _seed_bundle(validations, "bundle-b",
                 qsmells_verdict="fail", quant_verdict="pass", quant_auc=0.81)
    _seed_bundle(validations, "bundle-c",
                 qsmells_verdict="pass", quant_verdict="pass", quant_auc=0.79)

    # Drive the synthetic runner to emit all-pass on the improved bundles
    monkeypatch.setenv("VERDICT_OVERRIDE", "pass")
    monkeypatch.setenv("METRIC_OVERRIDE", "0.90")

    # Build args namespace the way argparse would
    args = SimpleNamespace(
        data_dir=data_dir,
        all=False,
        skip_agentic=True,   # don't try to invoke real SDK
        apply_evolution=False,  # dry-run source evolution
    )

    rc = _run_async(amain(args))
    assert rc == 0

    # Stage 2 — bridge wrote report.json for each bundle
    for b in ("bundle-a", "bundle-b", "bundle-c"):
        assert (validations / b / "results" / "report.json").exists()

    # Stage 3 — revalidation wrote results_improved.json + revalidation_result.json
    for b in ("bundle-a", "bundle-b", "bundle-c"):
        reval = validations / b / "improvement" / "revalidation"
        assert (reval / "revalidation_result.json").exists()
        assert (reval / "results_improved.json").exists()

    # Stage 3 also writes to the tracker
    assert (validations / "validation_recommendations.jsonl").exists()
    assert (validations / "validation_findings.jsonl").exists()

    # Stage 4 — reflection wrote agentic_reflection.md
    assert (knowledge / "agentic_reflection.md").exists()

    # Stage 5 — evolution_proposals.jsonl exists with at least one entry
    proposals_file = knowledge / "evolution_proposals.jsonl"
    assert proposals_file.exists()
    proposal_rows = [
        json.loads(l)
        for l in proposals_file.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert len(proposal_rows) >= 1
    # The qsmells failure pattern produces a recurring_failure proposal that
    # targets the methodology prompt
    kinds = {p["target_kind"] for p in proposal_rows}
    assert "prompt" in kinds or "helper" in kinds

    # Stage 6 — evolution attempts file exists (dry-run only)
    attempts_file = knowledge / "evolution_attempts.jsonl"
    assert attempts_file.exists()
    attempt_rows = [
        json.loads(l)
        for l in attempts_file.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert len(attempt_rows) >= 1
    # Every dry-run attempt that passes validation is "planned" (not "applied")
    # Any allow-list violation would be "denied". All proposals from the
    # evolver target allow-listed paths so they should be "planned".
    assert all(r["dry_run"] is True for r in attempt_rows)
    assert all(r["outcome"] in {"planned", "denied"} for r in attempt_rows)
    # At least one should have been planned (not denied)
    assert any(r["outcome"] == "planned" for r in attempt_rows)
