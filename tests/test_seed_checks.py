"""Tests for seed validation checks — deterministic checks only.
Sandbox and LLM checks are tested with mocks."""
import pytest
from pathlib import Path
from ouroboros.validation.check_registry import CheckRegistry, load_check_function


@pytest.fixture
def repo_dir():
    """Use the actual repo directory so checks can be loaded."""
    return Path(__file__).parent.parent


@pytest.fixture
def bundle_with_python(tmp_path):
    """Create a minimal bundle with parseable Python files."""
    code_dir = tmp_path / "raw" / "model_code"
    code_dir.mkdir(parents=True)
    (code_dir / "train.py").write_text("import sklearn\nprint('hello')\n")
    return tmp_path


@pytest.fixture
def bundle_with_bad_python(tmp_path):
    code_dir = tmp_path / "raw" / "model_code"
    code_dir.mkdir(parents=True)
    (code_dir / "broken.py").write_text("def foo(\n")  # syntax error
    return tmp_path


@pytest.fixture
def bundle_with_csv(tmp_path):
    data_dir = tmp_path / "raw" / "data_samples"
    data_dir.mkdir(parents=True)
    (data_dir / "train.csv").write_text("a,b,target\n1,2,0\n3,4,1\n5,6,0\n")
    return tmp_path


def test_s0_code_parseable_pass(repo_dir, bundle_with_python):
    registry = CheckRegistry(repo_dir)
    check = registry.get_check("S0.CODE_PARSEABLE")
    fn = load_check_function(check, repo_dir)
    result = fn(bundle_with_python, {})
    assert result.passed is True


def test_s0_code_parseable_fail(repo_dir, bundle_with_bad_python):
    registry = CheckRegistry(repo_dir)
    check = registry.get_check("S0.CODE_PARSEABLE")
    fn = load_check_function(check, repo_dir)
    result = fn(bundle_with_bad_python, {})
    assert result.passed is False
    assert "syntax" in result.details.lower() or "parse" in result.details.lower()


def test_s0_data_loadable_pass(repo_dir, bundle_with_csv):
    registry = CheckRegistry(repo_dir)
    check = registry.get_check("S0.DATA_LOADABLE")
    fn = load_check_function(check, repo_dir)
    result = fn(bundle_with_csv, {"data_format": "tabular"})
    assert result.passed is True
    assert "3" in result.details or result.score is not None  # should report row count


def test_s4_target_leakage_detects_correlation(repo_dir, tmp_path):
    """S4 check should flag a feature perfectly correlated with target."""
    data_dir = tmp_path / "raw" / "data_samples"
    data_dir.mkdir(parents=True)
    # 'leaked' is literally the target — should be caught
    (data_dir / "train.csv").write_text(
        "feat1,leaked,target\n1,0,0\n2,1,1\n3,0,0\n4,1,1\n5,0,0\n"
    )
    registry = CheckRegistry(repo_dir)
    check = registry.get_check("S4.TARGET_LEAKAGE")
    fn = load_check_function(check, repo_dir)
    result = fn(tmp_path, {"target_column": "target", "data_format": "tabular"})
    assert result.passed is False  # should detect the leakage
    assert "leaked" in result.details.lower() or "correlation" in result.details.lower()


def test_s8_code_smells_finds_hardcoded_path(repo_dir, tmp_path):
    """S8 check should flag hardcoded absolute paths in model code."""
    code_dir = tmp_path / "raw" / "model_code"
    code_dir.mkdir(parents=True)
    (code_dir / "model.py").write_text(
        'import pandas as pd\ndf = pd.read_csv("/home/user/data/train.csv")\n'
    )
    registry = CheckRegistry(repo_dir)
    check = registry.get_check("S8.CODE_SMELLS")
    fn = load_check_function(check, repo_dir)
    result = fn(tmp_path, {})
    assert result.passed is False
    assert "path" in result.details.lower() or "hardcoded" in result.details.lower()


def test_all_checks_registered(repo_dir):
    """All 9 seed checks exist in the manifest."""
    registry = CheckRegistry(repo_dir)
    checks = registry.list_checks(enabled_only=False)
    assert len(checks) >= 9
    stages_present = {c.stage for c in checks}
    for s in ["S0", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]:
        assert s in stages_present, f"No checks for stage {s}"
