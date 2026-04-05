"""Validation feedback tools: finding/recommendation effectiveness, self-assessment, evolution targets."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, List

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)


def _get_tracker(ctx: ToolContext):
    from ouroboros.validation.effectiveness import EffectivenessTracker
    return EffectivenessTracker(ctx.drive_root)


def _submit_finding_feedback(ctx: ToolContext, bundle_id: str, check_id: str, verdict: str, comment: str = "") -> str:
    tracker = _get_tracker(ctx)
    if verdict not in ("true_positive", "false_positive", "false_negative", "disputed"):
        return f"Invalid verdict: {verdict}. Use: true_positive, false_positive, false_negative, disputed."
    tracker.record_finding_feedback(check_id, bundle_id, verdict, source="human", weight=1.0)
    return f"Recorded {verdict} for {check_id} on bundle {bundle_id}."


def _run_self_assessment(ctx: ToolContext, bundle_id: str) -> str:
    report_path = ctx.drive_root / "validations" / bundle_id / "results" / "report.json"
    if not report_path.exists():
        return f"No report found for bundle {bundle_id}."

    import asyncio
    from ouroboros.validation.types import ValidationReport, ValidationConfig
    from ouroboros.validation.self_assessment import run_self_assessment
    from ouroboros.validation.config_loader import load_validation_config

    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    report = ValidationReport.from_dict(report_data)
    config = load_validation_config()
    tracker = _get_tracker(ctx)

    assessments = asyncio.get_event_loop().run_until_complete(
        run_self_assessment(ctx.drive_root / "validations" / bundle_id, report, config, tracker)
    )
    return f"Self-assessed {len(assessments)} findings for bundle {bundle_id}."


def _get_finding_effectiveness(ctx: ToolContext, check_id: str) -> str:
    tracker = _get_tracker(ctx)
    stats = tracker.get_finding_stats(check_id)
    return json.dumps({
        "check_id": stats.check_id,
        "weighted": {"tp": stats.tp, "fp": stats.fp, "fn": stats.fn,
                     "precision": round(stats.precision, 3), "recall": round(stats.recall, 3),
                     "f1": round(stats.f1, 3)},
        "self_assessed": {"tp": stats.self_assessed_tp, "fp": stats.self_assessed_fp},
        "human": {"tp": stats.human_tp, "fp": stats.human_fp, "fn": stats.human_fn},
        "times_triggered": stats.times_triggered,
    }, indent=2)


def _get_recommendation_effectiveness(ctx: ToolContext, check_id: str) -> str:
    tracker = _get_tracker(ctx)
    stats = tracker.get_recommendation_stats(check_id)
    return json.dumps({
        "check_id": stats.check_id,
        "produced": stats.recommendations_produced,
        "implemented": stats.recommendations_implemented,
        "improved": stats.recommendations_improved,
        "degraded": stats.recommendations_degraded,
        "unchanged": stats.recommendations_unchanged,
        "mean_lift": round(stats.mean_improvement_lift, 4),
        "usefulness_rate": round(stats.usefulness_rate, 3),
    }, indent=2)


def _get_platform_metrics(ctx: ToolContext) -> str:
    tracker = _get_tracker(ctx)
    metrics = tracker.get_platform_metrics()
    return json.dumps({
        "maturity_phase": metrics.maturity_phase,
        "total_validations": metrics.total_validations,
        "total_bundles_with_feedback": metrics.total_bundles_with_feedback,
        "mean_finding_precision": round(metrics.mean_finding_precision, 3),
        "mean_improvement_lift": round(metrics.mean_improvement_lift, 4),
        "total_improvement_cycles": metrics.total_improvement_cycles,
    }, indent=2)


def _get_evolution_targets(ctx: ToolContext) -> str:
    tracker = _get_tracker(ctx)
    targets = tracker.get_evolution_targets()
    return json.dumps([t.to_dict() for t in targets], indent=2)


def _get_methodology_changelog(ctx: ToolContext, last_n: int = 10) -> str:
    try:
        result = subprocess.run(
            ["git", "log", f"-{last_n}", "--oneline", "--", "ouroboros/validation/"],
            cwd=str(ctx.repo_dir), capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() or "No validation methodology commits found."
    except Exception as exc:
        return f"Error reading git log: {exc}"


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("submit_finding_feedback", {
            "name": "submit_finding_feedback",
            "description": "Record human (Tier 2) TP/FP/FN verdict for a validation finding.",
            "parameters": {"type": "object", "properties": {
                "bundle_id": {"type": "string"},
                "check_id": {"type": "string"},
                "verdict": {"type": "string", "description": "true_positive|false_positive|false_negative|disputed"},
                "comment": {"type": "string", "default": ""},
            }, "required": ["bundle_id", "check_id", "verdict"]},
        }, _submit_finding_feedback),

        ToolEntry("run_self_assessment", {
            "name": "run_self_assessment",
            "description": "Trigger Tier 0 self-assessment: LLM reviews its own findings.",
            "parameters": {"type": "object", "properties": {
                "bundle_id": {"type": "string"},
            }, "required": ["bundle_id"]},
        }, _run_self_assessment),

        ToolEntry("get_finding_effectiveness", {
            "name": "get_finding_effectiveness",
            "description": "Get finding quality stats for a check (precision, recall, tier breakdown).",
            "parameters": {"type": "object", "properties": {
                "check_id": {"type": "string"},
            }, "required": ["check_id"]},
        }, _get_finding_effectiveness),

        ToolEntry("get_recommendation_effectiveness", {
            "name": "get_recommendation_effectiveness",
            "description": "Get recommendation quality stats for a check (lift, usefulness rate).",
            "parameters": {"type": "object", "properties": {
                "check_id": {"type": "string"},
            }, "required": ["check_id"]},
        }, _get_recommendation_effectiveness),

        ToolEntry("get_platform_metrics", {
            "name": "get_platform_metrics",
            "description": "Get overall platform metrics including maturity phase.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }, _get_platform_metrics),

        ToolEntry("get_evolution_targets", {
            "name": "get_evolution_targets",
            "description": "Get prioritized list of methodology improvement targets.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }, _get_evolution_targets),

        ToolEntry("get_methodology_changelog", {
            "name": "get_methodology_changelog",
            "description": "Git log of recent validation methodology changes.",
            "parameters": {"type": "object", "properties": {
                "last_n": {"type": "integer", "default": 10},
            }, "required": []},
        }, _get_methodology_changelog),
    ]
