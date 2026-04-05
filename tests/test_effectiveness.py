"""Tests for effectiveness tracker — the core of the self-improvement loop."""
import json
import pytest
from pathlib import Path
from ouroboros.validation.effectiveness import EffectivenessTracker


@pytest.fixture
def tracker(tmp_path):
    return EffectivenessTracker(tmp_path)


def test_starts_in_early_phase(tracker):
    assert tracker.maturity_phase == "early"


def test_records_self_assessment(tracker):
    """Tier 0 self-assessed feedback is recorded with weight 0.3."""
    tracker.record_finding_feedback(
        "S2.OOS", "bundle-1", "true_positive", source="self_assessed", weight=0.3
    )
    stats = tracker.get_finding_stats("S2.OOS")
    assert stats.self_assessed_tp == 1


def test_human_feedback_overrides_self(tracker):
    """Tier 2 human label has weight 1.0, higher than Tier 0."""
    tracker.record_finding_feedback(
        "S2.OOS", "bundle-1", "false_positive", source="self_assessed", weight=0.3
    )
    tracker.record_finding_feedback(
        "S2.OOS", "bundle-1", "true_positive", source="human", weight=1.0
    )
    stats = tracker.get_finding_stats("S2.OOS")
    assert stats.human_tp == 1
    # Weighted: 0.3 FP + 1.0 TP → precision = 1.0 / (1.0 + 0.3) > 0.7
    assert stats.precision > 0.7


def test_recommendation_tracking_independent(tracker):
    """Recommendation quality tracked separately from finding quality."""
    tracker.record_finding_feedback(
        "S3.OVERFIT", "b1", "true_positive", source="human", weight=1.0
    )
    tracker.record_recommendation_result(
        "S3.OVERFIT", "b1",
        metric_before={"AUC": 0.70}, metric_after={"AUC": 0.68}
    )
    f_stats = tracker.get_finding_stats("S3.OVERFIT")
    r_stats = tracker.get_recommendation_stats("S3.OVERFIT")
    # Finding is TP (correct diagnosis) but recommendation degraded metrics
    assert f_stats.human_tp == 1
    assert r_stats.recommendations_degraded == 1


def test_maturity_transition(tracker):
    """Phase transitions from early to mature at threshold."""
    for i in range(20):
        tracker.record_finding_feedback(
            "S2.OOS", f"bundle-{i}", "true_positive", source="human", weight=1.0
        )
    assert tracker.maturity_phase == "mature"


def test_evolution_targets_early_phase(tracker):
    """Early phase targets focus on crashes and obvious issues."""
    tracker.record_finding_feedback(
        "S2.OOS", "b1", "false_positive", source="self_assessed", weight=0.3
    )
    targets = tracker.get_evolution_targets()
    # Should produce targets even with minimal data
    assert isinstance(targets, list)


def test_underperformers(tracker):
    """Checks with low precision are flagged."""
    for i in range(5):
        tracker.record_finding_feedback(
            "BAD.CHECK", f"b{i}", "false_positive", source="human", weight=1.0
        )
    tracker.record_finding_feedback(
        "BAD.CHECK", "b5", "true_positive", source="human", weight=1.0
    )
    underperformers = tracker.get_underperformers(min_samples=5, max_precision=0.5)
    assert "BAD.CHECK" in underperformers  # 1 TP, 5 FP → precision ~0.17


def test_persistence(tracker, tmp_path):
    """Data survives reloading from disk."""
    tracker.record_finding_feedback(
        "S2.OOS", "b1", "true_positive", source="human", weight=1.0
    )
    tracker2 = EffectivenessTracker(tmp_path)
    stats = tracker2.get_finding_stats("S2.OOS")
    assert stats.human_tp == 1


def test_platform_metrics(tracker):
    """Platform metrics are computed correctly."""
    tracker.record_finding_feedback("S2.OOS", "b1", "true_positive", source="human", weight=1.0)
    tracker.record_finding_feedback("S2.OOS", "b2", "false_positive", source="human", weight=1.0)
    tracker.record_recommendation_result("S2.OOS", "b1", {"AUC": 0.7}, {"AUC": 0.75})
    metrics = tracker.get_platform_metrics()
    assert metrics.maturity_phase == "early"
    assert metrics.total_bundles_with_feedback == 2
    assert metrics.mean_improvement_lift > 0
    assert metrics.total_improvement_cycles == 1


def test_recommendation_stats_lift(tracker):
    """Improvement lift is correctly categorized."""
    tracker.record_recommendation_result("C1", "b1", {"AUC": 0.7}, {"AUC": 0.75})  # improved
    tracker.record_recommendation_result("C1", "b2", {"AUC": 0.7}, {"AUC": 0.65})  # degraded
    tracker.record_recommendation_result("C1", "b3", {"AUC": 0.7}, {"AUC": 0.705})  # unchanged (~0)
    stats = tracker.get_recommendation_stats("C1")
    assert stats.recommendations_improved == 1
    assert stats.recommendations_degraded == 1
    assert stats.recommendations_unchanged == 1
