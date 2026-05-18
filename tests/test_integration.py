"""Final integration test — end-to-end validation of a simple model."""
import json
import pytest
import zipfile
from pathlib import Path


def test_safety_critical_paths():
    """sandbox.py is protected."""
    from ouroboros.tools.registry import SAFETY_CRITICAL_PATHS
    assert "ouroboros/validation/sandbox.py" in SAFETY_CRITICAL_PATHS


def test_reflection_markers():
    """Validation error markers are registered."""
    from ouroboros.reflection import _ERROR_MARKERS
    for marker in [
        "VALIDATION_PIPELINE_ERROR", "SANDBOX_TIMEOUT", "CHECK_REGRESSION",
        "IMPROVEMENT_DEGRADED", "COMPREHENSION_FAILED", "USELESS_RECOMMENDATION",
        "SELF_ASSESSMENT_BIAS_DETECTED",
    ]:
        assert marker in _ERROR_MARKERS, f"Missing error marker: {marker}"


def test_full_pipeline_smoke(tmp_path):
    """End-to-end: ingest a model, run checks, verify results."""
    # Create model code ZIP
    code_zip = tmp_path / "model.zip"
    with zipfile.ZipFile(code_zip, "w") as zf:
        zf.writestr("train.py", (
            "import pandas as pd\n"
            "from sklearn.ensemble import RandomForestClassifier\n"
            "from sklearn.model_selection import train_test_split\n"
            "df = pd.read_csv('data/train.csv')\n"
            "X = df[['a', 'b']]\n"
            "y = df['target']\n"
            "X_train, X_test, y_train, y_test = train_test_split(X, y, random_state=42)\n"
            "model = RandomForestClassifier(n_estimators=10, random_state=42)\n"
            "model.fit(X_train, y_train)\n"
            "print(f'accuracy: {model.score(X_test, y_test)}')\n"
        ))

    # Create data ZIP
    data_zip = tmp_path / "data.zip"
    with zipfile.ZipFile(data_zip, "w") as zf:
        rows = "a,b,target\n" + "\n".join(
            f"{i},{i*2},{i%2}" for i in range(100)
        )
        zf.writestr("train.csv", rows)

    # Ingest
    from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
    val_dir = tmp_path / "validations"
    val_dir.mkdir()
    bundle_id = _ingest_model_artifacts_impl(
        validations_dir=val_dir,
        model_code_zip=str(code_zip),
        task="Binary classification on synthetic data",
        data_zip=str(data_zip),
        data_description="Two numeric features, binary target",
    )

    bundle_dir = val_dir / bundle_id
    assert bundle_dir.exists()
    assert (bundle_dir / "raw" / "model_code" / "train.py").exists()
    assert (bundle_dir / "raw" / "data_samples" / "train.csv").exists()

    # Run deterministic checks only (no LLM, no sandbox)
    from ouroboros.validation.check_registry import CheckRegistry, load_check_function
    repo_dir = Path(__file__).parent.parent
    registry = CheckRegistry(repo_dir)

    # S0 checks
    s0_checks = registry.get_checks_for_stage("S0", {"data_format": "tabular"})
    assert len(s0_checks) >= 2  # code_parseable + data_loadable
    for check in s0_checks:
        fn = load_check_function(check, repo_dir)
        result = fn(bundle_dir, {"data_format": "tabular", "target_column": "target"})
        assert result.check_id.startswith("S0")
        if "PARSEABLE" in result.check_id:
            assert result.passed is True
        if "LOADABLE" in result.check_id:
            assert result.passed is True

    # S4 leakage check on data with no leakage
    s4_checks = registry.get_checks_for_stage("S4", {"data_format": "tabular", "model_type": "classification"})
    for check in s4_checks:
        fn = load_check_function(check, repo_dir)
        result = fn(bundle_dir, {"data_format": "tabular", "target_column": "target"})
        # No leakage in this synthetic data (a and b are simple, not correlated > 0.95 with target)
        assert result.check_id.startswith("S4")

    # S8 code smells — model code has random_state and train_test_split, so should pass
    s8_checks = registry.get_checks_for_stage("S8", {"data_format": "tabular"})
    for check in s8_checks:
        fn = load_check_function(check, repo_dir)
        result = fn(bundle_dir, {})
        assert result.check_id.startswith("S8")

    # Effectiveness tracker works from scratch
    from ouroboros.validation.effectiveness import EffectivenessTracker
    tracker = EffectivenessTracker(val_dir)
    assert tracker.maturity_phase == "early"
    tracker.record_finding_feedback("S0.CODE_PARSEABLE", bundle_id, "true_positive", "self_assessed", 0.3)
    stats = tracker.get_finding_stats("S0.CODE_PARSEABLE")
    assert stats.self_assessed_tp == 1

    print(f"Integration smoke test passed for bundle {bundle_id}")
