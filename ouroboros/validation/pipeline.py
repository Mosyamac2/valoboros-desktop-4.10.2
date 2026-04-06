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
    RevalidationResult,
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
        (self._bundle_dir / "methodology" / "custom_checks").mkdir(parents=True, exist_ok=True)
        self._log_path = self._bundle_dir / "validation.log"

    def _log(self, message: str) -> None:
        """Append a timestamped line to validation.log."""
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"[{ts}] {message}\n"
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(line)

    def _log_stage_result(self, result: ValidationStageResult) -> None:
        """Log a stage completion summary."""
        failed = sum(1 for c in result.checks if not c.passed)
        total = len(result.checks)
        self._log(f"{result.stage} ({result.stage_name}) {result.status}: {total} checks, {failed} failed, {result.duration_sec:.1f}s")

    def _update_status(self, status: str, verdict: str = None, error: str = None) -> None:
        """Write status.json to track pipeline lifecycle."""
        from datetime import datetime, timezone
        status_file = self._bundle_dir / "status.json"
        data: dict = {}
        if status_file.exists():
            try:
                data = json.loads(status_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        data["status"] = status
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        if verdict is not None:
            data["verdict"] = verdict
        if error is not None:
            data["error"] = error
        status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    async def run(self) -> ValidationReport:
        """Execute the full validation pipeline."""
        self._update_status("validating")
        stages: list[ValidationStageResult] = []
        self._log(f"Starting validation pipeline for bundle {self._bundle_id}")

        # --- S0: Artifact Comprehension (HARD GATE) ---
        self._log("Starting S0: Artifact Comprehension...")
        profile = await self._run_comprehension()
        self._log(f"S0 comprehension done: model_type={profile.model_type}, confidence={profile.comprehension_confidence}")
        s0_result = await self._run_stage_module("intake_check", "S0", profile)
        stages.append(s0_result)
        self._save_stage(s0_result)
        self._log_stage_result(s0_result)

        if profile.comprehension_confidence < 0.1 and not profile.comprehension_gaps == []:
            self._log("HARD GATE: S0 comprehension failed. Aborting pipeline.")
            return self._build_report(stages, profile, error="S0 comprehension failed")

        # --- Auto-install detected dependencies before S1 ---
        self._log(f"Installing {len(profile.dependencies_detected)} detected dependencies...")
        await self._install_dependencies(profile)
        self._log("Dependency installation complete.")

        # --- Per-model literature research ---
        if self._config.pre_research:
            self._log("Searching for literature relevant to this model...")
            research = await self._research_model(profile)
            if research and research.relevant_papers:
                self._log(f"Found {len(research.relevant_papers)} relevant papers, "
                          f"{len(research.risk_insights)} risk insights")
            else:
                self._log("No relevant papers found (non-blocking, continuing).")

        # --- Methodology planning ---
        self._log("Starting methodology planning...")
        methodology = await self._plan_methodology(profile)
        active_stages = self._get_active_stages(methodology)
        if methodology:
            self._log(f"Methodology plan: {len(methodology.checks_to_run)} checks to run, {len(methodology.checks_to_skip)} skipped, {len(methodology.checks_to_create)} to create. Active stages: {sorted(active_stages)}")
        else:
            self._log("Methodology planning failed — all stages active (default).")

        # --- S1: Reproducibility (HARD GATE for S2-S7, always runs) ---
        self._log("Starting S1: Reproducibility...")
        s1_result = await self._run_stage_module("reproducibility", "S1", profile)
        stages.append(s1_result)
        self._save_stage(s1_result)
        self._log_stage_result(s1_result)
        s1_passed = s1_result.status == "passed"

        # --- S2-S7: Run if S1 passed AND stage is in methodology plan ---
        sandbox_stages = [
            ("performance", "S2", "Performance"),
            ("fit_quality", "S3", "Fit Quality"),
            ("sensitivity", "S6", "Sensitivity"),
            ("robustness", "S7", "Robustness"),
        ]
        if s1_passed:
            for module_name, stage_id, stage_name in sandbox_stages:
                if stage_id in active_stages:
                    self._log(f"Starting {stage_id}: {stage_name}...")
                    result = await self._run_stage_module(module_name, stage_id, profile)
                    stages.append(result)
                    self._save_stage(result)
                    self._log_stage_result(result)
                else:
                    self._log(f"Skipping {stage_id} ({stage_name}): not in methodology plan.")
                    stages.append(ValidationStageResult(
                        stage=stage_id, stage_name=stage_name, status="skipped",
                        checks=[], duration_sec=0.0,
                        error_message=f"Skipped by methodology plan.",
                    ))
        else:
            self._log("S1 failed — skipping S2, S3, S6, S7 (sandbox-dependent stages).")
            for _, stage_id, stage_name in sandbox_stages:
                stages.append(ValidationStageResult(
                    stage=stage_id, stage_name=stage_name, status="skipped",
                    checks=[], duration_sec=0.0,
                    error_message="Skipped — S1 reproducibility failed.",
                ))

        # --- S4, S5, S8: Run even if S1 failed, if in methodology plan ---
        deterministic_stages = [
            ("leakage", "S4", "Data Leakage"),
            ("fairness", "S5", "Bias & Fairness"),
            ("code_quality", "S8", "Code Quality"),
        ]
        for module_name, stage_id, stage_name in deterministic_stages:
            if stage_id in active_stages:
                self._log(f"Starting {stage_id}: {stage_name}...")
                result = await self._run_stage_module(module_name, stage_id, profile)
                stages.append(result)
                self._save_stage(result)
                self._log_stage_result(result)
            else:
                self._log(f"Skipping {stage_id} ({stage_name}): not in methodology plan.")
                stages.append(ValidationStageResult(
                    stage=stage_id, stage_name=stage_name, status="skipped",
                    checks=[], duration_sec=0.0,
                    error_message=f"Skipped by methodology plan.",
                ))

        # --- S9: Synthesis (receives prior stage results) ---
        self._log("Starting S9: Synthesis & Improvement Plan...")
        from ouroboros.validation.synthesis import run_stage as synthesis_run
        s9_result = await synthesis_run(
            self._bundle_dir, profile, self._check_registry,
            self._sandbox, self._config, prior_stages=stages,
        )
        stages.append(s9_result)
        self._save_stage(s9_result)
        self._log_stage_result(s9_result)

        # Load recommendations from improvement/plan.json (written by synthesis)
        hard_recs, soft_recs = self._load_recommendations()

        report = self._build_report(stages, profile, hard_recs=hard_recs, soft_recs=soft_recs)

        # Save via ReportGenerator
        from ouroboros.validation.report import ReportGenerator
        ReportGenerator().save(report, self._bundle_dir, self._config)
        self._log(f"Report generated: verdict={report.overall_verdict}, {len(report.critical_findings)} critical findings, {len(report.hard_recommendations)} hard recs, {len(report.soft_recommendations)} soft recs")

        # --- Tier 0 self-assessment (if enabled) ---
        if self._config.auto_self_assess:
            self._log("Running Tier 0 self-assessment...")
            try:
                from ouroboros.validation.self_assessment import run_self_assessment
                from ouroboros.validation.effectiveness import EffectivenessTracker
                tracker = EffectivenessTracker(self._bundle_dir.parent)
                await run_self_assessment(self._bundle_dir, report, self._config, tracker)
                self._log("Self-assessment complete.")
            except Exception as exc:
                self._log(f"Self-assessment failed: {exc}")
                log.warning("Self-assessment failed: %s", exc)

        self._update_status("completed", verdict=report.overall_verdict)
        self._log("Pipeline complete.")
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

    async def _research_model(self, profile: ModelProfile) -> Optional['ModelResearchResult']:
        """Run per-model targeted literature research before methodology planning."""
        if not self._config.pre_research:
            return None
        try:
            from ouroboros.validation.model_researcher import ModelResearcher
            knowledge_dir = self._bundle_dir.parent.parent / "memory" / "knowledge"
            researcher = ModelResearcher(profile, knowledge_dir, self._config, bundle_dir=self._bundle_dir)
            result = await researcher.research()
            # Save to bundle's methodology/ dir
            if result and (result.relevant_papers or result.risk_insights):
                research_md = self._bundle_dir / "methodology" / "research.md"
                research_md.parent.mkdir(parents=True, exist_ok=True)
                lines = ["# Per-Model Literature Research\n"]
                if result.risk_insights:
                    lines.append("## Risk Insights\n")
                    for r in result.risk_insights:
                        lines.append(f"- {r}")
                if result.applicable_techniques:
                    lines.append("\n## Applicable Techniques\n")
                    for t in result.applicable_techniques:
                        lines.append(f"- {t}")
                if result.relevant_papers:
                    lines.append("\n## Relevant Papers\n")
                    for p in result.relevant_papers:
                        lines.append(f"- [{p.relevance_score:.1f}] {p.title}")
                        lines.append(f"  {p.url}")
                lines.append("")
                research_md.write_text("\n".join(lines), encoding="utf-8")
            return result
        except Exception as exc:
            self._log(f"Model research failed (non-blocking): {exc}")
            log.warning("Per-model research failed: %s", exc)
            return None

    async def _plan_methodology(self, profile: ModelProfile) -> Optional['MethodologyPlan']:
        """Run methodology planner to create a per-model validation plan."""
        try:
            from ouroboros.validation.methodology_planner import MethodologyPlanner
            knowledge_dir = self._bundle_dir.parent.parent / "memory" / "knowledge"
            planner = MethodologyPlanner(
                self._bundle_dir, profile, self._check_registry,
                self._config, knowledge_dir,
            )
            methodology = await planner.plan()
            log.info(
                "Methodology plan: %d checks to run, %d to skip, %d to create",
                len(methodology.checks_to_run),
                len(methodology.checks_to_skip),
                len(methodology.checks_to_create),
            )
            return methodology
        except Exception as exc:
            log.warning("Methodology planning failed: %s", exc)
            return None

    @staticmethod
    def _get_active_stages(methodology: Optional['MethodologyPlan']) -> set[str]:
        """Extract which stages (S0-S8) should run from the methodology plan.

        If no plan, all stages are active (default behavior).
        S0, S1, S9 always run regardless of the plan.
        """
        if methodology is None:
            return {"S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"}
        # Extract stage prefixes from checks_to_run
        stages = {"S0", "S1", "S9"}  # always active
        for check_id in methodology.checks_to_run:
            # Check IDs look like "S2.OOS_METRICS" — extract the "S2" part
            parts = check_id.split(".")
            if parts and parts[0].startswith("S"):
                stages.add(parts[0])
        return stages

    async def _install_dependencies(self, profile: ModelProfile) -> None:
        """Install detected dependencies into the sandbox venv before S1."""
        deps = profile.dependencies_detected
        if not deps:
            log.info("No dependencies to install.")
            return
        log.info("Installing %d detected dependencies: %s", len(deps), deps)
        result = self._sandbox.install_dependencies(deps)
        log.info("Dependency install result: %s", result[:300])
        # Save install log for transparency
        install_log = self._results_dir / "dependency_install.log"
        install_log.write_text(
            f"Packages: {deps}\n\nResult:\n{result}\n",
            encoding="utf-8",
        )

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

    def _load_recommendations(self) -> tuple[list[ImprovementRecommendation], list[ImprovementRecommendation]]:
        """Load hard/soft recommendations from improvement/plan.json."""
        plan_path = self._bundle_dir / "improvement" / "plan.json"
        if not plan_path.exists():
            return [], []
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
            hard = [ImprovementRecommendation.from_dict(r) for r in data.get("hard", [])]
            soft = [ImprovementRecommendation.from_dict(r) for r in data.get("soft", [])]
            return hard, soft
        except Exception as exc:
            log.warning("Failed to load recommendations: %s", exc)
            return [], []

    def _build_report(
        self,
        stages: list[ValidationStageResult],
        profile: ModelProfile,
        error: Optional[str] = None,
        hard_recs: Optional[list[ImprovementRecommendation]] = None,
        soft_recs: Optional[list[ImprovementRecommendation]] = None,
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

        hard = hard_recs or []
        soft = soft_recs or []
        estimated_improvement: dict[str, float] = {}
        for r in hard:
            for metric, delta in r.estimated_metric_impact.items():
                estimated_improvement[metric] = estimated_improvement.get(metric, 0) + delta

        return ValidationReport(
            bundle_id=self._bundle_id,
            model_profile=profile.to_dict(),
            overall_verdict=verdict,
            stages=stages,
            critical_findings=critical,
            hard_recommendations=hard,
            soft_recommendations=soft,
            estimated_total_improvement=estimated_improvement,
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
    """Re-run S2-S7 on improved code and compare with original metrics."""

    def __init__(
        self,
        bundle_id: str,
        bundle_dir: Path,
        repo_dir: Path,
        config: ValidationConfig,
    ) -> None:
        self._bundle_id = bundle_id
        self._bundle_dir = Path(bundle_dir)
        self._repo_dir = repo_dir
        self._config = config
        self._check_registry = CheckRegistry(repo_dir)
        # Sandbox for improved code uses the improvement directory
        self._sandbox = ModelSandbox(self._bundle_dir / "improvement" / "implementation", config)
        self._reval_dir = self._bundle_dir / "improvement" / "revalidation"
        self._reval_dir.mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        original_metrics: dict[str, float],
        recommendations_applied: list[str],
        recommendations_skipped: list[tuple[str, str]],
    ) -> RevalidationResult:
        """Run S2-S7 on improved model, compare with original, compute lift."""
        # Load profile
        profile = self._load_profile()

        # Re-run S2-S7 stages on improved code
        improved_stages: list[ValidationStageResult] = []
        for module_name, stage_id in [
            ("performance", "S2"), ("fit_quality", "S3"),
            ("leakage", "S4"), ("fairness", "S5"),
            ("sensitivity", "S6"), ("robustness", "S7"),
        ]:
            try:
                import importlib
                mod = importlib.import_module(f"ouroboros.validation.{module_name}")
                result = await mod.run_stage(
                    self._bundle_dir / "improvement" / "implementation",
                    profile, self._check_registry, self._sandbox, self._config,
                )
            except Exception as exc:
                result = ValidationStageResult(
                    stage=stage_id, stage_name=module_name, status="error",
                    checks=[], duration_sec=0.0, error_message=str(exc),
                )
            improved_stages.append(result)
            # Save stage result
            stage_path = self._reval_dir / f"stage_{stage_id}.json"
            stage_path.write_text(
                json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        # Extract improved metrics from check scores
        improved_metrics = self._extract_metrics(improved_stages)

        # Compute deltas and lift
        all_keys = set(original_metrics.keys()) | set(improved_metrics.keys())
        deltas: dict[str, float] = {}
        lifts: list[float] = []
        for k in all_keys:
            orig = original_metrics.get(k, 0.0)
            impr = improved_metrics.get(k, 0.0)
            delta = impr - orig
            deltas[k] = round(delta, 6)
            if abs(orig) > 1e-9:
                lifts.append(delta / abs(orig))

        aggregate_lift = sum(lifts) / len(lifts) if lifts else 0.0
        threshold = self._config.improvement_lift_threshold

        # Determine verdict
        if aggregate_lift > threshold:
            verdict = "improved"
        elif aggregate_lift < -threshold:
            verdict = "degraded"
        elif lifts and any(l > threshold for l in lifts) and any(l < -threshold for l in lifts):
            verdict = "mixed"
        else:
            verdict = "unchanged"

        result = RevalidationResult(
            original_bundle_id=self._bundle_id,
            improved_bundle_id=f"{self._bundle_id}_improved",
            original_metrics=original_metrics,
            improved_metrics=improved_metrics,
            metric_deltas=deltas,
            improvement_lift=round(aggregate_lift, 6),
            recommendations_applied=recommendations_applied,
            recommendations_skipped=[f"{cid}: {reason}" for cid, reason in recommendations_skipped],
            verdict=verdict,
        )

        # Save revalidation result
        (self._reval_dir / "revalidation_result.json").write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Record in effectiveness tracker
        self._record_effectiveness(result, recommendations_applied)

        return result

    def _load_profile(self) -> ModelProfile:
        profile_path = self._bundle_dir / "inferred" / "model_profile.json"
        if profile_path.exists():
            return ModelProfile.from_dict(
                json.loads(profile_path.read_text(encoding="utf-8"))
            )
        return ModelProfile(
            bundle_id=self._bundle_id, task_description="unknown",
            model_type="other", model_type_confidence=0.0,
            framework="other", framework_confidence=0.0,
            algorithm="unknown", data_format="tabular",
        )

    @staticmethod
    def _extract_metrics(stages: list[ValidationStageResult]) -> dict[str, float]:
        """Extract numeric scores from check results as metrics."""
        metrics: dict[str, float] = {}
        for stage in stages:
            for check in stage.checks:
                if check.score is not None:
                    metrics[check.check_id] = check.score
        return metrics

    def _record_effectiveness(
        self,
        result: RevalidationResult,
        applied_check_ids: list[str],
    ) -> None:
        """Record Signal A (rec quality) and Signal B (finding quality) in tracker."""
        try:
            from ouroboros.validation.effectiveness import EffectivenessTracker
            tracker = EffectivenessTracker(self._bundle_dir.parent)
            threshold = self._config.improvement_lift_threshold

            for check_id in applied_check_ids:
                # Signal A: recommendation quality (direct measurement)
                tracker.record_recommendation_result(
                    check_id, self._bundle_id,
                    result.original_metrics, result.improved_metrics,
                )

                # Signal B: inferred finding quality (weaker signal)
                if result.improvement_lift > threshold:
                    tracker.record_finding_feedback(
                        check_id, self._bundle_id, "true_positive",
                        source="improvement_inferred", weight=0.5,
                    )
                elif result.improvement_lift < -threshold:
                    tracker.record_finding_feedback(
                        check_id, self._bundle_id, "false_positive",
                        source="improvement_inferred", weight=0.3,
                    )
                # unchanged → no finding quality signal
        except Exception as exc:
            log.warning("Failed to record effectiveness: %s", exc)


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
