"""Tests for per-model methodology planning."""
import json
import pytest
from pathlib import Path
from ouroboros.validation.methodology_planner import MethodologyPlanner
from ouroboros.validation.check_registry import CheckRegistry
from ouroboros.validation.types import ModelProfile, ValidationConfig, MethodologyPlan


@pytest.fixture
def repo_dir():
    return Path(__file__).parent.parent


@pytest.fixture
def bundle_dir(tmp_path):
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "methodology").mkdir()
    (d / "inferred").mkdir()
    return d


@pytest.fixture
def knowledge_dir(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    return d


@pytest.fixture
def profile():
    return ModelProfile(
        bundle_id="test", task_description="Predict churn",
        model_type="classification", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="RandomForest", data_format="tabular",
        target_column="churn", target_column_confidence=0.9,
    )


def test_fallback_plan_selects_all_applicable(repo_dir, bundle_dir, knowledge_dir, profile):
    """When LLM is unavailable, fallback selects all applicable checks."""
    registry = CheckRegistry(repo_dir)
    planner = MethodologyPlanner(bundle_dir, profile, registry, ValidationConfig(), knowledge_dir)
    plan = planner._fallback_plan()
    assert isinstance(plan, MethodologyPlan)
    assert plan.bundle_id == "test"
    assert len(plan.checks_to_run) >= 5  # most seed checks apply to tabular classification
    assert plan.confidence < 0.5  # fallback should have low confidence


def test_fallback_skips_irrelevant_checks(repo_dir, bundle_dir, knowledge_dir):
    """Fallback plan skips checks tagged for other model types."""
    profile = ModelProfile(
        bundle_id="test", task_description="Predict price",
        model_type="regression", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="Ridge", data_format="tabular",
    )
    registry = CheckRegistry(repo_dir)
    planner = MethodologyPlanner(bundle_dir, profile, registry, ValidationConfig(), knowledge_dir)
    plan = planner._fallback_plan()
    # S5.DISPARATE_IMPACT is tagged ["tabular", "classification"] — should be skipped for regression
    assert "S5.DISPARATE_IMPACT" not in plan.checks_to_run


def test_methodology_md_generated(repo_dir, bundle_dir, knowledge_dir, profile):
    """_generate_methodology_md produces valid Markdown."""
    registry = CheckRegistry(repo_dir)
    planner = MethodologyPlanner(bundle_dir, profile, registry, ValidationConfig(), knowledge_dir)
    plan = planner._fallback_plan()
    md = planner._generate_methodology_md(plan)
    assert "Risk Priorities" in md
    assert "Checks Selected" in md
    assert profile.algorithm in md or profile.model_type in md


def test_methodology_plan_roundtrip():
    """MethodologyPlan serializes and deserializes correctly."""
    plan = MethodologyPlan(
        bundle_id="test", model_summary="A test model",
        risk_priorities=["overfitting", "leakage"],
        checks_to_run=["S2.OOS_METRICS", "S3.TRAIN_TEST_GAP"],
        checks_to_skip=["S5.DISPARATE_IMPACT"],
        checks_to_create=[{"check_id": "S4.CUSTOM", "description": "Custom check"}],
        knowledge_references=[], similar_past_validations=[],
        methodology_version="0.1.0", confidence=0.8,
    )
    d = plan.to_dict()
    plan2 = MethodologyPlan.from_dict(d)
    assert plan2.risk_priorities == ["overfitting", "leakage"]
    assert len(plan2.checks_to_create) == 1
    assert json.dumps(d)  # JSON-serializable


def test_knowledge_base_referenced(repo_dir, bundle_dir, knowledge_dir, profile):
    """Planner reads knowledge base files if they exist."""
    (knowledge_dir / "model_type_classification.md").write_text(
        "# Classification models\nOften have overfitting issues.\n"
    )
    registry = CheckRegistry(repo_dir)
    planner = MethodologyPlanner(bundle_dir, profile, registry, ValidationConfig(), knowledge_dir)
    kb = planner._gather_knowledge()
    assert "classification" in kb.lower() or "overfitting" in kb.lower()


def test_active_stages_from_methodology():
    """Pipeline correctly extracts active stages from methodology plan."""
    from ouroboros.validation.pipeline import ValidationPipeline
    plan = MethodologyPlan(
        checks_to_run=["S0.CODE_PARSEABLE", "S2.OOS_METRICS", "S4.TARGET_LEAKAGE", "S8.CODE_SMELLS"],
    )
    active = ValidationPipeline._get_active_stages(plan)
    assert "S0" in active  # always active
    assert "S1" in active  # always active
    assert "S2" in active  # from checks_to_run
    assert "S4" in active
    assert "S8" in active
    assert "S9" in active  # always active
    assert "S3" not in active  # not in checks_to_run
    assert "S5" not in active
    assert "S6" not in active
    assert "S7" not in active


def test_active_stages_none_methodology():
    """When no methodology, all stages are active."""
    from ouroboros.validation.pipeline import ValidationPipeline
    active = ValidationPipeline._get_active_stages(None)
    for s in ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"]:
        assert s in active


def test_fallback_risk_priorities_temporal(repo_dir, bundle_dir, knowledge_dir):
    """Temporal column in profile → temporal_leakage is top risk priority."""
    profile = ModelProfile(
        bundle_id="test", task_description="Predict",
        model_type="regression", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="XGBoost", data_format="tabular",
        temporal_column="report_date",
    )
    registry = CheckRegistry(repo_dir)
    planner = MethodologyPlanner(bundle_dir, profile, registry, ValidationConfig(), knowledge_dir)
    plan = planner._fallback_plan()
    assert plan.risk_priorities[0] == "temporal_leakage"
