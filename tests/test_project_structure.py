"""Tests for per-model project structure and execution logging."""
import json
import pytest
import zipfile
from pathlib import Path
from ouroboros.validation.types import ValidationConfig


def _make_test_bundle(tmp_path):
    """Create a minimal ingested bundle for testing."""
    from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
    code_zip = tmp_path / "code.zip"
    with zipfile.ZipFile(code_zip, "w") as zf:
        zf.writestr("train.py", "import pandas\nprint('hello')\n")
    data_zip = tmp_path / "data.zip"
    with zipfile.ZipFile(data_zip, "w") as zf:
        zf.writestr("train.csv", "a,b,target\n1,2,0\n3,4,1\n")
    val_dir = tmp_path / "validations"
    val_dir.mkdir()
    bundle_id = _ingest_model_artifacts_impl(
        val_dir, str(code_zip), "Test task", str(data_zip), "Test data",
    )
    return val_dir / bundle_id


def test_methodology_dir_created(tmp_path):
    """Pipeline creates methodology/ directory structure."""
    from ouroboros.validation.pipeline import ValidationPipeline
    bundle_dir = _make_test_bundle(tmp_path)
    config = ValidationConfig(auto_self_assess=False, auto_improve=False)
    pipeline = ValidationPipeline(
        bundle_dir.name, bundle_dir, Path(__file__).parent.parent, config,
    )
    assert (bundle_dir / "methodology").is_dir()
    assert (bundle_dir / "methodology" / "custom_checks").is_dir()


def test_log_method_writes(tmp_path):
    """_log() appends timestamped lines to validation.log."""
    from ouroboros.validation.pipeline import ValidationPipeline
    bundle_dir = _make_test_bundle(tmp_path)
    config = ValidationConfig(auto_self_assess=False, auto_improve=False)
    pipeline = ValidationPipeline(
        bundle_dir.name, bundle_dir, Path(__file__).parent.parent, config,
    )
    pipeline._log("Test message 1")
    pipeline._log("Test message 2")
    log_path = bundle_dir / "validation.log"
    assert log_path.exists()
    content = log_path.read_text()
    assert "Test message 1" in content
    assert "Test message 2" in content
    assert "UTC" in content  # timestamps present
    lines = [l for l in content.strip().splitlines() if l.strip()]
    assert len(lines) == 2


def test_validation_log_written(tmp_path):
    """After pipeline run, validation.log exists with stage entries."""
    import asyncio
    from ouroboros.validation.pipeline import ValidationPipeline
    bundle_dir = _make_test_bundle(tmp_path)
    config = ValidationConfig(
        auto_self_assess=False, auto_improve=False,
        comprehension_model="anthropic/claude-sonnet-4",
    )
    pipeline = ValidationPipeline(
        bundle_dir.name, bundle_dir, Path(__file__).parent.parent, config,
    )
    try:
        asyncio.run(pipeline.run())
    except Exception:
        pass  # LLM may not be available
    log_path = bundle_dir / "validation.log"
    assert log_path.exists()
    content = log_path.read_text()
    # Should at least log the pipeline start and S0
    assert "Starting validation pipeline" in content
    assert "S0" in content


def test_results_dir_has_stage_files(tmp_path):
    """After pipeline run, results/ contains stage JSON files."""
    import asyncio
    from ouroboros.validation.pipeline import ValidationPipeline
    bundle_dir = _make_test_bundle(tmp_path)
    config = ValidationConfig(
        auto_self_assess=False, auto_improve=False,
        comprehension_model="anthropic/claude-sonnet-4",
    )
    pipeline = ValidationPipeline(
        bundle_dir.name, bundle_dir, Path(__file__).parent.parent, config,
    )
    try:
        asyncio.run(pipeline.run())
    except Exception:
        pass
    results = bundle_dir / "results"
    stage_files = list(results.glob("stage_S*.json"))
    assert len(stage_files) >= 1


def test_full_bundle_structure(tmp_path):
    """Verify the complete per-model project structure."""
    bundle_dir = _make_test_bundle(tmp_path)
    for subdir in [
        "raw/model_code",
        "raw/data_samples",
        "inputs",
        "inferred",
        "results",
        "improvement/implementation",
        "improvement/revalidation",
    ]:
        assert (bundle_dir / subdir).is_dir(), f"Missing: {subdir}"
    assert (bundle_dir / "inputs" / "task.txt").exists()
    assert (bundle_dir / "raw" / "model_code" / "train.py").exists()
    assert (bundle_dir / "raw" / "data_samples" / "train.csv").exists()
