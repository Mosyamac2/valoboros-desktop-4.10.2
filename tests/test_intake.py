"""Tests for model intake tool — file handling, ZIP extraction, directory structure."""
import json
import pytest
import zipfile
from pathlib import Path
from ouroboros.tools.model_intake import (
    _ingest_model_artifacts_impl,
    _list_validations_impl,
    _get_validation_status_impl,
)


def _make_code_zip(tmp_path) -> Path:
    """Create a minimal model code ZIP."""
    zip_path = tmp_path / "model_code.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("train.py", "from sklearn.ensemble import RandomForestClassifier\nprint('train')\n")
        zf.writestr("utils.py", "def load_data(): pass\n")
    return zip_path


def _make_data_zip(tmp_path) -> Path:
    """Create a minimal data ZIP."""
    zip_path = tmp_path / "data.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("train.csv", "a,b,target\n1,2,0\n3,4,1\n5,6,0\n")
    return zip_path


def test_ingest_creates_directory_structure(tmp_path):
    """After ingestion, all expected directories exist."""
    code_zip = _make_code_zip(tmp_path)
    data_zip = _make_data_zip(tmp_path)
    validations_dir = tmp_path / "validations"
    validations_dir.mkdir()

    bundle_id = _ingest_model_artifacts_impl(
        validations_dir=validations_dir,
        model_code_zip=str(code_zip),
        task="Predict customer churn",
        data_zip=str(data_zip),
        data_description="Customer data with churn labels",
    )

    bundle_dir = validations_dir / bundle_id
    assert (bundle_dir / "raw" / "model_code" / "train.py").exists()
    assert (bundle_dir / "raw" / "data_samples" / "train.csv").exists()
    assert (bundle_dir / "inputs" / "task.txt").read_text() == "Predict customer churn"
    assert (bundle_dir / "inputs" / "data_description.txt").exists()
    assert (bundle_dir / "inferred").is_dir()
    assert (bundle_dir / "results").is_dir()
    assert (bundle_dir / "improvement").is_dir()
    assert (bundle_dir / "improvement" / "implementation").is_dir()
    assert (bundle_dir / "improvement" / "revalidation").is_dir()


def test_ingest_without_data_zip(tmp_path):
    """Ingestion works without data — data_samples dir exists but is empty."""
    code_zip = _make_code_zip(tmp_path)
    validations_dir = tmp_path / "validations"
    validations_dir.mkdir()

    bundle_id = _ingest_model_artifacts_impl(
        validations_dir=validations_dir,
        model_code_zip=str(code_zip),
        task="Predict churn",
    )

    bundle_dir = validations_dir / bundle_id
    assert (bundle_dir / "raw" / "model_code" / "train.py").exists()
    assert (bundle_dir / "inputs" / "task.txt").exists()


def test_ingest_invalid_zip(tmp_path):
    """Ingesting a non-ZIP file should raise."""
    bad_file = tmp_path / "not_a_zip.txt"
    bad_file.write_text("this is not a zip")
    validations_dir = tmp_path / "validations"
    validations_dir.mkdir()

    with pytest.raises(Exception):  # zipfile.BadZipFile
        _ingest_model_artifacts_impl(
            validations_dir=validations_dir,
            model_code_zip=str(bad_file),
            task="test",
        )


def test_ingest_missing_file(tmp_path):
    """Ingesting a nonexistent file should raise."""
    validations_dir = tmp_path / "validations"
    validations_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        _ingest_model_artifacts_impl(
            validations_dir=validations_dir,
            model_code_zip="/nonexistent/path.zip",
            task="test",
        )


def test_list_validations_empty(tmp_path):
    """list_validations on empty dir returns appropriate message."""
    validations_dir = tmp_path / "validations"
    validations_dir.mkdir()
    result = _list_validations_impl(validations_dir)
    assert "No validations" in result


def test_list_validations_with_bundles(tmp_path):
    """list_validations returns ingested bundles."""
    code_zip = _make_code_zip(tmp_path)
    validations_dir = tmp_path / "validations"
    validations_dir.mkdir()

    bid = _ingest_model_artifacts_impl(
        validations_dir=validations_dir,
        model_code_zip=str(code_zip),
        task="Churn prediction",
    )

    result = _list_validations_impl(validations_dir)
    parsed = json.loads(result)
    assert len(parsed) == 1
    assert parsed[0]["bundle_id"] == bid
    assert parsed[0]["status"] == "pending"


def test_get_validation_status(tmp_path):
    """get_validation_status returns bundle info."""
    code_zip = _make_code_zip(tmp_path)
    validations_dir = tmp_path / "validations"
    validations_dir.mkdir()

    bid = _ingest_model_artifacts_impl(
        validations_dir=validations_dir,
        model_code_zip=str(code_zip),
        task="Test task",
    )

    result = _get_validation_status_impl(validations_dir, bid)
    info = json.loads(result)
    assert info["bundle_id"] == bid
    assert info["status"] == "pending"
    assert info["code_files"] == 2  # train.py + utils.py


def test_get_validation_status_unknown(tmp_path):
    """get_validation_status for nonexistent bundle returns error message."""
    validations_dir = tmp_path / "validations"
    validations_dir.mkdir()
    result = _get_validation_status_impl(validations_dir, "nonexistent")
    assert "not found" in result.lower()
