"""End-to-end driver for the Plan v2 agentic validation loop.

Stages (each is independently invocable):

  1. Agentic validation per bundle  (A → B → C → D)
  2. Legacy bridge: results.json → ValidationReport (report.json)
  3. Revalidation v2 against improved bundles (if improved/ exists)
  4. Cross-bundle reflection
  5. Methodology evolution proposals
  6. Source-evolution attempts (dry-run by default)

The script is intentionally orchestration-only — every heavy operation is
delegated to its module, and the script reports a JSONL trail at
``/tmp/agentic_demo.jsonl`` plus emits a summary on stdout. Real Claude
Code SDK invocation only happens for Stage 1 (agentic validation) and
Stage 6's non-dry-run path; Stages 2-5 are pure-Python.

Usage::

    # Run all stages on every bundle in ~/Ouroboros/data/validations/
    python scripts/run_agentic_validation_demo.py --all

    # Only the post-validation analysis loop (stages 2-6)
    python scripts/run_agentic_validation_demo.py --skip-agentic

    # Apply evolution proposals for real (BEWARE: invokes claude_code_edit)
    python scripts/run_agentic_validation_demo.py --apply-evolution
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pathlib
import sys
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

log = logging.getLogger("agentic_demo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATA_DIR = pathlib.Path.home() / "Ouroboros" / "data"
PROGRESS_LOG = pathlib.Path("/tmp/agentic_demo.jsonl")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit(event: dict) -> None:
    event = {"ts": _ts(), **event}
    line = json.dumps(event, ensure_ascii=False, default=str)
    print(line, flush=True)
    try:
        with PROGRESS_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        log.warning("could not append progress log: %s", exc)


# ---------------------------------------------------------------------------
# Stage 1 — agentic validation per bundle (real SDK calls)
# ---------------------------------------------------------------------------

async def stage_agentic_validation(bundle_dirs: list[pathlib.Path]) -> list[dict]:
    from ouroboros.validation.agentic_runner import run_agentic_validation
    out: list[dict] = []
    for bundle_dir in bundle_dirs:
        _emit({"event": "agentic_start", "bundle_id": bundle_dir.name})
        try:
            agg = await run_agentic_validation(
                bundle_id=bundle_dir.name,
                bundle_dir=bundle_dir,
                model_type="unknown",
            )
            out.append(agg.to_dict())
            _emit({
                "event": "agentic_done",
                "bundle_id": bundle_dir.name,
                "success": agg.success,
                "phases": [p.phase for p in agg.phases],
                "total_cost_usd": agg.total_cost_usd,
            })
        except Exception as exc:
            _emit({"event": "agentic_failed", "bundle_id": bundle_dir.name,
                   "error": f"{type(exc).__name__}: {exc}"})
    return out


# ---------------------------------------------------------------------------
# Stage 2 — legacy bridge (pure Python; safe to always run)
# ---------------------------------------------------------------------------

def stage_legacy_bridge(bundle_dirs: list[pathlib.Path]) -> list[dict]:
    from ouroboros.validation.agentic_results_parser import parse_agentic_results
    out: list[dict] = []
    for bundle_dir in bundle_dirs:
        results_path = bundle_dir / "results" / "results.json"
        if not results_path.exists():
            _emit({"event": "bridge_skip", "bundle_id": bundle_dir.name,
                   "reason": "no_results_json"})
            continue
        try:
            report = parse_agentic_results(bundle_dir)
            out.append({
                "bundle_id": report.bundle_id,
                "overall_verdict": report.overall_verdict,
                "n_hard": len(report.hard_recommendations),
                "n_soft": len(report.soft_recommendations),
                "n_critical": len(report.critical_findings),
            })
            _emit({"event": "bridge_done", "bundle_id": bundle_dir.name,
                   "overall_verdict": report.overall_verdict,
                   "n_hard": len(report.hard_recommendations)})
        except Exception as exc:
            _emit({"event": "bridge_failed", "bundle_id": bundle_dir.name,
                   "error": f"{type(exc).__name__}: {exc}"})
    return out


# ---------------------------------------------------------------------------
# Stage 3 — revalidation v2 on bundles that have been improved
# ---------------------------------------------------------------------------

async def stage_revalidation(bundle_dirs: list[pathlib.Path]) -> list[dict]:
    from ouroboros.validation.agentic_revalidation import AgenticRevalidationPipeline
    from ouroboros.validation.config_loader import load_validation_config
    config = load_validation_config()
    out: list[dict] = []
    for bundle_dir in bundle_dirs:
        improved = bundle_dir / "improvement" / "implementation"
        if not improved.exists():
            _emit({"event": "reval_skip", "bundle_id": bundle_dir.name,
                   "reason": "no_improvement_implementation"})
            continue
        report_path = bundle_dir / "results" / "report.json"
        applied: list[str] = []
        if report_path.exists():
            try:
                rep = json.loads(report_path.read_text(encoding="utf-8"))
                applied = [
                    r.get("finding_check_id", "")
                    for r in rep.get("hard_recommendations", [])
                    if r.get("finding_check_id")
                ]
            except Exception:
                pass
        try:
            pipeline = AgenticRevalidationPipeline(
                bundle_id=bundle_dir.name, bundle_dir=bundle_dir, config=config,
            )
            result = await pipeline.run(
                recommendations_applied=applied,
                recommendations_skipped=[],
            )
            out.append(result.to_dict())
            _emit({"event": "reval_done", "bundle_id": bundle_dir.name,
                   "verdict": result.verdict,
                   "improvement_lift": result.improvement_lift,
                   "categorical_lift": result.categorical_lift})
        except Exception as exc:
            _emit({"event": "reval_failed", "bundle_id": bundle_dir.name,
                   "error": f"{type(exc).__name__}: {exc}"})
    return out


# ---------------------------------------------------------------------------
# Stage 4 — reflection
# ---------------------------------------------------------------------------

def stage_reflection(validations_dir: pathlib.Path, knowledge_dir: pathlib.Path):
    from ouroboros.validation.agentic_reflection import AgenticReflectionEngine
    engine = AgenticReflectionEngine(
        validations_dir=validations_dir, knowledge_dir=knowledge_dir,
    )
    result = engine.reflect()
    _emit({
        "event": "reflection_done",
        "total_analyzed": result.total_validations_analyzed,
        "n_patterns": len(result.patterns_found),
        "kinds": list({p.get("kind") for p in result.patterns_found}),
        "knowledge_entries": result.knowledge_entries_written,
    })
    return result


# ---------------------------------------------------------------------------
# Stage 5 — evolution proposals
# ---------------------------------------------------------------------------

def stage_evolver(reflection_result, knowledge_dir: pathlib.Path):
    from ouroboros.validation.agentic_evolver import AgenticEvolutionProposer
    proposer = AgenticEvolutionProposer(knowledge_dir=knowledge_dir)
    proposals = proposer.propose(reflection_result)
    _emit({
        "event": "evolution_proposals",
        "n_total": len(proposals),
        "n_persisted": sum(1 for p in proposals if p.confidence >= 0.5),
        "kinds": [p.target_kind for p in proposals],
    })
    return proposals


# ---------------------------------------------------------------------------
# Stage 6 — source-evolution attempts
# ---------------------------------------------------------------------------

def stage_source_evolution(proposals, repo_dir: pathlib.Path,
                           knowledge_dir: pathlib.Path, dry_run: bool):
    from ouroboros.validation.agentic_source_evolution import SourceEvolutionExecutor
    executor = SourceEvolutionExecutor(repo_dir=repo_dir, knowledge_dir=knowledge_dir)
    attempts = []
    for p in proposals:
        attempt = executor.attempt(p, dry_run=dry_run)
        attempts.append(attempt.to_dict())
        _emit({
            "event": "source_evolution_attempt",
            "proposal_id": p.proposal_id, "target_kind": p.target_kind,
            "target_path": p.target_path, "outcome": attempt.outcome,
            "dry_run": attempt.dry_run, "reason": attempt.reason,
        })
    return attempts


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

async def amain(args) -> int:
    PROGRESS_LOG.unlink(missing_ok=True)
    _emit({"event": "demo_start", "data_dir": str(args.data_dir)})

    validations_dir = args.data_dir / "validations"
    knowledge_dir = args.data_dir / "memory" / "knowledge"
    if not validations_dir.exists():
        _emit({"event": "no_validations_dir"})
        return 1

    bundle_dirs = [d for d in sorted(validations_dir.iterdir()) if d.is_dir()]
    if not bundle_dirs:
        _emit({"event": "no_bundles"})
        return 1

    if not args.skip_agentic:
        targets = bundle_dirs
        if args.max_bundles is not None and args.max_bundles > 0:
            targets = bundle_dirs[: args.max_bundles]
            _emit({"event": "agentic_capped", "n_target": len(targets),
                   "n_total": len(bundle_dirs), "max_bundles": args.max_bundles})
        await stage_agentic_validation(targets)

    stage_legacy_bridge(bundle_dirs)
    await stage_revalidation(bundle_dirs)

    reflection = stage_reflection(validations_dir, knowledge_dir)
    proposals = stage_evolver(reflection, knowledge_dir)
    env_flag = os.environ.get("OUROBOROS_APPLY_EVOLUTION", "").strip().lower()
    env_apply = env_flag in {"1", "true", "yes", "on"}
    apply_evolution = args.apply_evolution or env_apply
    stage_source_evolution(
        proposals, REPO, knowledge_dir,
        dry_run=not apply_evolution,
    )

    _emit({"event": "demo_done"})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR,
                        help="Validation data root; defaults to ~/Ouroboros/data")
    parser.add_argument("--all", action="store_true",
                        help="Run every stage. Same as no skip flags.")
    parser.add_argument("--skip-agentic", action="store_true",
                        help="Skip Stage 1 (agentic validation). Useful when "
                             "bundles already have results.json from a prior run.")
    parser.add_argument("--apply-evolution", action="store_true",
                        help="Stage 6 actually invokes claude_code_edit. "
                             "Default is dry-run. Equivalent to setting "
                             "OUROBOROS_APPLY_EVOLUTION=1 in the env.")
    parser.add_argument("--max-bundles", type=int, default=None,
                        help="Cap Stage 1 (agentic validation) to the first N "
                             "bundles. Useful for smoke-testing on a small "
                             "subset before committing the full subscription "
                             "rate window. Stages 2-6 still run over all "
                             "bundles that have results.json.")
    args = parser.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
