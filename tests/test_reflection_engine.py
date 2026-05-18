"""Tests for cross-validation reflection engine."""
import json
import pytest
from pathlib import Path
from ouroboros.validation.reflection_engine import ValidationReflectionEngine
from ouroboros.validation.types import ValidationConfig


@pytest.fixture
def validations_dir(tmp_path):
    d = tmp_path / "validations"
    d.mkdir()
    return d


@pytest.fixture
def knowledge_dir(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    return d


def _create_fake_report(validations_dir, bundle_id, model_type, failed_checks):
    """Create a minimal report.json for testing."""
    bundle_dir = validations_dir / bundle_id / "results"
    bundle_dir.mkdir(parents=True)
    report = {
        "bundle_id": bundle_id,
        "model_profile": {"model_type": model_type, "framework": "sklearn"},
        "overall_verdict": "conditional",
        "stages": [
            {
                "stage": "S0", "stage_name": "Intake", "status": "passed",
                "checks": [], "duration_sec": 1.0, "error_message": None,
            }
        ],
        "critical_findings": [
            {"check_id": cid, "check_name": cid, "severity": "warning",
             "passed": False, "score": None, "details": f"Failed: {cid}",
             "evidence": {}, "methodology_version": "test",
             "improvement_suggestion": None}
            for cid in failed_checks
        ],
        "hard_recommendations": [],
        "soft_recommendations": [],
        "estimated_total_improvement": {},
        "generated_at": "2026-01-01",
        "methodology_snapshot": "test",
        "meta_scores": {},
    }
    (bundle_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")


def test_no_reports_returns_empty(validations_dir, knowledge_dir):
    """With no reports, reflection returns empty result."""
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    result = engine.reflect_sync()
    assert result.total_validations_analyzed == 0
    assert result.patterns_found == []


def test_single_report_returns_empty(validations_dir, knowledge_dir):
    """With only 1 report, no patterns to generalize — returns empty."""
    _create_fake_report(validations_dir, "b1", "classification", ["S4.TARGET_LEAKAGE"])
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    result = engine.reflect_sync()
    assert result.total_validations_analyzed == 1
    assert result.patterns_found == []  # need >= 2 to generalize


def test_detects_common_failures(validations_dir, knowledge_dir):
    """When same check fails across multiple models, it's detected as a pattern."""
    _create_fake_report(validations_dir, "b1", "classification", ["S8.CODE_SMELLS", "S4.TARGET_LEAKAGE"])
    _create_fake_report(validations_dir, "b2", "regression", ["S8.CODE_SMELLS"])
    _create_fake_report(validations_dir, "b3", "classification", ["S8.CODE_SMELLS", "S3.TRAIN_TEST_GAP"])
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    result = engine.reflect_sync()
    assert result.total_validations_analyzed == 3
    # S8.CODE_SMELLS fails in all 3 → should be detected
    check_ids_in_patterns = [p["check_id"] for p in result.patterns_found]
    assert "S8.CODE_SMELLS" in check_ids_in_patterns


def test_detects_dead_checks(validations_dir, knowledge_dir):
    """Checks that exist in registry but never triggered are flagged."""
    _create_fake_report(validations_dir, "b1", "classification", ["S4.TARGET_LEAKAGE"])
    _create_fake_report(validations_dir, "b2", "regression", ["S4.TARGET_LEAKAGE"])
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    all_check_ids = ["S4.TARGET_LEAKAGE", "S5.DISPARATE_IMPACT", "S7.PERTURBATION"]
    result = engine.reflect_sync(registered_check_ids=all_check_ids)
    assert "S5.DISPARATE_IMPACT" in result.dead_checks
    assert "S7.PERTURBATION" in result.dead_checks
    assert "S4.TARGET_LEAKAGE" not in result.dead_checks


def test_groups_by_model_type(validations_dir, knowledge_dir):
    """Patterns include which model types they affect."""
    _create_fake_report(validations_dir, "b1", "classification", ["S3.TRAIN_TEST_GAP"])
    _create_fake_report(validations_dir, "b2", "classification", ["S3.TRAIN_TEST_GAP"])
    _create_fake_report(validations_dir, "b3", "regression", [])
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    result = engine.reflect_sync()
    gap_pattern = [p for p in result.patterns_found if p["check_id"] == "S3.TRAIN_TEST_GAP"]
    assert len(gap_pattern) == 1
    assert "classification" in gap_pattern[0]["model_types"]


def test_writes_knowledge_files(validations_dir, knowledge_dir):
    """Reflection writes validation_patterns.md and model_type_*.md."""
    _create_fake_report(validations_dir, "b1", "classification", ["S8.CODE_SMELLS"])
    _create_fake_report(validations_dir, "b2", "classification", ["S8.CODE_SMELLS"])
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    result = engine.reflect_sync()
    assert "validation_patterns.md" in result.knowledge_entries_written
    assert "model_type_classification.md" in result.knowledge_entries_written
    assert (knowledge_dir / "validation_patterns.md").exists()
    assert (knowledge_dir / "model_type_classification.md").exists()
    # Content should mention the check
    content = (knowledge_dir / "validation_patterns.md").read_text()
    assert "S8.CODE_SMELLS" in content


def test_hot_checks_detected(validations_dir, knowledge_dir):
    """Checks that trigger in every single validation are flagged as hot."""
    _create_fake_report(validations_dir, "b1", "classification", ["S8.CODE_SMELLS"])
    _create_fake_report(validations_dir, "b2", "regression", ["S8.CODE_SMELLS"])
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    result = engine.reflect_sync()
    assert "S8.CODE_SMELLS" in result.hot_checks
