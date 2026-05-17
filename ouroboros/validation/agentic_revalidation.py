"""Agentic revalidation — Plan v2 Piece 1.

After the improver modifies a bundle's kernel, re-run THE SAME Phase B
``validation_project/`` on the improved kernel to get real per-test
verdict deltas + numeric metric deltas. This is a much stronger lift
signal than v1's re-run of seed S2-S8 checks because the tests were
authored *for this specific bundle's model* by the agentic session.

Execution model:
  1. Build a workdir under ``bundle_dir/improvement/revalidation_workdir/``
     that mirrors the bundle layout: ``raw/`` (data symlinked, model_code
     overridden with the improved copy), ``methodology/`` (symlinked).
  2. Subprocess: ``cd workdir; python methodology/validation_project/run_all.py
     --tests all --output ./results/results.json``.
  3. Parse the new results.json. Compare against the original at
     ``bundle_dir/results/results.json``.
  4. Compute per-test verdict transitions (categorical lift) and per-metric
     deltas (numeric lift).
  5. Aggregate into a :class:`RevalidationResult`. Persist alongside the
     improvement artifacts.

Effectiveness-tracker wiring is Phase 7; this module exposes the lift but
doesn't write to the tracker yet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from ouroboros.validation.types import (
    RevalidationResult,
    ValidationConfig,
)

log = logging.getLogger(__name__)


class AgenticRevalidationPipeline:
    """Re-run the Phase-B validation_project on the improved bundle.

    Constructor is cheap (just paths). :meth:`run` is async so it can be
    awaited inside the agent's task pipeline.
    """

    def __init__(
        self,
        bundle_id: str,
        bundle_dir: Path | str,
        config: Optional[ValidationConfig] = None,
        python_executable: Optional[str] = None,
        execution_timeout_sec: int = 1500,
    ) -> None:
        self.bundle_id = bundle_id
        self.bundle_dir = Path(bundle_dir).resolve()
        self.config = config or ValidationConfig()
        # Default to the same interpreter the runner is using — keeps unit
        # tests hermetic on environments without a system python3 on PATH.
        self.python_executable = python_executable or sys.executable
        self.execution_timeout_sec = execution_timeout_sec

        self._reval_dir = self.bundle_dir / "improvement" / "revalidation"
        self._workdir = self.bundle_dir / "improvement" / "revalidation_workdir"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        recommendations_applied: list[str],
        recommendations_skipped: list[tuple[str, str]],
    ) -> RevalidationResult:
        """Re-run the validation project on the improved bundle.

        Returns a :class:`RevalidationResult` with ``source='agentic-v2'`` —
        the ``categorical_lift`` and ``per_test_deltas`` fields are
        populated.
        """
        self._reval_dir.mkdir(parents=True, exist_ok=True)

        original_results = self._load_original_results()
        improved_results = await asyncio.to_thread(self._execute_improved)

        deltas, per_test = self._compute_per_test_deltas(
            original_results, improved_results
        )
        categorical = self._tally_categorical_transitions(per_test)
        original_metrics = self._flatten_metrics(original_results)
        improved_metrics = self._flatten_metrics(improved_results)
        metric_deltas = {
            k: round(improved_metrics.get(k, 0.0) - original_metrics.get(k, 0.0), 6)
            for k in set(original_metrics) | set(improved_metrics)
        }
        lift = self._aggregate_lift(metric_deltas, original_metrics, categorical)
        verdict = self._verdict_for(lift, categorical)

        result = RevalidationResult(
            original_bundle_id=self.bundle_id,
            improved_bundle_id=f"{self.bundle_id}_improved",
            original_metrics=original_metrics,
            improved_metrics=improved_metrics,
            metric_deltas=metric_deltas,
            improvement_lift=round(lift, 6),
            recommendations_applied=list(recommendations_applied),
            recommendations_skipped=[f"{cid}: {reason}" for cid, reason in recommendations_skipped],
            verdict=verdict,
            categorical_lift=categorical,
            per_test_deltas=per_test,
            source="agentic-v2",
        )

        # Persist the result + the improved results.json next to it
        (self._reval_dir / "revalidation_result.json").write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (self._reval_dir / "results_improved.json").write_text(
            json.dumps(improved_results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return result

    # ------------------------------------------------------------------
    # Workdir + execution
    # ------------------------------------------------------------------

    def _execute_improved(self) -> dict[str, Any]:
        """Build the mirror workdir, run the validation project, return
        the parsed improved ``results.json`` dict.

        Returns ``{}`` (empty dict) on execution failure so the caller's
        lift math can still proceed — every test will register as missing
        from improved which scores worst-case for those tests.
        """
        self._prepare_workdir()
        results_path = self._workdir / "results" / "results.json"
        results_path.parent.mkdir(parents=True, exist_ok=True)

        # Surface improved metrics via subprocess, not in-process — Phase B's
        # generated project may import heavy deps (numpy, sklearn, torch, …)
        # that we DON'T want polluting the runner's import table.
        cmd = [
            self.python_executable,
            str(self._workdir / "methodology" / "validation_project" / "run_all.py"),
            "--tests", "all",
            "--output", str(results_path),
        ]
        env = os.environ.copy()
        # Run under the workdir so any relative paths in the project resolve.
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self._workdir),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.execution_timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            log.warning(
                "Agentic revalidation timed out after %ss for bundle %s",
                self.execution_timeout_sec, self.bundle_id,
            )
            self._dump_execution_log(
                cmd, returncode=-1, stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + "\n[TIMEOUT]",
                duration_sec=time.monotonic() - t0,
            )
            return {}
        duration = time.monotonic() - t0
        self._dump_execution_log(cmd, proc.returncode, proc.stdout, proc.stderr, duration)

        if proc.returncode != 0 or not results_path.exists():
            log.warning(
                "Revalidation run_all.py exited %s for bundle %s; "
                "stderr tail: %s",
                proc.returncode, self.bundle_id, (proc.stderr or "")[-400:],
            )
            return {}

        try:
            return json.loads(results_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("Could not parse improved results.json: %s", exc)
            return {}

    def _prepare_workdir(self) -> None:
        """Build a mirror of the bundle layout where:
          - ``./raw/data`` is a symlink to the original ``raw/data``
          - ``./raw/model_code`` is a symlink to ``improvement/implementation``
          - any other ``./raw/*`` is symlinked through to the original
          - ``./methodology`` is a symlink to the original methodology
          - ``./results`` is fresh

        Symlinks (not copies) so we don't bloat disk on big data files,
        and so accidentally touching the improved kernel inside the workdir
        propagates to the actual improvement directory (which is also
        useful for audit).
        """
        if self._workdir.exists():
            shutil.rmtree(self._workdir)
        self._workdir.mkdir(parents=True)

        raw_src = self.bundle_dir / "raw"
        raw_dst = self._workdir / "raw"
        raw_dst.mkdir()
        if raw_src.exists():
            for entry in raw_src.iterdir():
                if entry.name == "model_code":
                    continue  # overridden below
                (raw_dst / entry.name).symlink_to(entry.resolve())
        # model_code → improvement/implementation
        improved_model = self.bundle_dir / "improvement" / "implementation"
        if improved_model.exists():
            (raw_dst / "model_code").symlink_to(improved_model.resolve())
        else:
            # Fallback to original — improver may have skipped all recs;
            # revalidation should still run and just report unchanged.
            orig_model = raw_src / "model_code"
            if orig_model.exists():
                (raw_dst / "model_code").symlink_to(orig_model.resolve())

        meth_src = self.bundle_dir / "methodology"
        if meth_src.exists():
            (self._workdir / "methodology").symlink_to(meth_src.resolve())

        (self._workdir / "results").mkdir(exist_ok=True)

    def _dump_execution_log(
        self, cmd: list[str], returncode: int, stdout: str, stderr: str,
        duration_sec: float,
    ) -> None:
        try:
            (self._reval_dir / "execution.log").write_text(
                "$ " + " ".join(cmd) + "\n"
                + f"# returncode={returncode} duration={duration_sec:.1f}s\n"
                + "\n# stdout (tail 4k)\n"
                + (stdout or "")[-4096:]
                + "\n\n# stderr (tail 4k)\n"
                + (stderr or "")[-4096:]
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not write execution.log: %s", exc)

    # ------------------------------------------------------------------
    # Lift computation
    # ------------------------------------------------------------------

    def _load_original_results(self) -> dict[str, Any]:
        path = self.bundle_dir / "results" / "results.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Original agentic results.json missing at {path}; cannot "
                "compute lift without a baseline."
            )
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _index_tests(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for t in results.get("tests", []) or []:
            tid = t.get("id")
            if tid:
                out[tid] = t
        return out

    def _compute_per_test_deltas(
        self,
        original: dict[str, Any],
        improved: dict[str, Any],
    ) -> tuple[dict[str, float], list[dict[str, Any]]]:
        """Return ``(metric_deltas, per_test_deltas)``.

        ``per_test_deltas`` is a list of dicts, one per test that appears
        in EITHER original or improved, with keys::

            {id, name, block, verdict_before, verdict_after,
             metric_before, metric_after, metric_delta}

        ``metric_delta`` is null when neither side has a numeric metric.
        """
        orig_by_id = self._index_tests(original)
        impr_by_id = self._index_tests(improved)
        all_ids = sorted(set(orig_by_id) | set(impr_by_id))

        per_test: list[dict[str, Any]] = []
        # Per-test metric_deltas keyed by ``test_id.metric_name`` for the
        # numeric lift aggregation path.
        metric_deltas: dict[str, float] = {}

        for tid in all_ids:
            o = orig_by_id.get(tid) or {}
            i = impr_by_id.get(tid) or {}
            o_metric = o.get("metric") if isinstance(o.get("metric"), dict) else None
            i_metric = i.get("metric") if isinstance(i.get("metric"), dict) else None

            entry: dict[str, Any] = {
                "id": tid,
                "name": (i.get("name") or o.get("name") or tid),
                "block": (i.get("block") or o.get("block") or "unknown"),
                "verdict_before": (o.get("verdict") or "missing"),
                "verdict_after": (i.get("verdict") or "missing"),
                "metric_before": o_metric,
                "metric_after": i_metric,
                "metric_delta": None,
            }

            if o_metric and i_metric:
                deltas: dict[str, float] = {}
                for k in set(o_metric) | set(i_metric):
                    ov = o_metric.get(k)
                    iv = i_metric.get(k)
                    if isinstance(ov, (int, float)) and isinstance(iv, (int, float)):
                        d = float(iv) - float(ov)
                        deltas[k] = round(d, 6)
                        metric_deltas[f"{tid}.{k}"] = round(d, 6)
                entry["metric_delta"] = deltas
            per_test.append(entry)

        return metric_deltas, per_test

    @staticmethod
    def _tally_categorical_transitions(per_test: list[dict[str, Any]]) -> dict[str, int]:
        """Count verdict transitions across tests.

        Keys like ``fail_to_pass``, ``pass_to_fail``, ``unchanged``,
        ``warn_to_pass`` etc. Useful for "did this improvement fix what
        was failing?"
        """
        counts: dict[str, int] = {}
        for entry in per_test:
            before = entry.get("verdict_before") or "missing"
            after = entry.get("verdict_after") or "missing"
            if before == after:
                key = f"unchanged_{before}"
            else:
                key = f"{before}_to_{after}"
            counts[key] = counts.get(key, 0) + 1
        return counts

    @staticmethod
    def _flatten_metrics(results: dict[str, Any]) -> dict[str, float]:
        """Flatten every test's numeric metrics into a single dict for
        compat with the v1 RevalidationResult shape.

        Keys are ``{test_id}.{metric_name}`` to avoid collisions when
        two tests report the same metric (e.g. two AUC tests).
        """
        out: dict[str, float] = {}
        for t in results.get("tests", []) or []:
            tid = t.get("id") or "?"
            m = t.get("metric")
            if not isinstance(m, dict):
                continue
            for k, v in m.items():
                if isinstance(v, (int, float)):
                    out[f"{tid}.{k}"] = float(v)
        return out

    def _aggregate_lift(
        self,
        metric_deltas: dict[str, float],
        original_metrics: dict[str, float],
        categorical: dict[str, int],
    ) -> float:
        """Combine numeric metric lift with categorical verdict lift.

        Numeric lift component: mean of per-metric proportional improvement
        (delta / |original|). Bounded loosely; metrics near zero are
        skipped to avoid divide-by-zero blowups.

        Categorical lift component: net (fail_to_pass + warn_to_pass) -
        (pass_to_fail + pass_to_warn). Scaled so that fixing two failures
        is comparable to a ~0.4 numeric lift, which we treat as "very good".

        These two components are summed. The threshold for an "improved"
        verdict is configurable via ``ValidationConfig.improvement_lift_threshold``.
        """
        numeric_components: list[float] = []
        for k, delta in metric_deltas.items():
            base = original_metrics.get(k)
            if base is not None and abs(base) > 1e-9:
                numeric_components.append(delta / abs(base))
        numeric_lift = (
            sum(numeric_components) / len(numeric_components) if numeric_components else 0.0
        )

        wins = categorical.get("fail_to_pass", 0) + categorical.get("warn_to_pass", 0)
        losses = categorical.get("pass_to_fail", 0) + categorical.get("pass_to_warn", 0)
        net_categorical = wins - losses
        categorical_lift = 0.2 * net_categorical

        return numeric_lift + categorical_lift

    def _verdict_for(self, lift: float, categorical: dict[str, int]) -> str:
        thr = self.config.improvement_lift_threshold
        # Mixed = something improved AND something else regressed
        improved_any = categorical.get("fail_to_pass", 0) + categorical.get("warn_to_pass", 0) > 0
        regressed_any = categorical.get("pass_to_fail", 0) + categorical.get("pass_to_warn", 0) > 0
        if improved_any and regressed_any:
            return "mixed"
        if lift > thr:
            return "improved"
        if lift < -thr:
            return "degraded"
        return "unchanged"
