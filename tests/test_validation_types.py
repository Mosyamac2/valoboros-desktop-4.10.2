"""Tests for validation foundation types."""
import json
import pytest
from ouroboros.validation.types import (
    CheckResult, ValidationStageResult, ImprovementRecommendation,
    ValidationReport, RevalidationResult, SandboxResult,
    ValidationConfig, ModelProfile,
)


def test_check_result_roundtrip():
    """CheckResult serializes to dict and back without data loss."""
    cr = CheckResult(
        check_id="S2.OOS.AUC", check_name="OOS AUC", severity="critical",
        passed=False, score=0.65, details="AUC below threshold",
        evidence={"auc": 0.65, "threshold": 0.7},
        methodology_version="abc123", improvement_suggestion="Increase regularization"
    )
    d = cr.to_dict()
    cr2 = CheckResult.from_dict(d)
    assert cr2.check_id == "S2.OOS.AUC"
    assert cr2.score == 0.65
    assert cr2.evidence["auc"] == 0.65
    assert json.dumps(d)  # must be JSON-serializable


def test_improvement_recommendation_hard_vs_soft():
    """Hard and soft recommendations have correct fields."""
    hard = ImprovementRecommendation(
        finding_check_id="S3.OVERFIT", problem="Train/test gap > 0.1",
        recommendation="Add L2 regularization", kind="hard",
        implementation_sketch="model = Ridge(alpha=1.0)",
        estimated_metric_impact={"AUC": 0.03}, confidence=0.7,
        effort="trivial", priority=1,
    )
    soft = ImprovementRecommendation(
        finding_check_id="S3.SMALL_DATA", problem="Only 500 training rows",
        recommendation="Collect more data from production",
        kind="soft", implementation_sketch="",
        estimated_metric_impact={}, confidence=0.9,
        effort="infeasible", priority=2,
    )
    assert hard.kind == "hard"
    assert soft.kind == "soft"
    assert soft.effort == "infeasible"


def test_validation_report_splits_recommendations():
    """ValidationReport has separate hard and soft recommendation lists."""
    report = ValidationReport(
        bundle_id="test-123", model_profile={},
        overall_verdict="conditional", stages=[],
        critical_findings=[],
        hard_recommendations=[], soft_recommendations=[],
        estimated_total_improvement={"AUC": 0.05},
        generated_at="2026-01-01T00:00:00Z",
        methodology_snapshot="abc123",
        meta_scores={"comprehension_confidence": 0.85},
    )
    assert hasattr(report, 'hard_recommendations')
    assert hasattr(report, 'soft_recommendations')


def test_model_profile_roundtrip():
    """ModelProfile can serialize and deserialize with all optional fields."""
    mp = ModelProfile(
        bundle_id="test", task_description="Predict churn",
        model_type="classification", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.95,
        algorithm="RandomForestClassifier", data_format="tabular",
        target_column="churn", target_column_confidence=0.8,
        feature_columns=["age", "tenure"], protected_attributes_candidates=["gender"],
        temporal_column=None, data_files=[], code_files=[],
        preprocessing_steps=["StandardScaler"], data_join_logic=None,
        train_test_split_method="random 80/20", hyperparameters={"n_estimators": 100},
        metrics_mentioned_in_code={"accuracy": 0.85},
        dependencies_detected=["sklearn", "pandas"],
        known_limitations_from_comments=[], llm_warnings=[],
        comprehension_confidence=0.9, comprehension_gaps=[],
    )
    d = mp.to_dict()
    mp2 = ModelProfile.from_dict(d)
    assert mp2.algorithm == "RandomForestClassifier"
    assert mp2.comprehension_confidence == 0.9
    assert json.dumps(d)  # JSON-serializable


def test_validation_config_defaults():
    """ValidationConfig has sensible defaults matching the plan."""
    cfg = ValidationConfig()
    assert cfg.sandbox_mem_mb == 4096
    assert cfg.sandbox_cpu_sec == 120
    assert cfg.maturity_threshold == 20
    assert cfg.evo_min_bundles_early == 1
    assert cfg.evo_min_bundles_mature == 3
    assert cfg.auto_self_assess is True
    assert cfg.improvement_lift_threshold == 0.01
    assert cfg.max_hard_recommendations == 10
    assert cfg.max_soft_recommendations == 10


def test_revalidation_result_verdict():
    """RevalidationResult correctly stores verdict and metric deltas."""
    rr = RevalidationResult(
        original_bundle_id="a", improved_bundle_id="b",
        original_metrics={"AUC": 0.70}, improved_metrics={"AUC": 0.75},
        metric_deltas={"AUC": 0.05}, improvement_lift=0.071,
        recommendations_applied=["S3.OVERFIT"],
        recommendations_skipped=[], verdict="improved",
    )
    assert rr.verdict == "improved"
    assert rr.metric_deltas["AUC"] == 0.05


def test_sandbox_result_roundtrip():
    """SandboxResult serializes and deserializes."""
    sr = SandboxResult(
        returncode=0, stdout="hello", stderr="",
        duration_sec=1.5, oom_killed=False, timeout_killed=False,
    )
    d = sr.to_dict()
    sr2 = SandboxResult.from_dict(d)
    assert sr2.returncode == 0
    assert sr2.stdout == "hello"
    assert sr2.duration_sec == 1.5
    assert json.dumps(d)


def test_validation_stage_result_roundtrip():
    """ValidationStageResult with nested CheckResults roundtrips."""
    cr = CheckResult(
        check_id="S0.PARSE", check_name="Code parseable",
        severity="pass", passed=True, score=None,
        details="All files parsed", evidence={},
        methodology_version="abc", improvement_suggestion=None,
    )
    vsr = ValidationStageResult(
        stage="S0", stage_name="Intake", status="passed",
        checks=[cr], duration_sec=2.3, error_message=None,
    )
    d = vsr.to_dict()
    vsr2 = ValidationStageResult.from_dict(d)
    assert vsr2.stage == "S0"
    assert len(vsr2.checks) == 1
    assert vsr2.checks[0].check_id == "S0.PARSE"
    assert json.dumps(d)


def test_validation_report_full_roundtrip():
    """ValidationReport with all nested objects roundtrips through JSON."""
    cr = CheckResult(
        check_id="S3.OVERFIT", check_name="Overfit",
        severity="warning", passed=False, score=0.15,
        details="Train/test gap", evidence={"gap": 0.15},
        methodology_version="abc", improvement_suggestion="Add regularization",
    )
    stage = ValidationStageResult(
        stage="S3", stage_name="Fit Quality", status="failed",
        checks=[cr], duration_sec=5.0, error_message=None,
    )
    hard_rec = ImprovementRecommendation(
        finding_check_id="S3.OVERFIT", problem="Overfit",
        recommendation="Add Ridge", kind="hard",
        implementation_sketch="Ridge(alpha=1.0)",
        estimated_metric_impact={"AUC": 0.03},
        confidence=0.7, effort="trivial", priority=1,
    )
    soft_rec = ImprovementRecommendation(
        finding_check_id="S3.SMALL", problem="Small data",
        recommendation="Get more data", kind="soft",
        implementation_sketch="", estimated_metric_impact={},
        confidence=0.9, effort="infeasible", priority=2,
    )
    report = ValidationReport(
        bundle_id="test-full", model_profile={"model_type": "classification"},
        overall_verdict="conditional", stages=[stage],
        critical_findings=[cr],
        hard_recommendations=[hard_rec], soft_recommendations=[soft_rec],
        estimated_total_improvement={"AUC": 0.03},
        generated_at="2026-01-01T00:00:00Z",
        methodology_snapshot="abc123",
        meta_scores={"confidence": 0.85},
    )
    d = report.to_dict()
    json_str = json.dumps(d)  # must be JSON-serializable
    parsed = json.loads(json_str)

    report2 = ValidationReport.from_dict(parsed)
    assert report2.bundle_id == "test-full"
    assert len(report2.stages) == 1
    assert report2.stages[0].checks[0].check_id == "S3.OVERFIT"
    assert len(report2.hard_recommendations) == 1
    assert report2.hard_recommendations[0].kind == "hard"
    assert len(report2.soft_recommendations) == 1
    assert report2.soft_recommendations[0].kind == "soft"


def test_model_profile_defaults():
    """ModelProfile fields with defaults can be omitted in from_dict."""
    minimal = {
        "bundle_id": "x", "task_description": "test",
        "model_type": "classification", "model_type_confidence": 0.9,
        "framework": "sklearn", "framework_confidence": 0.9,
        "algorithm": "RF", "data_format": "tabular",
    }
    mp = ModelProfile.from_dict(minimal)
    assert mp.bundle_id == "x"
    assert mp.feature_columns == []
    assert mp.comprehension_confidence == 0.0
    assert mp.target_column is None


def test_validation_config_roundtrip():
    """ValidationConfig serializes and deserializes."""
    cfg = ValidationConfig(sandbox_mem_mb=2048, auto_improve=False)
    d = cfg.to_dict()
    cfg2 = ValidationConfig.from_dict(d)
    assert cfg2.sandbox_mem_mb == 2048
    assert cfg2.auto_improve is False
    assert json.dumps(d)
