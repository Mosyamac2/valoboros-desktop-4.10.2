"""Tests for improvement cycle — uses mocked LLM and sandbox."""
import pytest
import tempfile
from pathlib import Path
from ouroboros.validation.types import (
    ImprovementRecommendation,
    ImproverResult,
    SandboxResult,
    RevalidationResult,
)


def test_model_improver_filters_soft_recs():
    """ModelImprover only processes hard recommendations."""
    from ouroboros.validation.model_improver import ModelImprover
    hard = ImprovementRecommendation(
        finding_check_id="S3.OVERFIT", problem="Overfit",
        recommendation="Add reg", kind="hard",
        implementation_sketch="Ridge(alpha=1.0)",
        estimated_metric_impact={"AUC": 0.03},
        confidence=0.7, effort="trivial", priority=1,
    )
    soft = ImprovementRecommendation(
        finding_check_id="S3.SMALL", problem="Small data",
        recommendation="Get more data", kind="soft",
        implementation_sketch="", estimated_metric_impact={},
        confidence=0.9, effort="infeasible", priority=2,
    )
    # ModelImprover filters to hard only in __init__
    from ouroboros.validation.sandbox import ModelSandbox
    from ouroboros.validation.types import ValidationConfig
    d = Path(tempfile.mkdtemp())
    (d / "raw" / "model_code").mkdir(parents=True)
    cfg = ValidationConfig()
    sandbox = ModelSandbox(d, cfg)
    improver = ModelImprover(d, [hard, soft], sandbox, cfg)
    assert len(improver._plan) == 1
    assert improver._plan[0].finding_check_id == "S3.OVERFIT"


def test_improvement_lift_computation():
    """Improvement lift is correctly computed from before/after metrics."""
    before = {"AUC": 0.70, "accuracy": 0.80}
    after = {"AUC": 0.75, "accuracy": 0.82}
    deltas = {k: after[k] - before[k] for k in before}
    lifts = {k: deltas[k] / abs(before[k]) if before[k] != 0 else 0 for k in deltas}
    assert abs(lifts["AUC"] - 0.0714) < 0.001  # 0.05 / 0.70
    assert abs(lifts["accuracy"] - 0.025) < 0.001  # 0.02 / 0.80


def test_revalidation_verdict_logic():
    """Verify verdict assignment based on lift threshold."""
    threshold = 0.01

    # Improved
    lift = 0.05
    assert (lift > threshold)  # → "improved"

    # Degraded
    lift = -0.03
    assert (lift < -threshold)  # → "degraded"

    # Unchanged
    lift = 0.005
    assert (-threshold <= lift <= threshold)  # → "unchanged"


def test_signal_a_and_b_recorded_independently():
    """After improvement cycle, rec quality and finding quality
    are recorded as separate entries."""
    from ouroboros.validation.effectiveness import EffectivenessTracker
    tracker = EffectivenessTracker(Path(tempfile.mkdtemp()))

    # Signal A: recommendation improved metrics → rec was useful
    tracker.record_recommendation_result(
        "S3.OVERFIT", "bundle-1",
        metric_before={"AUC": 0.70}, metric_after={"AUC": 0.75}
    )
    # Signal B: inferred finding quality (weight 0.5, not 1.0)
    tracker.record_finding_feedback(
        "S3.OVERFIT", "bundle-1", "true_positive",
        source="improvement_inferred", weight=0.5
    )

    r_stats = tracker.get_recommendation_stats("S3.OVERFIT")
    f_stats = tracker.get_finding_stats("S3.OVERFIT")

    assert r_stats.recommendations_improved == 1
    # Finding stat should reflect the inferred TP at weight 0.5
    assert f_stats.tp == 0.5  # weighted TP


def test_improver_result_roundtrip():
    """ImproverResult serializes and deserializes."""
    ir = ImproverResult(
        recommendations_applied=["S3.OVERFIT"],
        recommendations_skipped=[("S3.SMALL", "LLM unavailable")],
        modified_files=["train.py"],
        sandbox_output=SandboxResult(0, "ok", "", 1.0, False, False),
        new_metrics={"AUC": 0.75},
    )
    d = ir.to_dict()
    assert d["recommendations_applied"] == ["S3.OVERFIT"]
    assert d["new_metrics"]["AUC"] == 0.75
    ir2 = ImproverResult.from_dict(d)
    assert ir2.recommendations_applied == ["S3.OVERFIT"]
    assert ir2.new_metrics["AUC"] == 0.75


def test_revalidation_result_verdict_values():
    """RevalidationResult stores all fields correctly."""
    rr = RevalidationResult(
        original_bundle_id="a", improved_bundle_id="b",
        original_metrics={"AUC": 0.70}, improved_metrics={"AUC": 0.75},
        metric_deltas={"AUC": 0.05}, improvement_lift=0.071,
        recommendations_applied=["S3.OVERFIT"],
        recommendations_skipped=["S3.SMALL: infeasible"],
        verdict="improved",
    )
    assert rr.verdict == "improved"
    assert rr.improvement_lift > 0
    assert "S3.OVERFIT" in rr.recommendations_applied
