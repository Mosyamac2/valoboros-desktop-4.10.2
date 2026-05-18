"""
S9 stage orchestrator: synthesis & improvement plan.

Collects all failed checks from S0-S8, uses LLM to classify each as
hard or soft recommendation, prioritizes by impact/effort, and caps
at configured maximums.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from ouroboros.validation.check_registry import CheckRegistry
from ouroboros.validation.types import (
    CheckResult,
    ImprovementRecommendation,
    ModelProfile,
    ValidationConfig,
    ValidationStageResult,
)

log = logging.getLogger(__name__)

_EFFORT_RANK = {"trivial": 0, "moderate": 1, "significant": 2, "infeasible": 3}

_SYNTHESIS_PROMPT = """\
You are an ML model validation expert. You have validated a model and found issues.
For each failed check, produce an improvement recommendation.

## Model Profile
{model_profile}

## Failed Checks
{failed_checks}

## Instructions
For EACH failed check, produce a JSON object with these fields:
- "finding_check_id": the check_id from the finding
- "problem": one-sentence summary of the issue
- "recommendation": specific suggestion
- "kind": "hard" if you can write a code change to fix it, "soft" if the fix requires human action or external data
- "implementation_sketch": Python code snippet that implements the fix (empty string for soft)
- "estimated_metric_impact": dict of metric name to expected improvement (empty dict for soft), e.g. {{"AUC": 0.03}}
- "confidence": 0.0-1.0 how confident you are
- "effort": "trivial" | "moderate" | "significant" | "infeasible"
- "priority": integer, 1 = highest priority

IMPORTANT:
- Do NOT add any information not supported by the test results above.
  Cite only what the checks actually found. No speculation, no filler.
- Recommendations must be feasible, practical, and measurable — in terms of
  model metrics, stability, inference latency, code quality, or any other
  concrete dimension. "Consider improving" is not a recommendation.
- Each recommendation should be specific enough that a developer can implement it.
- If NO quantitative checks ran (all findings are from code review only),
  explicitly state in the FIRST recommendation: "WARNING: This validation is
  based on code review only. No quantitative analysis was performed on the data.
  Findings should be considered preliminary until confirmed with data analysis."

Return a JSON array of these objects. Return ONLY the JSON array, no explanation.
If there are no failed checks, return an empty array: []
"""


async def run_stage(
    bundle_dir: Path,
    model_profile: ModelProfile,
    check_registry: CheckRegistry,
    sandbox: Any,
    config: ValidationConfig,
    prior_stages: list[ValidationStageResult] | None = None,
) -> ValidationStageResult:
    """Synthesize findings from prior stages into improvement recommendations."""
    start = time.monotonic()

    # Collect failed checks from prior stages
    failed_checks: list[CheckResult] = []
    if prior_stages:
        for stage_result in prior_stages:
            for check in stage_result.checks:
                if not check.passed:
                    failed_checks.append(check)

    if not failed_checks:
        return ValidationStageResult(
            stage="S9", stage_name="Synthesis", status="passed",
            checks=[], duration_sec=round(time.monotonic() - start, 3),
            error_message=None,
        )

    # Try LLM synthesis
    try:
        recommendations = await _call_llm_synthesis(
            model_profile, failed_checks, config,
        )
    except Exception as exc:
        log.warning("LLM synthesis failed, using fallback: %s", exc)
        recommendations = _fallback_recommendations(failed_checks)

    # Split into hard/soft, sort, and cap
    hard = sorted(
        [r for r in recommendations if r.kind == "hard"],
        key=lambda r: (r.priority, _EFFORT_RANK.get(r.effort, 2)),
    )[:config.max_hard_recommendations]
    soft = sorted(
        [r for r in recommendations if r.kind == "soft"],
        key=lambda r: r.priority,
    )[:config.max_soft_recommendations]

    # Store recommendations as JSON in the bundle
    recs_data = {
        "hard": [r.to_dict() for r in hard],
        "soft": [r.to_dict() for r in soft],
    }
    improvement_dir = Path(bundle_dir) / "improvement"
    improvement_dir.mkdir(parents=True, exist_ok=True)
    (improvement_dir / "plan.json").write_text(
        json.dumps(recs_data, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    # Build a summary check result for the synthesis stage
    summary = CheckResult(
        check_id="S9.SYNTHESIS",
        check_name="Improvement plan synthesis",
        severity="info",
        passed=True,
        score=None,
        details=f"Generated {len(hard)} hard and {len(soft)} soft recommendations from {len(failed_checks)} findings.",
        evidence={"hard_count": len(hard), "soft_count": len(soft), "findings_count": len(failed_checks)},
        methodology_version="seed",
        improvement_suggestion=None,
    )

    duration = time.monotonic() - start
    return ValidationStageResult(
        stage="S9", stage_name="Synthesis", status="passed",
        checks=[summary], duration_sec=round(duration, 3),
        error_message=None,
    )


async def _call_llm_synthesis(
    model_profile: ModelProfile,
    failed_checks: list[CheckResult],
    config: ValidationConfig,
) -> list[ImprovementRecommendation]:
    """Call LLM to generate recommendations from failed checks."""
    from ouroboros.llm import LLMClient

    checks_text = "\n".join(
        f"- [{c.check_id}] {c.severity}: {c.details} (score={c.score})"
        for c in failed_checks
    )
    prompt = _SYNTHESIS_PROMPT.format(
        model_profile=json.dumps(model_profile.to_dict(), indent=2),
        failed_checks=checks_text,
    )

    client = LLMClient()
    messages = [
        {"role": "system", "content": "You produce JSON arrays of improvement recommendations."},
        {"role": "user", "content": prompt},
    ]
    response, _usage = await asyncio.to_thread(
        client.chat,
        messages=messages,
        model=config.synthesis_model,
        reasoning_effort="medium",
        max_tokens=8192,
    )

    text = response.get("content", "")
    if isinstance(text, list):
        text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]

    items = json.loads(text.strip())
    return [ImprovementRecommendation.from_dict(item) for item in items]


def _fallback_recommendations(
    failed_checks: list[CheckResult],
) -> list[ImprovementRecommendation]:
    """Generate simple recommendations without LLM when the call fails."""
    recs: list[ImprovementRecommendation] = []
    for i, check in enumerate(failed_checks):
        if check.improvement_suggestion:
            recs.append(ImprovementRecommendation(
                finding_check_id=check.check_id,
                problem=check.details[:200],
                recommendation=check.improvement_suggestion,
                kind="hard",
                implementation_sketch="",
                estimated_metric_impact={},
                confidence=0.4,
                effort="moderate",
                priority=i + 1,
            ))
        else:
            recs.append(ImprovementRecommendation(
                finding_check_id=check.check_id,
                problem=check.details[:200],
                recommendation=f"Investigate and address: {check.details[:100]}",
                kind="soft",
                implementation_sketch="",
                estimated_metric_impact={},
                confidence=0.3,
                effort="infeasible",
                priority=i + 1,
            ))
    return recs
