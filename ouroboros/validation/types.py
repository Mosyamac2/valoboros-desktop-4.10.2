"""
Ouroboros validation platform — core data types.

All dataclasses used across the validation pipeline live here.
Each has to_dict() for JSON serialization and from_dict() for deserialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_dict(obj: Any) -> Any:
    """Recursively convert dataclass instances, lists, and dicts to plain dicts."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, list):
        return [_as_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _as_dict(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# SandboxResult
# ---------------------------------------------------------------------------

@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    duration_sec: float
    oom_killed: bool
    timeout_killed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SandboxResult:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    check_id: str                        # e.g., "S2.OOS.AUC"
    check_name: str                      # human-readable
    severity: str                        # "critical" | "warning" | "info" | "pass"
    passed: bool
    score: Optional[float]               # quantitative metric if applicable
    details: str                         # explanation
    evidence: dict[str, Any]             # raw data supporting the finding
    methodology_version: str             # git short-hash of the check's code
    improvement_suggestion: Optional[str]  # specific, feasible suggestion

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CheckResult:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ValidationStageResult
# ---------------------------------------------------------------------------

@dataclass
class ValidationStageResult:
    stage: str                           # "S0" .. "S9"
    stage_name: str
    status: str                          # "passed" | "failed" | "error" | "skipped"
    checks: list[CheckResult]
    duration_sec: float
    error_message: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "stage_name": self.stage_name,
            "status": self.status,
            "checks": [c.to_dict() for c in self.checks],
            "duration_sec": self.duration_sec,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ValidationStageResult:
        checks = [CheckResult.from_dict(c) for c in d.get("checks", [])]
        return cls(
            stage=d["stage"],
            stage_name=d["stage_name"],
            status=d["status"],
            checks=checks,
            duration_sec=d["duration_sec"],
            error_message=d.get("error_message"),
        )


# ---------------------------------------------------------------------------
# ImprovementRecommendation
# ---------------------------------------------------------------------------

@dataclass
class ImprovementRecommendation:
    finding_check_id: str                # which check identified the issue
    problem: str                         # what's wrong
    recommendation: str                  # specific fix description
    kind: str                            # "hard" | "soft"
    implementation_sketch: str           # pseudocode or code snippet (empty for soft)
    estimated_metric_impact: dict[str, float]  # e.g., {"AUC": +0.03} (may be empty for soft)
    confidence: float                    # LLM's confidence in the estimate (0-1)
    effort: str                          # "trivial" | "moderate" | "significant" | "infeasible"
    priority: int                        # 1 = highest
    #
    # "hard" recommendations: specific, implementable by the side agent,
    #   enter the improve->revalidate cycle. Must have implementation_sketch
    #   and estimated_metric_impact.
    # "soft" recommendations: genuinely valuable observations that CANNOT be
    #   implemented by the side agent (e.g., "collect more data",
    #   "consult domain expert about feature X", "retrain with 3x more epochs
    #   on full dataset -- infeasible on sample data"). Communicated to the
    #   human reviewer but do NOT enter the improvement cycle and do NOT
    #   count toward improvement lift.

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ImprovementRecommendation:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ValidationReport
# ---------------------------------------------------------------------------

@dataclass
class ValidationReport:
    bundle_id: str
    model_profile: dict[str, Any]        # the inferred model_profile.json
    overall_verdict: str                 # "approved" | "conditional" | "rejected"
    stages: list[ValidationStageResult]
    critical_findings: list[CheckResult]
    # Recommendations split by kind:
    hard_recommendations: list[ImprovementRecommendation]
    soft_recommendations: list[ImprovementRecommendation]
    estimated_total_improvement: dict[str, float]  # from hard recommendations only
    generated_at: str                    # ISO-8601
    methodology_snapshot: str            # git commit hash of validation code
    meta_scores: dict[str, float]        # e.g., {"comprehension_confidence": 0.85}

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "model_profile": self.model_profile,
            "overall_verdict": self.overall_verdict,
            "stages": [s.to_dict() for s in self.stages],
            "critical_findings": [c.to_dict() for c in self.critical_findings],
            "hard_recommendations": [r.to_dict() for r in self.hard_recommendations],
            "soft_recommendations": [r.to_dict() for r in self.soft_recommendations],
            "estimated_total_improvement": self.estimated_total_improvement,
            "generated_at": self.generated_at,
            "methodology_snapshot": self.methodology_snapshot,
            "meta_scores": self.meta_scores,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ValidationReport:
        return cls(
            bundle_id=d["bundle_id"],
            model_profile=d.get("model_profile", {}),
            overall_verdict=d["overall_verdict"],
            stages=[ValidationStageResult.from_dict(s) for s in d.get("stages", [])],
            critical_findings=[CheckResult.from_dict(c) for c in d.get("critical_findings", [])],
            hard_recommendations=[ImprovementRecommendation.from_dict(r) for r in d.get("hard_recommendations", [])],
            soft_recommendations=[ImprovementRecommendation.from_dict(r) for r in d.get("soft_recommendations", [])],
            estimated_total_improvement=d.get("estimated_total_improvement", {}),
            generated_at=d["generated_at"],
            methodology_snapshot=d["methodology_snapshot"],
            meta_scores=d.get("meta_scores", {}),
        )


# ---------------------------------------------------------------------------
# RevalidationResult
# ---------------------------------------------------------------------------

@dataclass
class RevalidationResult:
    original_bundle_id: str
    improved_bundle_id: str
    original_metrics: dict[str, float]
    improved_metrics: dict[str, float]
    metric_deltas: dict[str, float]
    improvement_lift: float              # aggregate improvement score
    recommendations_applied: list[str]   # check_ids of applied recommendations
    recommendations_skipped: list[str]   # check_ids of skipped recommendations
    verdict: str                         # "improved" | "degraded" | "unchanged" | "mixed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RevalidationResult:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ImproverResult
# ---------------------------------------------------------------------------

@dataclass
class ImproverResult:
    recommendations_applied: list[str]                    # check_ids of applied recs
    recommendations_skipped: list[tuple[str, str]]        # (check_id, reason)
    modified_files: list[str]                             # paths of modified files
    sandbox_output: Optional[SandboxResult] = None        # final sandbox run
    new_metrics: Optional[dict[str, float]] = None        # if pipeline ran successfully

    def to_dict(self) -> dict[str, Any]:
        d = {
            "recommendations_applied": self.recommendations_applied,
            "recommendations_skipped": self.recommendations_skipped,
            "modified_files": self.modified_files,
            "new_metrics": self.new_metrics,
        }
        if self.sandbox_output:
            d["sandbox_output"] = self.sandbox_output.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ImproverResult:
        so = d.get("sandbox_output")
        return cls(
            recommendations_applied=d.get("recommendations_applied", []),
            recommendations_skipped=d.get("recommendations_skipped", []),
            modified_files=d.get("modified_files", []),
            sandbox_output=SandboxResult.from_dict(so) if so else None,
            new_metrics=d.get("new_metrics"),
        )


# ---------------------------------------------------------------------------
# MethodologyPlan
# ---------------------------------------------------------------------------

@dataclass
class MethodologyPlan:
    bundle_id: str = ""
    model_summary: str = ""
    risk_priorities: list[str] = field(default_factory=list)
    checks_to_run: list[str] = field(default_factory=list)
    checks_to_skip: list[str] = field(default_factory=list)
    checks_to_create: list[dict[str, Any]] = field(default_factory=list)
    knowledge_references: list[str] = field(default_factory=list)
    similar_past_validations: list[str] = field(default_factory=list)
    methodology_version: str = "0.1.0"
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MethodologyPlan:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ReflectionResult
# ---------------------------------------------------------------------------

@dataclass
class ReflectionResult:
    total_validations_analyzed: int = 0
    patterns_found: list[dict[str, Any]] = field(default_factory=list)
    # Each pattern: {"check_id": str, "frequency": int, "model_types": list[str], "description": str}
    dead_checks: list[str] = field(default_factory=list)   # check_ids that never triggered
    hot_checks: list[str] = field(default_factory=list)    # check_ids that always trigger
    knowledge_entries_written: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReflectionResult:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ModelProfile
# ---------------------------------------------------------------------------

@dataclass
class ModelProfile:
    bundle_id: str
    task_description: str
    model_type: str                      # classification|regression|ranking|clustering|generative|other
    model_type_confidence: float
    framework: str                       # sklearn|pytorch|tensorflow|xgboost|lightgbm|catboost|statsmodels|other
    framework_confidence: float
    algorithm: str                       # e.g., "GradientBoostingClassifier", "LSTM"
    data_format: str                     # tabular|image|text|timeseries|mixed
    target_column: Optional[str] = None
    target_column_confidence: float = 0.0
    feature_columns: list[str] = field(default_factory=list)
    protected_attributes_candidates: list[str] = field(default_factory=list)
    temporal_column: Optional[str] = None
    data_files: list[dict[str, Any]] = field(default_factory=list)
    code_files: list[dict[str, Any]] = field(default_factory=list)
    preprocessing_steps: list[str] = field(default_factory=list)
    data_join_logic: Optional[str] = None
    train_test_split_method: Optional[str] = None
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    metrics_mentioned_in_code: dict[str, float] = field(default_factory=dict)
    dependencies_detected: list[str] = field(default_factory=list)
    known_limitations_from_comments: list[str] = field(default_factory=list)
    llm_warnings: list[str] = field(default_factory=list)
    comprehension_confidence: float = 0.0
    comprehension_gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelProfile:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ValidationConfig
# ---------------------------------------------------------------------------

@dataclass
class ValidationConfig:
    validation_dir: str = "validations"
    timeout_sec: int = 3600
    stage_timeout_sec: int = 600
    sandbox_mem_mb: int = 4096
    sandbox_cpu_sec: int = 120
    comprehension_model: str = "anthropic/claude-opus-4.6"
    comprehension_effort: str = "high"
    synthesis_model: str = "anthropic/claude-opus-4.6"
    improvement_model: str = "anthropic/claude-opus-4.6"
    maturity_threshold: int = 20
    evo_min_bundles_early: int = 1
    evo_min_bundles_mature: int = 3
    auto_evolve: bool = True
    auto_improve: bool = True
    auto_self_assess: bool = True
    report_model: str = "anthropic/claude-opus-4.6"
    methodology_version: str = "0.1.0"
    improvement_lift_threshold: float = 0.01
    max_hard_recommendations: int = 10
    max_soft_recommendations: int = 10
    inbox_dir: str = "ml-models-to-validate"
    auto_ingest: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ValidationConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
