"""Validation tools: run_validation, check CRUD, improvement cycle, etc."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, List

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _run_validation(ctx: ToolContext, bundle_id: str, stages: str = "all") -> str:
    from ouroboros.validation.pipeline import ValidationPipeline
    from ouroboros.validation.config_loader import load_validation_config
    config = load_validation_config()
    bundle_dir = ctx.drive_root / "validations" / bundle_id
    if not bundle_dir.exists():
        return f"Bundle not found: {bundle_id}"
    pipeline = ValidationPipeline(bundle_id, bundle_dir, ctx.repo_dir, config)
    report = asyncio.run(pipeline.run())
    return f"Validation complete. Verdict: {report.overall_verdict}. " \
           f"Stages: {len(report.stages)}, critical findings: {len(report.critical_findings)}."


def _run_validation_stage(ctx: ToolContext, bundle_id: str, stage: str) -> str:
    from ouroboros.validation.pipeline import ValidationPipeline
    from ouroboros.validation.config_loader import load_validation_config
    config = load_validation_config()
    bundle_dir = ctx.drive_root / "validations" / bundle_id
    if not bundle_dir.exists():
        return f"Bundle not found: {bundle_id}"
    pipeline = ValidationPipeline(bundle_id, bundle_dir, ctx.repo_dir, config)
    result = asyncio.run(pipeline.run_single_stage(stage))
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)


def _get_validation_report(ctx: ToolContext, bundle_id: str) -> str:
    report_path = ctx.drive_root / "validations" / bundle_id / "results" / "report.json"
    if not report_path.exists():
        return f"No report found for bundle {bundle_id}."
    return report_path.read_text(encoding="utf-8")


def _get_model_profile(ctx: ToolContext, bundle_id: str) -> str:
    profile_path = ctx.drive_root / "validations" / bundle_id / "inferred" / "model_profile.json"
    if not profile_path.exists():
        return f"No model profile found for bundle {bundle_id}."
    return profile_path.read_text(encoding="utf-8")


def _list_validation_checks(ctx: ToolContext, stage: str = "all", enabled_only: bool = True) -> str:
    from ouroboros.validation.check_registry import CheckRegistry
    registry = CheckRegistry(ctx.repo_dir)
    stage_filter = None if stage == "all" else stage
    checks = registry.list_checks(stage=stage_filter, enabled_only=enabled_only)
    result = [
        {"check_id": c.check_id, "stage": c.stage, "name": c.name,
         "check_type": c.check_type, "enabled": c.enabled, "tags": c.tags}
        for c in checks
    ]
    return json.dumps(result, indent=2, ensure_ascii=False)


def _create_validation_check(
    ctx: ToolContext,
    check_id: str,
    stage: str,
    name: str,
    description: str,
    check_type: str,
    code: str,
    tags: str = "",
) -> str:
    from ouroboros.validation.check_registry import CheckRegistry, ValidationCheck
    registry = CheckRegistry(ctx.repo_dir)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    impl_path = f"checks/{check_id.lower().replace('.', '_')}.py"

    # Write the check code file
    check_file = ctx.repo_dir / "ouroboros" / "validation" / impl_path
    check_file.parent.mkdir(parents=True, exist_ok=True)
    check_file.write_text(code, encoding="utf-8")

    from datetime import datetime, timezone
    check = ValidationCheck(
        check_id=check_id, stage=stage, name=name, description=description,
        check_type=check_type, enabled=True,
        created_by="agent", created_at=datetime.now(timezone.utc).isoformat(),
        version=1, tags=tag_list, implementation_path=impl_path,
    )
    try:
        registry.add_check(check)
        return f"Created check {check_id} at {impl_path}"
    except ValueError as exc:
        return f"Error: {exc}"


def _edit_validation_check(ctx: ToolContext, check_id: str, new_code: str, reason: str) -> str:
    from ouroboros.validation.check_registry import CheckRegistry
    registry = CheckRegistry(ctx.repo_dir)
    try:
        return registry.update_check(check_id, new_code, reason)
    except KeyError:
        return f"Check not found: {check_id}"


def _disable_validation_check(ctx: ToolContext, check_id: str, reason: str) -> str:
    from ouroboros.validation.check_registry import CheckRegistry
    registry = CheckRegistry(ctx.repo_dir)
    try:
        return registry.disable_check(check_id, reason)
    except KeyError:
        return f"Check not found: {check_id}"


def _delete_validation_check(ctx: ToolContext, check_id: str, reason: str) -> str:
    from ouroboros.validation.check_registry import CheckRegistry
    registry = CheckRegistry(ctx.repo_dir)
    try:
        return registry.delete_check(check_id, reason)
    except KeyError:
        return f"Check not found: {check_id}"


def _run_improvement_cycle(ctx: ToolContext, bundle_id: str) -> str:
    from ouroboros.validation.config_loader import load_validation_config
    from ouroboros.validation.model_improver import ModelImprover
    from ouroboros.validation.pipeline import RevalidationPipeline
    from ouroboros.validation.sandbox import ModelSandbox
    from ouroboros.validation.types import ImprovementRecommendation

    config = load_validation_config()
    bundle_dir = ctx.drive_root / "validations" / bundle_id
    if not bundle_dir.exists():
        return f"Bundle not found: {bundle_id}"

    # Load recommendations
    plan_path = bundle_dir / "improvement" / "plan.json"
    if not plan_path.exists():
        return "No improvement plan found. Run validation first."
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    hard_recs = [ImprovementRecommendation.from_dict(r) for r in plan_data.get("hard", [])]
    if not hard_recs:
        return "No hard recommendations to implement."

    sandbox = ModelSandbox(bundle_dir, config)

    # Step 1: Implement recommendations
    improver = ModelImprover(bundle_dir, hard_recs, sandbox, config)
    impl_result = asyncio.run(improver.implement())

    if not impl_result.recommendations_applied:
        skipped_summary = "; ".join(f"{c}: {r}" for c, r in impl_result.recommendations_skipped)
        return f"No recommendations could be implemented. Skipped: {skipped_summary}"

    # Step 2: Extract original metrics from report
    report_path = bundle_dir / "results" / "report.json"
    original_metrics: dict = {}
    if report_path.exists():
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
        for stage in report_data.get("stages", []):
            for check in stage.get("checks", []):
                if check.get("score") is not None:
                    original_metrics[check["check_id"]] = check["score"]

    # Step 3: Revalidate
    reval = RevalidationPipeline(bundle_id, bundle_dir, ctx.repo_dir, config)
    reval_result = asyncio.run(
        reval.run(original_metrics, impl_result.recommendations_applied,
                  impl_result.recommendations_skipped)
    )

    return (
        f"Improvement cycle complete. Applied: {len(impl_result.recommendations_applied)}, "
        f"Skipped: {len(impl_result.recommendations_skipped)}. "
        f"Verdict: {reval_result.verdict}, lift: {reval_result.improvement_lift:.4f}."
    )


def _compare_validations(ctx: ToolContext, bundle_id_a: str, bundle_id_b: str) -> str:
    report_a_path = ctx.drive_root / "validations" / bundle_id_a / "results" / "report.json"
    report_b_path = ctx.drive_root / "validations" / bundle_id_b / "results" / "report.json"
    if not report_a_path.exists():
        return f"No report for {bundle_id_a}"
    if not report_b_path.exists():
        return f"No report for {bundle_id_b}"
    a = json.loads(report_a_path.read_text(encoding="utf-8"))
    b = json.loads(report_b_path.read_text(encoding="utf-8"))
    return json.dumps({
        "bundle_a": {"id": bundle_id_a, "verdict": a.get("overall_verdict"), "stages": len(a.get("stages", []))},
        "bundle_b": {"id": bundle_id_b, "verdict": b.get("overall_verdict"), "stages": len(b.get("stages", []))},
    }, indent=2)


def _backtest_check(ctx: ToolContext, check_id: str, bundle_ids: str = "all") -> str:
    return f"Backtest for {check_id} — not yet implemented."


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("run_validation", {
            "name": "run_validation",
            "description": "Run the full validation pipeline on a model bundle.",
            "parameters": {"type": "object", "properties": {
                "bundle_id": {"type": "string", "description": "Bundle ID to validate."},
                "stages": {"type": "string", "default": "all", "description": "Stages to run (default: all)."},
            }, "required": ["bundle_id"]},
        }, _run_validation),

        ToolEntry("run_validation_stage", {
            "name": "run_validation_stage",
            "description": "Run a single validation stage on a model bundle.",
            "parameters": {"type": "object", "properties": {
                "bundle_id": {"type": "string"},
                "stage": {"type": "string", "description": "Stage ID (S0-S9)."},
            }, "required": ["bundle_id", "stage"]},
        }, _run_validation_stage),

        ToolEntry("get_validation_report", {
            "name": "get_validation_report",
            "description": "Get the validation report for a model bundle.",
            "parameters": {"type": "object", "properties": {
                "bundle_id": {"type": "string"},
            }, "required": ["bundle_id"]},
        }, _get_validation_report),

        ToolEntry("get_model_profile", {
            "name": "get_model_profile",
            "description": "Get the LLM-inferred model profile for a bundle.",
            "parameters": {"type": "object", "properties": {
                "bundle_id": {"type": "string"},
            }, "required": ["bundle_id"]},
        }, _get_model_profile),

        ToolEntry("list_validation_checks", {
            "name": "list_validation_checks",
            "description": "List registered validation checks, optionally filtered by stage.",
            "parameters": {"type": "object", "properties": {
                "stage": {"type": "string", "default": "all"},
                "enabled_only": {"type": "boolean", "default": True},
            }, "required": []},
        }, _list_validation_checks),

        ToolEntry("create_validation_check", {
            "name": "create_validation_check",
            "description": "Create a new validation check. Agent writes the Python code.",
            "parameters": {"type": "object", "properties": {
                "check_id": {"type": "string", "description": "Unique check ID (e.g., S2.CUSTOM_AUC)."},
                "stage": {"type": "string", "description": "Stage (S0-S9)."},
                "name": {"type": "string", "description": "Human-readable name."},
                "description": {"type": "string", "description": "What the check does."},
                "check_type": {"type": "string", "description": "deterministic|llm_assisted|sandbox."},
                "code": {"type": "string", "description": "Python source code for the check."},
                "tags": {"type": "string", "default": "", "description": "Comma-separated tags."},
            }, "required": ["check_id", "stage", "name", "description", "check_type", "code"]},
        }, _create_validation_check),

        ToolEntry("edit_validation_check", {
            "name": "edit_validation_check",
            "description": "Replace a check's implementation code.",
            "parameters": {"type": "object", "properties": {
                "check_id": {"type": "string"},
                "new_code": {"type": "string", "description": "New Python source code."},
                "reason": {"type": "string", "description": "Why the change was made."},
            }, "required": ["check_id", "new_code", "reason"]},
        }, _edit_validation_check),

        ToolEntry("disable_validation_check", {
            "name": "disable_validation_check",
            "description": "Disable a validation check (keeps in registry for audit).",
            "parameters": {"type": "object", "properties": {
                "check_id": {"type": "string"},
                "reason": {"type": "string"},
            }, "required": ["check_id", "reason"]},
        }, _disable_validation_check),

        ToolEntry("delete_validation_check", {
            "name": "delete_validation_check",
            "description": "Remove a validation check entirely.",
            "parameters": {"type": "object", "properties": {
                "check_id": {"type": "string"},
                "reason": {"type": "string"},
            }, "required": ["check_id", "reason"]},
        }, _delete_validation_check),

        ToolEntry("run_improvement_cycle", {
            "name": "run_improvement_cycle",
            "description": "Run the validate->improve->revalidate cycle on a bundle.",
            "parameters": {"type": "object", "properties": {
                "bundle_id": {"type": "string"},
            }, "required": ["bundle_id"]},
        }, _run_improvement_cycle),

        ToolEntry("compare_validations", {
            "name": "compare_validations",
            "description": "Compare two validation reports side by side.",
            "parameters": {"type": "object", "properties": {
                "bundle_id_a": {"type": "string"},
                "bundle_id_b": {"type": "string"},
            }, "required": ["bundle_id_a", "bundle_id_b"]},
        }, _compare_validations),

        ToolEntry("backtest_check", {
            "name": "backtest_check",
            "description": "Run a check against historical bundles.",
            "parameters": {"type": "object", "properties": {
                "check_id": {"type": "string"},
                "bundle_ids": {"type": "string", "default": "all"},
            }, "required": ["check_id"]},
        }, _backtest_check),
    ]
