"""Tests for dynamic check registry CRUD."""
import json
import pytest
from pathlib import Path
from ouroboros.validation.check_registry import CheckRegistry, ValidationCheck, load_check_function


@pytest.fixture
def registry(tmp_path):
    """Create a registry in a temp dir with proper structure."""
    checks_dir = tmp_path / "ouroboros" / "validation" / "checks"
    checks_dir.mkdir(parents=True)
    (checks_dir / "__init__.py").touch()
    (checks_dir / "check_manifest.json").write_text("[]")
    return CheckRegistry(tmp_path)


def _make_check(check_id="TEST.001", stage="S2", enabled=True, tags=None):
    return ValidationCheck(
        check_id=check_id, stage=stage, name="Test check",
        description="A test", check_type="deterministic",
        enabled=enabled, created_by="test", created_at="2026-01-01",
        version=1, tags=tags or [],
        implementation_path=f"checks/{check_id.lower().replace('.', '_')}.py",
    )


def test_add_and_list(registry):
    """Can add a check and list it."""
    registry.add_check(_make_check("TEST.001"))
    checks = registry.list_checks()
    assert len(checks) == 1
    assert checks[0].check_id == "TEST.001"


def test_list_filters_by_stage(registry):
    registry.add_check(_make_check("S2.A", stage="S2"))
    registry.add_check(_make_check("S3.B", stage="S3"))
    s2_checks = registry.list_checks(stage="S2")
    assert len(s2_checks) == 1
    assert s2_checks[0].check_id == "S2.A"


def test_disable_hides_from_list(registry):
    registry.add_check(_make_check("TEST.001"))
    registry.disable_check("TEST.001", "test reason")
    assert len(registry.list_checks(enabled_only=True)) == 0
    assert len(registry.list_checks(enabled_only=False)) == 1


def test_delete_removes_completely(registry):
    registry.add_check(_make_check("TEST.001"))
    registry.delete_check("TEST.001", "test reason")
    assert len(registry.list_checks(enabled_only=False)) == 0


def test_get_unknown_check_raises(registry):
    with pytest.raises(KeyError):
        registry.get_check("NONEXISTENT")


def test_manifest_persists_to_disk(registry):
    """Manifest survives reload from disk."""
    registry.add_check(_make_check("TEST.001"))
    # _checks_dir = repo_dir/ouroboros/validation/checks → .parent^3 = repo_dir
    registry2 = CheckRegistry(registry._checks_dir.parent.parent.parent)
    checks = registry2.list_checks()
    assert len(checks) == 1


def test_tag_filtering(registry):
    """get_checks_for_stage filters by model_profile tags."""
    registry.add_check(_make_check("S2.A", tags=["tabular", "classification"]))
    registry.add_check(_make_check("S2.B", tags=["tabular", "regression"]))
    registry.add_check(_make_check("S2.C", tags=[]))  # no tags = applies to all

    profile = {"model_type": "classification", "data_format": "tabular"}
    matches = registry.get_checks_for_stage("S2", profile)
    ids = [c.check_id for c in matches]
    assert "S2.A" in ids       # matches classification + tabular
    assert "S2.C" in ids       # no tags = universal
    assert "S2.B" not in ids   # regression doesn't match classification


def test_load_check_function(registry, tmp_path):
    """Can dynamically load and call a check's run() function."""
    check = _make_check("TEST.LOAD")
    # Write actual check code
    check_file = tmp_path / "ouroboros" / "validation" / check.implementation_path
    check_file.parent.mkdir(parents=True, exist_ok=True)
    check_file.write_text('''
from ouroboros.validation.types import CheckResult
def run(bundle_dir, model_profile, sandbox=None):
    return CheckResult(
        check_id="TEST.LOAD", check_name="Load test",
        severity="pass", passed=True, score=1.0,
        details="OK", evidence={},
        methodology_version="test", improvement_suggestion=None,
    )
''')
    registry.add_check(check)
    fn = load_check_function(check, tmp_path)
    result = fn(tmp_path, {})
    assert result.passed is True
    assert result.check_id == "TEST.LOAD"
