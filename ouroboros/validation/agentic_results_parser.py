"""Bridge: agentic ``results.json`` → legacy ``ValidationReport``.

The agentic four-phase flow produces:
  - ``./methodology/methodology.md``
  - ``./methodology/validation_project/`` (Phase B's code)
  - ``./results/results.json``         (schema_version="1")
  - ``./results/interpretation.md``
  - ``./results/report.md``

Downstream consumers — :mod:`ouroboros.validation.model_improver`,
:mod:`ouroboros.validation.effectiveness`, the reflection engine — expect
the legacy ``ValidationReport`` dataclass (``stages``,
``critical_findings``, ``hard_recommendations``, ``soft_recommendations``,
``meta_scores`` …). Rather than rewrite all of those, we translate the
agentic outputs into that shape here.

The mapping is documented inline. A canonical ``report.json`` is also
written alongside the agentic artifacts so reflection only ever needs to
read one format (per §10 risk 6 of the v2 plan).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ouroboros.validation.types import (
    CheckResult,
    ImprovementRecommendation,
    ValidationReport,
    ValidationStageResult,
)

log = logging.getLogger(__name__)


_VERDICT_TO_SEVERITY = {
    "pass": "pass",
    "warn": "warning",
    "fail": "critical",
    "error": "info",
    "deferred": "info",
}


def _verdict_passed(verdict: str) -> bool:
    """A test passed iff its verdict is exactly 'pass'. Everything else
    (warn / fail / error / deferred) is a non-pass — the system has to
    decide what to do with it."""
    return verdict == "pass"


def _coerce_score(metric: Any) -> Optional[float]:
    """The agentic schema stores ``metric`` as either ``null``, a single
    number, or a small dict like ``{"AUC": 0.82}``. ``CheckResult.score``
    is a single optional float — pick the first numeric value."""
    if metric is None:
        return None
    if isinstance(metric, (int, float)):
        return float(metric)
    if isinstance(metric, dict):
        for v in metric.values():
            if isinstance(v, (int, float)):
                return float(v)
    return None


def _check_id_for(test: dict[str, Any]) -> str:
    """Project an agentic test to a stable, namespaced check_id usable by
    the reflection engine. ``QUAL.q1`` / ``QUANT.quant3`` etc."""
    block = test.get("block", "").upper() or "UNK"
    tid = test.get("id") or "?"
    return f"{block}.{tid}"


def _extract_soft_findings_from_interpretation(interpretation_text: str) -> list[str]:
    """Heuristic: pull bullet items out of any 'Soft findings' section in
    ``interpretation.md``. Returns a list of free-form strings."""
    if not interpretation_text:
        return []
    out: list[str] = []
    section_match = re.search(
        r"(?im)^##\s*soft\s+findings[^\n]*\n(.*?)(?=^##\s|\Z)",
        interpretation_text,
        flags=re.DOTALL,
    )
    if not section_match:
        return []
    body = section_match.group(1)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*", "•")):
            text = stripped.lstrip("-*• ").strip()
            if text:
                out.append(text)
    return out


def _build_hard_recs(failed_tests: list[dict[str, Any]]) -> list[ImprovementRecommendation]:
    """One hard recommendation per failed test that has actionable evidence.

    A test with ``verdict=fail`` is by definition pointing at a real issue.
    The evidence string from ``results.json`` is the closest thing we have
    to a fix recipe — the Phase B / Phase C session already wrote it with
    the model owner in mind. We hand that to the improver as the
    recommendation body; the improver's own prompt builds the rewrite.
    """
    recs: list[ImprovementRecommendation] = []
    for i, t in enumerate(failed_tests, start=1):
        check_id = _check_id_for(t)
        evidence = t.get("evidence") or ""
        name = t.get("name") or check_id
        rec = ImprovementRecommendation(
            finding_check_id=check_id,
            problem=f"{name}: {evidence[:300]}",
            recommendation=evidence or f"Address the failure in {name}.",
            kind="hard",
            implementation_sketch="",
            estimated_metric_impact={},
            confidence=0.6,
            effort="moderate",
            priority=i,
        )
        recs.append(rec)
    return recs


def _build_soft_recs(soft_findings: list[str]) -> list[ImprovementRecommendation]:
    out: list[ImprovementRecommendation] = []
    for i, text in enumerate(soft_findings, start=1):
        out.append(ImprovementRecommendation(
            finding_check_id=f"SOFT.{i}",
            problem=text[:300],
            recommendation=text,
            kind="soft",
            implementation_sketch="",
            estimated_metric_impact={},
            confidence=0.5,
            effort="significant",
            priority=i + 100,
        ))
    return out


def _overall_verdict(summary: dict[str, Any], n_critical: int) -> str:
    """Aggregate: any fail OR critical finding → rejected; only warns →
    conditional; everything pass / deferred → approved."""
    if n_critical > 0 or summary.get("n_fail", 0) > 0:
        return "rejected"
    if summary.get("n_warn", 0) > 0 or summary.get("n_error", 0) > 0:
        return "conditional"
    return "approved"


def _aggregate_meta_scores(tests: list[dict[str, Any]]) -> dict[str, float]:
    """Flatten every test's numeric metric into one dict for ``meta_scores``.

    Conflicts (two tests reporting the same metric name) prefer the later
    test, since methodologies tend to refine — and the reflection engine
    won't care which one wins for cross-bundle counting. Quantitative tests
    are weighted equally.
    """
    out: dict[str, float] = {}
    for t in tests:
        m = t.get("metric")
        if not isinstance(m, dict):
            continue
        for k, v in m.items():
            if isinstance(v, (int, float)):
                out[k] = float(v)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_agentic_results(
    bundle_dir: Path | str,
    bundle_id: Optional[str] = None,
    pre_check_summary: Optional[dict[str, Any]] = None,
    write_legacy_report: bool = True,
    methodology_snapshot: str = "agentic-v2",
) -> ValidationReport:
    """Translate the agentic artifacts in ``bundle_dir`` into a legacy
    :class:`ValidationReport`.

    Args:
      bundle_dir: per-bundle workdir produced by the agentic runner.
      bundle_id: override for ``report.bundle_id``. Defaults to
        ``results.json``'s ``bundle_id`` field, falling back to
        ``bundle_dir.name``.
      pre_check_summary: the fast deterministic pre-check summary, if any.
        Stored on the resulting report's ``meta_scores`` under the
        ``pre_check_*`` namespace so reflection can correlate.
      write_legacy_report: persist a ``results/report.json`` alongside the
        agentic outputs so the reflection engine only has to read one
        format. Defaults to True.
      methodology_snapshot: opaque tag put into ``report.methodology_snapshot``.

    Raises:
      FileNotFoundError if ``results/results.json`` is missing — the agentic
      flow guarantees that file exists; absence means the validation never
      ran or crashed before Phase C.
    """
    bundle_dir = Path(bundle_dir).resolve()
    results_path = bundle_dir / "results" / "results.json"
    if not results_path.exists():
        raise FileNotFoundError(
            f"Agentic results.json missing at {results_path}; cannot parse legacy report."
        )

    results = json.loads(results_path.read_text(encoding="utf-8"))
    interpretation_text = ""
    interp_path = bundle_dir / "results" / "interpretation.md"
    if interp_path.exists():
        interpretation_text = interp_path.read_text(encoding="utf-8")

    raw_tests = results.get("tests", []) or []
    summary = results.get("summary", {}) or {}

    qual_checks: list[CheckResult] = []
    quant_checks: list[CheckResult] = []
    critical_findings: list[CheckResult] = []
    failed_tests: list[dict[str, Any]] = []

    for test in raw_tests:
        verdict = (test.get("verdict") or "error").lower()
        severity = _VERDICT_TO_SEVERITY.get(verdict, "info")
        passed = _verdict_passed(verdict)
        check_id = _check_id_for(test)
        score = _coerce_score(test.get("metric"))
        details = test.get("evidence") or test.get("error") or ""
        evidence = {
            "agentic_verdict": verdict,
            "metric_raw": test.get("metric"),
            "block": test.get("block"),
            "error": test.get("error"),
        }

        check = CheckResult(
            check_id=check_id,
            check_name=test.get("name") or check_id,
            severity=severity,
            passed=passed,
            score=score,
            details=details,
            evidence=evidence,
            methodology_version=methodology_snapshot,
            improvement_suggestion=None,
        )

        block = (test.get("block") or "").lower()
        if block == "qualitative":
            qual_checks.append(check)
        else:
            quant_checks.append(check)

        if verdict == "fail":
            critical_findings.append(check)
            failed_tests.append(test)

    stages: list[ValidationStageResult] = []
    if qual_checks:
        stages.append(ValidationStageResult(
            stage="QUAL",
            stage_name="Qualitative analysis",
            status="passed" if all(c.passed for c in qual_checks) else "failed",
            checks=qual_checks,
            duration_sec=0.0,
            error_message=None,
        ))
    if quant_checks:
        stages.append(ValidationStageResult(
            stage="QUANT",
            stage_name="Quantitative analysis",
            status="passed" if all(c.passed for c in quant_checks) else "failed",
            checks=quant_checks,
            duration_sec=0.0,
            error_message=None,
        ))

    hard_recs = _build_hard_recs(failed_tests)
    soft_findings = _extract_soft_findings_from_interpretation(interpretation_text)
    soft_recs = _build_soft_recs(soft_findings)

    meta_scores = _aggregate_meta_scores(raw_tests)
    # Carry pre-check verbatim counts so reflection can spot mismatches between
    # what the fast pre-check thought it found vs. what the agentic session
    # ultimately concluded.
    if pre_check_summary:
        for k, v in pre_check_summary.items():
            if isinstance(v, (int, float)):
                meta_scores[f"pre_check_{k}"] = float(v)

    overall = _overall_verdict(summary, len(critical_findings))

    report = ValidationReport(
        bundle_id=bundle_id or results.get("bundle_id") or bundle_dir.name,
        model_profile={
            "bundle_id": bundle_id or results.get("bundle_id") or bundle_dir.name,
            "schema_source": "agentic-v2",
        },
        overall_verdict=overall,
        stages=stages,
        critical_findings=critical_findings,
        hard_recommendations=hard_recs,
        soft_recommendations=soft_recs,
        estimated_total_improvement={},
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        methodology_snapshot=methodology_snapshot,
        meta_scores=meta_scores,
    )

    if write_legacy_report:
        out_path = bundle_dir / "results" / "report.json"
        try:
            out_path.write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not write legacy report.json at %s: %s", out_path, exc)

        # Mirror the agentic recommendations into improvement/plan.json so the
        # downstream improvement → revalidation handoff reads the same source
        # of truth. The legacy synthesis stage writes the same file from
        # stage-runner findings; without this mirror, a later agentic pass
        # leaves a stale legacy plan in place.
        plan_path = bundle_dir / "improvement" / "plan.json"
        try:
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_data = {
                "hard": [r.to_dict() for r in hard_recs],
                "soft": [r.to_dict() for r in soft_recs],
            }
            plan_path.write_text(
                json.dumps(plan_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not write improvement/plan.json at %s: %s", plan_path, exc)

    return report
