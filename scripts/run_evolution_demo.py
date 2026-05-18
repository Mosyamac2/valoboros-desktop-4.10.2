"""End-to-end evolution-cycle orchestrator for the 20 Kaggle bundles.

For each ZIP in ~/Ouroboros/data/ml-models-to-validate/:
  1. ingest into ~/Ouroboros/data/validations/{bundle_id}/
  2. run the full S0-S9 validation pipeline
  3. (later, optional) improvement cycle + revalidation

After all bundles done:
  - run the reflection engine to aggregate patterns
  - print the resulting validation_patterns.md and effectiveness metrics

Writes a JSONL progress trail to /tmp/evolution_demo.jsonl so the parent
session can monitor every state change without polling.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import time
from datetime import datetime, timezone


REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DATA_DIR = pathlib.Path.home() / "Ouroboros" / "data"
INBOX = DATA_DIR / "ml-models-to-validate"
VALIDATIONS_DIR = DATA_DIR / "validations"
PROGRESS_LOG = pathlib.Path("/tmp/evolution_demo.jsonl")


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit(event: dict) -> None:
    event["ts"] = _utc()
    line = json.dumps(event, ensure_ascii=False)
    # stdout for the Monitor stream
    print(line, flush=True)
    # JSONL trail for post-run analysis
    with PROGRESS_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


async def _validate_one(zip_path: pathlib.Path) -> dict:
    """Ingest one bundle ZIP then run the full S0-S9 pipeline.

    Returns a summary dict (no exception propagation; failures recorded
    in the dict's ``error`` key).
    """
    slug = zip_path.stem.replace("_kaggle_model", "")
    _emit({"event": "bundle_start", "slug": slug, "zip_size_mb": zip_path.stat().st_size // (1024 * 1024)})

    from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
    from ouroboros.validation.config_loader import load_validation_config
    from ouroboros.validation.pipeline import ValidationPipeline

    config = load_validation_config()
    VALIDATIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Use the description text inside the bundle as the task. We pull it
    # back out of the ZIP after ingest by reading whatever .txt files
    # ended up in raw/model_code/.
    t0 = time.monotonic()
    try:
        bundle_id = _ingest_model_artifacts_impl(
            validations_dir=VALIDATIONS_DIR,
            model_code_zip=str(zip_path),
            task=f"Validate the Kaggle competition model bundled in {zip_path.name}. "
                 f"See kaggle_overview.txt or similar description file inside the bundle "
                 f"for competition task, evaluation metric, and source kernel.",
        )
        ingest_sec = time.monotonic() - t0
        _emit({"event": "bundle_ingested", "slug": slug, "bundle_id": bundle_id,
               "ingest_sec": round(ingest_sec, 1)})
    except Exception as e:
        _emit({"event": "bundle_failed", "slug": slug, "stage": "ingest",
               "error": f"{type(e).__name__}: {e}"})
        return {"slug": slug, "ok": False, "stage_failed": "ingest", "error": str(e)}

    bundle_dir = VALIDATIONS_DIR / bundle_id

    try:
        pipeline = ValidationPipeline(
            bundle_id=bundle_id,
            bundle_dir=bundle_dir,
            repo_dir=REPO,
            config=config,
        )
        report = await pipeline.run()
        duration_sec = time.monotonic() - t0
        verdict = (
            "PASSED" if all(s.status in ("ok", "passed") for s in report.stages)
            else "WITH_FINDINGS"
        )
        n_findings = sum(
            sum(1 for c in s.checks if not c.passed)
            for s in report.stages
        )
        _emit({"event": "bundle_done", "slug": slug, "bundle_id": bundle_id,
               "verdict": verdict, "duration_sec": round(duration_sec, 1),
               "stages_run": len(report.stages), "findings": n_findings})
        return {"slug": slug, "ok": True, "bundle_id": bundle_id,
                "findings": n_findings, "duration_sec": duration_sec}
    except Exception as e:
        duration_sec = time.monotonic() - t0
        _emit({"event": "bundle_failed", "slug": slug, "bundle_id": bundle_id,
               "stage": "pipeline", "duration_sec": round(duration_sec, 1),
               "error": f"{type(e).__name__}: {e}"})
        return {"slug": slug, "ok": False, "stage_failed": "pipeline",
                "bundle_id": bundle_id, "error": str(e)}


async def _run_reflection() -> None:
    _emit({"event": "reflection_start"})
    try:
        from ouroboros.validation.reflection_engine import ValidationReflectionEngine
        from ouroboros.validation.config_loader import load_validation_config
        config = load_validation_config()
        engine = ValidationReflectionEngine(
            validations_dir=VALIDATIONS_DIR,
            knowledge_dir=DATA_DIR / "memory" / "knowledge",
            config=config,
        )
        # reflect_sync is the synchronous entrypoint per the engine's public API.
        result = engine.reflect_sync()
        _emit({"event": "reflection_done",
               "patterns": getattr(result, "patterns_found", None),
               "summary": str(result)[:300] if result else None})
    except Exception as e:
        _emit({"event": "reflection_failed", "error": f"{type(e).__name__}: {e}"})


async def _snapshot_effectiveness() -> None:
    try:
        from ouroboros.validation.effectiveness import EffectivenessTracker
        tracker = EffectivenessTracker(data_root=DATA_DIR)
        platform = tracker.get_platform_metrics()
        _emit({
            "event": "effectiveness",
            "platform_metrics": platform if isinstance(platform, dict) else str(platform)[:400],
        })
    except Exception as e:
        _emit({"event": "effectiveness_failed", "error": f"{type(e).__name__}: {e}"})


async def main() -> int:
    PROGRESS_LOG.unlink(missing_ok=True)
    zips = sorted(INBOX.glob("*.zip"))
    _emit({"event": "run_start", "n_bundles": len(zips),
           "validation_models": {
               "comprehension": os.environ.get("OUROBOROS_VALIDATION_COMPREHENSION_MODEL", "(default)"),
               "synthesis": os.environ.get("OUROBOROS_VALIDATION_SYNTHESIS_MODEL", "(default)"),
               "main": os.environ.get("OUROBOROS_MODEL", "(default)"),
           }})

    results: list[dict] = []
    for i, zp in enumerate(zips, 1):
        _emit({"event": "progress", "i": i, "total": len(zips)})
        r = await _validate_one(zp)
        results.append(r)
        # Brief pace between bundles — keeps subscription rate-limit-friendly
        await asyncio.sleep(3)
        if i % 5 == 0:
            await _snapshot_effectiveness()

    await _snapshot_effectiveness()
    await _run_reflection()

    n_ok = sum(1 for r in results if r["ok"])
    _emit({"event": "run_done", "ok": n_ok, "total": len(zips),
           "results": results})
    return 0 if n_ok == len(zips) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
