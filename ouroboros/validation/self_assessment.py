"""
Ouroboros validation platform — Tier 0 self-assessment.

After every validation, the LLM reviews its own findings and rates each
as likely-TP or likely-FP.  Stored with source="self_assessed", weight=0.3.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ouroboros.validation.effectiveness import EffectivenessTracker
from ouroboros.validation.types import CheckResult, ValidationConfig, ValidationReport

log = logging.getLogger(__name__)

_SELF_ASSESS_PROMPT = """\
You previously validated an ML model and produced findings. Now review each finding
and assess whether it is likely a true positive or false positive.

For each finding below, respond with a JSON object:
{{"check_id": "<id>", "verdict": "likely_tp" or "likely_fp", "reasoning": "<brief explanation>"}}

Return a JSON array of these objects.

## Findings to assess
{findings}
"""


async def run_self_assessment(
    bundle_dir: Path,
    report: ValidationReport,
    config: ValidationConfig,
    tracker: EffectivenessTracker,
) -> list[dict[str, Any]]:
    """Run Tier 0 self-assessment on all failed findings in the report."""
    failed_checks: list[CheckResult] = []
    for stage in report.stages:
        for check in stage.checks:
            if not check.passed:
                failed_checks.append(check)

    if not failed_checks:
        return []

    # Try LLM assessment
    try:
        assessments = await _call_llm_assessment(failed_checks, config)
    except Exception as exc:
        log.warning("LLM self-assessment failed, using heuristic: %s", exc)
        assessments = _heuristic_assessment(failed_checks)

    # Record in effectiveness tracker
    for a in assessments:
        tracker.record_self_assessment(
            check_id=a["check_id"],
            bundle_id=report.bundle_id,
            likely_verdict=a["verdict"],
            reasoning=a.get("reasoning", ""),
        )

    return assessments


async def _call_llm_assessment(
    failed_checks: list[CheckResult],
    config: ValidationConfig,
) -> list[dict[str, Any]]:
    from ouroboros.llm import LLMClient

    findings_text = "\n".join(
        f"- [{c.check_id}] severity={c.severity}, details: {c.details[:200]}"
        for c in failed_checks
    )
    prompt = _SELF_ASSESS_PROMPT.format(findings=findings_text)

    client = LLMClient()
    response, _usage = await asyncio.to_thread(
        client.chat,
        messages=[
            {"role": "system", "content": "You assess ML validation findings. Return only JSON."},
            {"role": "user", "content": prompt},
        ],
        model=config.comprehension_model,
        reasoning_effort="low",
        max_tokens=4096,
    )

    text = response.get("content", "")
    if isinstance(text, list):
        text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]

    return json.loads(text.strip())


def _heuristic_assessment(failed_checks: list[CheckResult]) -> list[dict[str, Any]]:
    """Simple heuristic when LLM is unavailable: critical = likely TP, info = likely FP."""
    results = []
    for c in failed_checks:
        if c.severity == "critical":
            verdict = "likely_tp"
            reasoning = "Critical severity findings are usually genuine issues."
        elif c.severity == "warning" and c.score is not None and c.score > 0.5:
            verdict = "likely_tp"
            reasoning = "Warning with high score suggests a real issue."
        else:
            verdict = "likely_fp"
            reasoning = "Low severity or no quantitative evidence."
        results.append({
            "check_id": c.check_id,
            "verdict": verdict,
            "reasoning": reasoning,
        })
    return results
