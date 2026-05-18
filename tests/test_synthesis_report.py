"""Test synthesis and report generation with mocked LLM."""
import json
import pytest
from pathlib import Path
from ouroboros.validation.types import (
    CheckResult,
    ImprovementRecommendation,
    ValidationConfig,
    ValidationReport,
    ValidationStageResult,
)
from ouroboros.validation.report import ReportGenerator


def _make_failed_check(check_id, details):
    return CheckResult(
        check_id=check_id, check_name=check_id, severity="warning",
        passed=False, score=0.5, details=details, evidence={"val": 0.5},
        methodology_version="test", improvement_suggestion=None,
    )


def test_report_json_roundtrip():
    """Report can be serialized to JSON and parsed back."""
    report = ValidationReport(
        bundle_id="test-123", model_profile={"model_type": "classification"},
        overall_verdict="conditional",
        stages=[ValidationStageResult("S0", "Intake", "passed", [], 1.0, None)],
        critical_findings=[_make_failed_check("S3.OVERFIT", "Gap too large")],
        hard_recommendations=[
            ImprovementRecommendation(
                finding_check_id="S3.OVERFIT", problem="Overfit",
                recommendation="Add regularization", kind="hard",
                implementation_sketch="Ridge(alpha=1.0)",
                estimated_metric_impact={"AUC": 0.03},
                confidence=0.7, effort="trivial", priority=1,
            )
        ],
        soft_recommendations=[
            ImprovementRecommendation(
                finding_check_id="S3.SMALL_DATA", problem="Too few rows",
                recommendation="Collect more data", kind="soft",
                implementation_sketch="", estimated_metric_impact={},
                confidence=0.9, effort="infeasible", priority=2,
            )
        ],
        estimated_total_improvement={"AUC": 0.03},
        generated_at="2026-01-01T00:00:00Z",
        methodology_snapshot="abc123",
        meta_scores={"comprehension_confidence": 0.85},
    )
    gen = ReportGenerator()
    j = gen.generate_json(report)
    parsed = json.loads(j)
    assert parsed["bundle_id"] == "test-123"
    assert len(parsed["hard_recommendations"]) == 1
    assert len(parsed["soft_recommendations"]) == 1
    assert parsed["hard_recommendations"][0]["kind"] == "hard"
    assert parsed["soft_recommendations"][0]["kind"] == "soft"


def test_report_save_creates_files(tmp_path):
    """save() creates both report.json and report.md."""
    report = ValidationReport(
        bundle_id="test", model_profile={}, overall_verdict="approved",
        stages=[], critical_findings=[], hard_recommendations=[],
        soft_recommendations=[], estimated_total_improvement={},
        generated_at="2026-01-01", methodology_snapshot="test",
        meta_scores={},
    )
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    gen = ReportGenerator()
    gen.save(report, tmp_path, ValidationConfig())
    assert (results_dir / "report.json").exists()
    assert (results_dir / "report.md").exists()
    # report.md should have template content even without LLM
    md = (results_dir / "report.md").read_text()
    assert "Validation Report" in md
    assert "approved" in md


def test_report_markdown_has_recommendations():
    """Markdown template includes hard and soft recommendation sections."""
    report = ValidationReport(
        bundle_id="test", model_profile={"model_type": "classification", "algorithm": "RF"},
        overall_verdict="conditional",
        stages=[ValidationStageResult("S3", "Fit", "failed", [], 1.0, None)],
        critical_findings=[],
        hard_recommendations=[
            ImprovementRecommendation(
                finding_check_id="S3.OVERFIT", problem="Overfit",
                recommendation="Add Ridge", kind="hard",
                implementation_sketch="Ridge(alpha=1.0)",
                estimated_metric_impact={"AUC": 0.03},
                confidence=0.7, effort="trivial", priority=1,
            )
        ],
        soft_recommendations=[
            ImprovementRecommendation(
                finding_check_id="S3.SMALL", problem="Small data",
                recommendation="Get more data", kind="soft",
                implementation_sketch="", estimated_metric_impact={},
                confidence=0.9, effort="infeasible", priority=2,
            )
        ],
        estimated_total_improvement={"AUC": 0.03},
        generated_at="2026-01-01", methodology_snapshot="test",
        meta_scores={"comprehension_confidence": 0.85},
    )
    gen = ReportGenerator()
    md = gen._template_markdown(report)
    assert "Hard Recommendations" in md
    assert "Soft Recommendations" in md
    assert "Ridge(alpha=1.0)" in md
    assert "Get more data" in md


@pytest.mark.asyncio
async def test_synthesis_fallback_without_llm(tmp_path):
    """Synthesis produces fallback recommendations when LLM is unavailable."""
    from ouroboros.validation.synthesis import run_stage
    from ouroboros.validation.check_registry import CheckRegistry
    from ouroboros.validation.types import ModelProfile

    profile = ModelProfile(
        bundle_id="test", task_description="test",
        model_type="classification", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="RF", data_format="tabular",
    )

    prior_stages = [
        ValidationStageResult(
            stage="S3", stage_name="Fit", status="failed",
            checks=[
                CheckResult(
                    check_id="S3.OVERFIT", check_name="Overfit",
                    severity="warning", passed=False, score=0.15,
                    details="Train/test gap too large",
                    evidence={}, methodology_version="test",
                    improvement_suggestion="Add regularization (e.g., Ridge(alpha=1.0))",
                ),
                CheckResult(
                    check_id="S3.SMALL", check_name="Small data",
                    severity="info", passed=False, score=None,
                    details="Only 100 rows",
                    evidence={}, methodology_version="test",
                    improvement_suggestion=None,  # no suggestion → will become soft
                ),
            ],
            duration_sec=1.0, error_message=None,
        )
    ]

    # Create minimal bundle dir structure
    (tmp_path / "improvement").mkdir(parents=True)

    # Use a fake repo_dir — synthesis doesn't need the check registry
    result = await run_stage(
        tmp_path, profile, None, None, ValidationConfig(),
        prior_stages=prior_stages,
    )

    assert result.stage == "S9"
    assert result.status == "passed"
    assert len(result.checks) == 1  # summary check

    # Verify plan.json was written
    plan_path = tmp_path / "improvement" / "plan.json"
    assert plan_path.exists()
    plan = json.loads(plan_path.read_text())

    # S3.OVERFIT had improvement_suggestion → hard rec
    # S3.SMALL had no suggestion → soft rec
    assert len(plan["hard"]) >= 1
    assert len(plan["soft"]) >= 1
    assert plan["hard"][0]["finding_check_id"] == "S3.OVERFIT"
    assert plan["soft"][0]["finding_check_id"] == "S3.SMALL"
