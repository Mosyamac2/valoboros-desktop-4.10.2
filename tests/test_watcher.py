"""Tests for the folder watcher — auto-detection and processing of new ZIPs."""
import json
import pytest
import zipfile
from pathlib import Path
from ouroboros.validation.watcher import ValidationWatcher
from ouroboros.validation.types import ValidationConfig


@pytest.fixture
def inbox(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    return d


@pytest.fixture
def validations_dir(tmp_path):
    d = tmp_path / "validations"
    d.mkdir()
    return d


def _make_model_zip(inbox, name="test_model.zip"):
    zip_path = inbox / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("train.py", "import pandas\nprint('training')\n")
    return zip_path


def test_scan_finds_new_zips(inbox, validations_dir, tmp_path):
    """scan_inbox detects new ZIP files."""
    _make_model_zip(inbox, "model_a.zip")
    _make_model_zip(inbox, "model_b.zip")
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    new = watcher.scan_inbox()
    assert len(new) == 2
    assert any(p.name == "model_a.zip" for p in new)


def test_scan_ignores_processed(inbox, validations_dir, tmp_path):
    """Already-processed ZIPs are not returned by scan_inbox."""
    _make_model_zip(inbox, "already_done.zip")
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    watcher.mark_processed("already_done.zip", "bundle-123", "completed")
    new = watcher.scan_inbox()
    assert len(new) == 0


def test_scan_ignores_non_zip(inbox, validations_dir, tmp_path):
    """Non-ZIP files in inbox are ignored."""
    (inbox / "readme.txt").write_text("not a model")
    (inbox / "data.csv").write_text("a,b\n1,2")
    _make_model_zip(inbox, "real_model.zip")
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    new = watcher.scan_inbox()
    assert len(new) == 1
    assert new[0].name == "real_model.zip"


def test_mark_processed_persists(inbox, validations_dir, tmp_path):
    """Processed state survives watcher re-instantiation."""
    watcher1 = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    watcher1.mark_processed("model.zip", "bundle-1", "completed")
    # New instance reads the same file
    watcher2 = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    processed = watcher2._load_processed()
    assert "model.zip" in processed
    assert processed["model.zip"]["bundle_id"] == "bundle-1"


def test_processed_file_location(inbox, validations_dir, tmp_path):
    """Processed tracking file is inside the inbox directory."""
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    watcher.mark_processed("test.zip", "b1", "ingested")
    assert (inbox / ".valoboros_processed.json").exists()


def test_ingest_creates_bundle(inbox, validations_dir, tmp_path):
    """ingest creates a bundle directory with correct structure."""
    zip_path = _make_model_zip(inbox, "new_model.zip")
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    bundle_id = watcher.ingest(zip_path, task="Test model validation")
    bundle_dir = validations_dir / bundle_id
    assert bundle_dir.exists()
    assert (bundle_dir / "raw" / "model_code" / "train.py").exists()
    assert (bundle_dir / "inputs" / "task.txt").exists()


def test_ingest_marks_as_processed(inbox, validations_dir, tmp_path):
    """After ingest, the ZIP is marked as processed."""
    zip_path = _make_model_zip(inbox, "auto_model.zip")
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    watcher.ingest(zip_path, task="Test")
    # Should not appear in scan anymore
    new = watcher.scan_inbox()
    assert len(new) == 0
    # Should be in processed file
    processed = watcher._load_processed()
    assert "auto_model.zip" in processed
    assert processed["auto_model.zip"]["status"] == "ingested"


def test_empty_inbox(inbox, validations_dir, tmp_path):
    """Empty inbox returns no new ZIPs."""
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    assert watcher.scan_inbox() == []


def test_nonexistent_inbox(tmp_path):
    """Watcher handles nonexistent inbox dir gracefully."""
    watcher = ValidationWatcher(
        tmp_path / "does_not_exist",
        tmp_path / "validations",
        tmp_path,
        ValidationConfig(),
    )
    assert watcher.scan_inbox() == []
