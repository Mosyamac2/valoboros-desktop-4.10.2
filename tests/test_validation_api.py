"""Tests for the validation API endpoints."""
import io
import json
import pytest
import zipfile
from pathlib import Path
from starlette.testclient import TestClient


@pytest.fixture
def test_app(tmp_path, monkeypatch):
    """Create a minimal Starlette app with validation routes for testing."""
    data_dir = tmp_path / "data"
    repo_dir = tmp_path / "repo"
    data_dir.mkdir()
    repo_dir.mkdir()
    monkeypatch.setenv("OUROBOROS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OUROBOROS_REPO_DIR", str(repo_dir))
    monkeypatch.setenv("OUROBOROS_APP_ROOT", str(tmp_path))
    monkeypatch.setenv("OUROBOROS_FILE_BROWSER_DEFAULT", str(data_dir))

    # Force config module to re-read env
    import ouroboros.config
    monkeypatch.setattr(ouroboros.config, "DATA_DIR", data_dir)
    monkeypatch.setattr(ouroboros.config, "REPO_DIR", repo_dir)

    from starlette.applications import Starlette
    from ouroboros.server_validation_api import validation_api_routes
    app = Starlette(routes=validation_api_routes())
    return TestClient(app)


def _make_zip_bytes(filename="train.py", content="print('hello')"):
    """Create a ZIP file in memory and return bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(filename, content)
    buf.seek(0)
    return buf.read()


def test_upload_returns_bundle_id(test_app):
    """Upload a ZIP → get bundle_id back."""
    zip_bytes = _make_zip_bytes()
    response = test_app.post(
        "/api/validation/upload",
        files={"file": ("model.zip", zip_bytes, "application/zip")},
        data={"task": "Test classification model"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "bundle_id" in data
    assert data["status"] == "ingested"


def test_upload_without_task(test_app):
    """Upload works without task description."""
    zip_bytes = _make_zip_bytes()
    response = test_app.post(
        "/api/validation/upload",
        files={"file": ("model.zip", zip_bytes, "application/zip")},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_upload_non_zip_rejected(test_app):
    """Non-ZIP file upload is rejected."""
    response = test_app.post(
        "/api/validation/upload",
        files={"file": ("readme.txt", b"not a zip", "text/plain")},
    )
    assert response.status_code == 400


def test_list_empty(test_app):
    """List with no validations returns empty or message."""
    response = test_app.get("/api/validation/list")
    assert response.status_code == 200


def test_list_after_upload(test_app):
    """After upload, list shows the bundle."""
    zip_bytes = _make_zip_bytes()
    upload_resp = test_app.post(
        "/api/validation/upload",
        files={"file": ("model.zip", zip_bytes, "application/zip")},
        data={"task": "Test model"},
    )
    bundle_id = upload_resp.json()["bundle_id"]

    list_resp = test_app.get("/api/validation/list")
    assert list_resp.status_code == 200
    body = list_resp.text
    assert bundle_id in body


def test_report_not_found(test_app):
    """Report for nonexistent bundle returns 404."""
    response = test_app.get("/api/validation/report?bundle_id=nonexistent")
    assert response.status_code == 404
