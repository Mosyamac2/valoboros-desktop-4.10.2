"""
Ouroboros validation platform — pipeline orchestrator.

ValidationPipeline runs S0-S9 stages on a model bundle.
RevalidationPipeline (stub) will re-run S2-S7 on improved code.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from ouroboros.validation.check_registry import CheckRegistry
from ouroboros.validation.sandbox import ModelSandbox
from ouroboros.validation.types import (
    CheckResult,
    ImprovementRecommendation,
    ModelProfile,
    ValidationConfig,
    ValidationReport,
    ValidationStageResult,
)

log = logging.getLogger(__name__)


class ValidationPipeline:
    """Orchestrates S0-S9 for a given bundle."""

    def __init__(
        self,
        bundle_id: str,
        bundle_dir: Path,
        repo_dir: Path,
        config: ValidationConfig,
    ) -> None:
        self._bundle_id = bundle_id
        self._bundle_dir = Path(bundle_dir)
        self._repo_dir = Path(repo_dir)
        self._config = config
        self._check_registry = CheckRegistry(repo_dir)
        self._sandbox = ModelSandbox(self._bundle_dir, config)
        self._results_dir = self._bundle_dir / "results"
        self._results_dir.mkdir(parents=True, exist_ok=True)

    async def run(self) -> ValidationReport:
        """Execute the full validation pipeline."""
        stages: list[ValidationStageResult] = []

        # --- S0: Artifact Comprehension (HARD GATE) ---
        profile = await self._run_comprehension()
        s0_result = await self._run_stage_module("intake_check", "S0", profile)
        stages.append(s0_result)
        self._save_stage(s0_result)

        if profile.comprehension_confidence < 0.1 and not profile.comprehension_gaps == []:
            # Comprehension totally failed — can't proceed meaningfully
            return self._build_report(stages, profile, error="S0 comprehension failed")

        # --- S1: Reproducibility (HARD GATE for S2-S7) ---
        s1_result = await self._run_stage_module("reproducibility", "S1", profile)
        stages.append(s1_result)
        self._save_stage(s1_result)
        s1_passed = s1_result.status == "passed"

        # --- S2-S7: Run if S1 passed (sandbox-dependent stages) ---
        if s1_passed:
            for module_name, stage_id in [
                ("performance", "S2"),
                ("fit_quality", "S3"),
                ("sensitivity", "S6"),
                ("robustness", "S7"),
            ]:
                result = await self._run_stage_module(module_name, stage_id, profile)
                stages.append(result)
                self._save_stage(result)
        else:
            for stage_id, name in [("S2", "Performance"), ("S3", "Fit Quality"),
                                    ("S6", "Sensitivity"), ("S7", "Robustness")]:
                stages.append(ValidationStageResult(
                    stage=stage_id, stage_name=name, status="skipped",
                    checks=[], duration_sec=0.0,
                    error_message="Skipped — S1 reproducibility failed.",
                ))

        # --- S4, S5, S8: Run even if S1 failed (code-only / deterministic) ---
        for module_name, stage_id in [
            ("leakage", "S4"),
            ("fairness", "S5"),
            ("code_quality", "S8"),
        ]:
            result = await self._run_stage_module(module_name, stage_id, profile)
            stages.append(result)
            self._save_stage(result)

        # --- S9: Synthesis (placeholder) ---
        s9_result = await self._run_stage_module("synthesis", "S9", profile)
        stages.append(s9_result)
        self._save_stage(s9_result)

        report = self._build_report(stages, profile)
        self._save_report(report)
        return report

    async def run_single_stage(self, stage: str) -> ValidationStageResult:
        """Re-run a single stage (requires model_profile.json to exist)."""
        profile = self._load_profile()
        module_map = {
            "S0": "intake_check", "S1": "reproducibility",
            "S2": "performance", "S3": "fit_quality",
            "S4": "leakage", "S5": "fairness",
            "S6": "sensitivity", "S7": "robustness",
            "S8": "code_quality", "S9": "synthesis",
        }
        module_name = module_map.get(stage)
        if not module_name:
            return ValidationStageResult(
                stage=stage, stage_name="Unknown", status="error",
                checks=[], duration_sec=0.0,
                error_message=f"Unknown stage: {stage}",
            )
        result = await self._run_stage_module(module_name, stage, profile)
        self._save_stage(result)
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _run_comprehension(self) -> ModelProfile:
        """Run S0 artifact comprehension to produce ModelProfile."""
        profile_path = self._bundle_dir / "inferred" / "model_profile.json"
        if profile_path.exists():
            return self._load_profile()

        try:
            from ouroboros.validation.artifact_comprehension import ArtifactComprehension
            ac = ArtifactComprehension(self._bundle_dir, self._config, self._bundle_id)
            return await ac.analyze()
        except Exception as exc:
            log.error("Artifact comprehension failed: %s", exc)
            return ModelProfile(
                bundle_id=self._bundle_id,
                task_description="unknown",
                model_type="other", model_type_confidence=0.0,
                framework="other", framework_confidence=0.0,
                algorithm="unknown", data_format="tabular",
                comprehension_confidence=0.0,
                comprehension_gaps=[f"Comprehension failed: {exc}"],
            )

    async def _run_stage_module(
        self, module_name: str, stage_id: str, profile: ModelProfile,
    ) -> ValidationStageResult:
        """Import and run a stage orchestrator module."""
        try:
            import importlib
            mod = importlib.import_module(f"ouroboros.validation.{module_name}")
            return await mod.run_stage(
                self._bundle_dir, profile, self._check_registry,
                self._sandbox, self._config,
            )
        except Exception as exc:
            log.error("Stage %s failed: %s", stage_id, exc)
            return ValidationStageResult(
                stage=stage_id, stage_name=module_name,
                status="error", checks=[], duration_sec=0.0,
                error_message=f"{type(exc).__name__}: {exc}",
            )

    def _load_profile(self) -> ModelProfile:
        profile_path = self._bundle_dir / "inferred" / "model_profile.json"
        if profile_path.exists():
            data = json.loads(profile_path.read_text(encoding="utf-8"))
            return ModelProfile.from_dict(data)
        return ModelProfile(
            bundle_id=self._bundle_id, task_description="unknown",
            model_type="other", model_type_confidence=0.0,
            framework="other", framework_confidence=0.0,
            algorithm="unknown", data_format="tabular",
        )

    def _build_report(
        self,
        stages: list[ValidationStageResult],
        profile: ModelProfile,
        error: Optional[str] = None,
    ) -> ValidationReport:
        """Build a ValidationReport from stage results."""
        all_checks: list[CheckResult] = []
        for s in stages:
            all_checks.extend(s.checks)

        critical = [c for c in all_checks if not c.passed and c.severity == "critical"]
        any_critical = len(critical) > 0
        any_failed = any(not c.passed for c in all_checks)

        if error:
            verdict = "rejected"
        elif any_critical:
            verdict = "rejected"
        elif any_failed:
            verdict = "conditional"
        else:
            verdict = "approved"

        return ValidationReport(
            bundle_id=self._bundle_id,
            model_profile=profile.to_dict(),
            overall_verdict=verdict,
            stages=stages,
            critical_findings=critical,
            hard_recommendations=[],  # populated by S9 synthesis in Prompt 8
            soft_recommendations=[],
            estimated_total_improvement={},
            generated_at=_utc_now(),
            methodology_snapshot="",
            meta_scores={"comprehension_confidence": profile.comprehension_confidence},
        )

    def _save_stage(self, result: ValidationStageResult) -> None:
        path = self._results_dir / f"stage_{result.stage}.json"
        path.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save_report(self, report: ValidationReport) -> None:
        path = self._results_dir / "report.json"
        path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


class RevalidationPipeline:
    """Stub — will be completed in Prompt 10."""
    pass


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
