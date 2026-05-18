"""Run the improvement cycle on N selected bundles.

For each bundle:
  1. Load its hard_recommendations from results/report.json
  2. Build a ModelSandbox + ModelImprover
  3. Call improver.implement() — Claude rewrites the bundle in the sandbox
  4. Run RevalidationPipeline.run() — re-runs S2-S8 on the improved model
  5. Record the lift

Bundle selection: pick the 3 smallest bundles that have at least one
hard_recommendation, so the demo finishes in a reasonable time.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DATA_DIR = pathlib.Path.home() / "Ouroboros" / "data"
PROGRESS_LOG = pathlib.Path("/tmp/improvement_cycle.jsonl")


def _emit(event: dict) -> None:
    from datetime import datetime, timezone
    event["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = json.dumps(event, ensure_ascii=False)
    print(line, flush=True)
    with PROGRESS_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _candidate_bundles(n: int) -> list[pathlib.Path]:
    """Return N bundle dirs that (a) have a hard_recommendation and
    (b) are smallest by on-disk size — biases toward fast iteration."""
    candidates: list[tuple[int, pathlib.Path]] = []
    for d in (DATA_DIR / "validations").iterdir():
        rj = d / "results" / "report.json"
        if not rj.exists():
            continue
        try:
            r = json.loads(rj.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not r.get("hard_recommendations"):
            continue
        # Sum the bundle's on-disk size (raw + extracted)
        size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
        candidates.append((size, d))
    candidates.sort()
    return [d for _, d in candidates[:n]]


async def _improve_one(bundle_dir: pathlib.Path) -> dict:
    from ouroboros.validation.config_loader import load_validation_config
    from ouroboros.validation.model_improver import ModelImprover
    from ouroboros.validation.pipeline import RevalidationPipeline
    from ouroboros.validation.sandbox import ModelSandbox
    from ouroboros.validation.types import ImprovementRecommendation

    bundle_id = bundle_dir.name
    _emit({"event": "bundle_start", "bundle_id": bundle_id})

    report = json.loads((bundle_dir / "results" / "report.json").read_text(encoding="utf-8"))
    raw_recs = report.get("hard_recommendations", [])
    if not raw_recs:
        _emit({"event": "bundle_skip", "bundle_id": bundle_id, "reason": "no_hard_recs"})
        return {"bundle_id": bundle_id, "ok": False, "reason": "no_hard_recs"}

    # Re-hydrate ImprovementRecommendation dataclasses from the JSON
    recs = []
    for raw in raw_recs:
        recs.append(ImprovementRecommendation(
            finding_check_id=str(raw.get("finding_check_id", "")),
            problem=str(raw.get("problem", "")),
            recommendation=str(raw.get("recommendation", "")),
            kind=str(raw.get("kind", "hard")),
            implementation_sketch=str(raw.get("implementation_sketch", "")),
            estimated_metric_impact=str(raw.get("estimated_metric_impact", "")),
            confidence=float(raw.get("confidence", 0.5)),
            effort=str(raw.get("effort", "medium")),
            priority=int(raw.get("priority", 5)),
        ))
    _emit({"event": "recs_loaded", "bundle_id": bundle_id, "n_recs": len(recs),
           "rec_summary": [r.recommendation[:100] for r in recs]})

    config = load_validation_config()
    sandbox = ModelSandbox(bundle_dir=bundle_dir, config=config)
    improver = ModelImprover(
        bundle_dir=bundle_dir, recommendations=recs,
        sandbox=sandbox, config=config,
    )

    t0 = time.monotonic()
    try:
        # improver.implement() is async — await it directly. Wrapping in
        # run_in_executor was wrong: it would return the un-awaited
        # coroutine object instead of running the LLM-driven sandbox edits.
        result = await improver.implement()
        impl_sec = time.monotonic() - t0
        applied = getattr(result, "recommendations_applied", None)
        if applied is None:
            applied = getattr(result, "applied", []) or []
        skipped = getattr(result, "recommendations_skipped", None)
        if skipped is None:
            skipped = getattr(result, "skipped", []) or []
        _emit({
            "event": "improver_done", "bundle_id": bundle_id,
            "duration_sec": round(impl_sec, 1),
            "applied": [str(x) for x in applied],
            "skipped": [str(x) for x in skipped],
            "result_summary": str(result)[:400],
        })
    except Exception as e:
        _emit({"event": "improver_failed", "bundle_id": bundle_id,
               "error": f"{type(e).__name__}: {e}"})
        return {"bundle_id": bundle_id, "ok": False, "reason": "improver_failed"}

    # Revalidate
    try:
        revalidator = RevalidationPipeline(
            bundle_id=bundle_id, bundle_dir=bundle_dir,
            repo_dir=REPO, config=config,
        )
        original_metrics = {}
        if "meta_scores" in report and isinstance(report["meta_scores"], dict):
            original_metrics = {k: float(v) for k, v in report["meta_scores"].items()
                                if isinstance(v, (int, float))}
        applied_ids: list[str] = []
        for a in (getattr(result, "recommendations_applied", None) or
                  getattr(result, "applied", []) or []):
            if isinstance(a, str):
                applied_ids.append(a)
            else:
                applied_ids.append(getattr(a, "finding_check_id", str(a)))
        skipped_pairs: list = []
        for s in (getattr(result, "recommendations_skipped", None) or
                  getattr(result, "skipped", []) or []):
            if isinstance(s, (tuple, list)) and len(s) == 2:
                skipped_pairs.append((str(s[0]), str(s[1])))
            else:
                skipped_pairs.append((str(s), "no_reason"))
        t1 = time.monotonic()
        reval = await revalidator.run(original_metrics, applied_ids, skipped_pairs)
        reval_sec = time.monotonic() - t1
        _emit({
            "event": "revalidation_done", "bundle_id": bundle_id,
            "duration_sec": round(reval_sec, 1),
            "result_summary": str(reval)[:600],
        })
        return {"bundle_id": bundle_id, "ok": True,
                "applied": applied_ids,
                "revalidation": str(reval)[:600]}
    except Exception as e:
        _emit({"event": "revalidation_failed", "bundle_id": bundle_id,
               "error": f"{type(e).__name__}: {e}"})
        return {"bundle_id": bundle_id, "ok": False, "reason": "revalidation_failed"}


async def main() -> int:
    PROGRESS_LOG.unlink(missing_ok=True)
    bundles = _candidate_bundles(3)
    _emit({"event": "run_start", "selected_bundles": [d.name for d in bundles]})
    if not bundles:
        _emit({"event": "no_candidates"})
        return 1
    results = []
    for d in bundles:
        results.append(await _improve_one(d))
    _emit({"event": "run_done", "results": results})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
