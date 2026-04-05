"""Test inbox path resolution."""
import pytest
from pathlib import Path
from ouroboros.validation.watcher import ValidationWatcher, resolve_inbox_dir
from ouroboros.validation.types import ValidationConfig


def test_resolve_inbox_relative(tmp_path, monkeypatch):
    """Relative inbox_dir resolves against DATA_DIR."""
    monkeypatch.setattr("ouroboros.validation.watcher._get_data_dir", lambda: tmp_path)
    cfg = ValidationConfig(inbox_dir="ml-models-to-validate")
    resolved = resolve_inbox_dir(cfg)
    assert resolved == tmp_path / "ml-models-to-validate"
    assert resolved.exists()  # created automatically


def test_resolve_inbox_absolute(tmp_path, monkeypatch):
    """Absolute inbox_dir is used as-is."""
    abs_path = tmp_path / "custom_inbox"
    abs_path.mkdir()
    monkeypatch.setattr("ouroboros.validation.watcher._get_data_dir", lambda: tmp_path)
    cfg = ValidationConfig(inbox_dir=str(abs_path))
    resolved = resolve_inbox_dir(cfg)
    assert resolved == abs_path


def test_watcher_uses_resolved_path(tmp_path, monkeypatch):
    """ValidationWatcher uses the resolved inbox path."""
    monkeypatch.setattr("ouroboros.validation.watcher._get_data_dir", lambda: tmp_path)
    cfg = ValidationConfig(inbox_dir="ml-models-to-validate")
    watcher = ValidationWatcher(
        inbox_dir=resolve_inbox_dir(cfg),
        validations_dir=tmp_path / "validations",
        repo_dir=tmp_path,
        config=cfg,
    )
    assert watcher._inbox_dir == tmp_path / "ml-models-to-validate"
