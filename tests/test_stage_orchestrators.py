"""Tests for stage orchestrators — uses real checks but no LLM or sandbox."""
import pytest
from pathlib import Path
from ouroboros.validation.types import ValidationConfig, ModelProfile, ValidationStageResult
from ouroboros.validation.check_registry import CheckRegistry


@pytest.fixture
def repo_dir():
    return Path(__file__).parent.parent


@pytest.fixture
def bundle_with_data(tmp_path):
    code_dir = tmp_path / "raw" / "model_code"
    code_dir.mkdir(parents=True)
    (code_dir / "train.py").write_text("import sklearn\nprint('hello')\n")
    data_dir = tmp_path / "raw" / "data_samples"
    data_dir.mkdir(parents=True)
    (data_dir / "train.csv").write_text("a,b,target\n1,2,0\n3,4,1\n")
    return tmp_path


@pytest.fixture
def profile():
    return ModelProfile(
        bundle_id="test", task_description="test",
        model_type="classification", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="RandomForest", data_format="tabular",
        target_column="target", target_column_confidence=0.9,
        feature_columns=["a", "b"], protected_attributes_candidates=[],
        temporal_column=None, data_files=[], code_files=[],
        preprocessing_steps=[], data_join_logic=None,
        train_test_split_method=None, hyperparameters={},
        metrics_mentioned_in_code={}, dependencies_detected=[],
        known_limitations_from_comments=[], llm_warnings=[],
        comprehension_confidence=0.9, comprehension_gaps=[],
    )


@pytest.mark.asyncio
async def test_intake_runs_s0_checks(repo_dir, bundle_with_data, profile):
    """intake_check orchestrator runs S0 checks and returns results."""
    from ouroboros.validation.intake_check import run_stage
    registry = CheckRegistry(repo_dir)
    cfg = ValidationConfig()
    result = await run_stage(bundle_with_data, profile, registry, None, cfg)
    assert isinstance(result, ValidationStageResult)
    assert result.stage == "S0"
    assert len(result.checks) >= 1  # at least s0_code_parseable should run


@pytest.mark.asyncio
async def test_leakage_runs_s4_checks(repo_dir, bundle_with_data, profile):
    """leakage orchestrator runs S4 checks."""
    from ouroboros.validation.leakage import run_stage
    registry = CheckRegistry(repo_dir)
    cfg = ValidationConfig()
    result = await run_stage(bundle_with_data, profile, registry, None, cfg)
    assert isinstance(result, ValidationStageResult)
    assert result.stage == "S4"


@pytest.mark.asyncio
async def test_orchestrator_catches_check_crash(repo_dir, tmp_path, profile):
    """If a check crashes, orchestrator captures the error, doesn't propagate."""
    from ouroboros.validation.performance import run_stage
    # Empty bundle — sandbox checks will fail/skip gracefully
    (tmp_path / "raw" / "model_code").mkdir(parents=True)
    registry = CheckRegistry(repo_dir)
    cfg = ValidationConfig()
    result = await run_stage(tmp_path, profile, registry, None, cfg)
    assert isinstance(result, ValidationStageResult)
    # Should not raise — errors captured in check results
    for check in result.checks:
        if not check.passed:
            assert check.details  # must have an explanation


@pytest.mark.asyncio
async def test_code_quality_runs_s8_checks(repo_dir, bundle_with_data, profile):
    """code_quality orchestrator runs S8 checks."""
    from ouroboros.validation.code_quality import run_stage
    registry = CheckRegistry(repo_dir)
    cfg = ValidationConfig()
    result = await run_stage(bundle_with_data, profile, registry, None, cfg)
    assert isinstance(result, ValidationStageResult)
    assert result.stage == "S8"
    assert len(result.checks) >= 1


@pytest.mark.asyncio
async def test_synthesis_placeholder_returns_empty(repo_dir, bundle_with_data, profile):
    """S9 synthesis placeholder returns a valid empty result."""
    from ouroboros.validation.synthesis import run_stage
    registry = CheckRegistry(repo_dir)
    cfg = ValidationConfig()
    result = await run_stage(bundle_with_data, profile, registry, None, cfg)
    assert isinstance(result, ValidationStageResult)
    assert result.stage == "S9"
    assert result.status == "passed"
    assert len(result.checks) == 0
