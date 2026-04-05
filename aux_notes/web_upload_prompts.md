# Web UI Model Upload — Implementation Prompts

**How to use:** Execute these 4 prompts sequentially in separate Claude Code sessions.

**Start each session by saying:**
> Read `aux_notes/web_upload_plan.md` — this is the detailed plan.
> Then execute the prompt below.

**Dependency:** Strictly sequential: 1 → 2 → 3 → 4.

---

## Prompt 1 of 4: Inbox Path Resolution + Docker Fix

```
Read the web upload plan aux_notes/web_upload_plan.md, sections §3.1 and §4.1.

The inbox directory must live inside OUROBOROS_DATA_DIR so it persists in
Docker volumes and is accessible from the file browser.

### Files to modify:

1. ouroboros/validation/watcher.py — Add a class method or module-level
   function that resolves the inbox path:

   def resolve_inbox_dir(config: ValidationConfig) -> Path:
       """Resolve inbox_dir relative to DATA_DIR if not absolute."""
       from ouroboros.config import DATA_DIR
       inbox = Path(config.inbox_dir)
       if not inbox.is_absolute():
           inbox = DATA_DIR / config.inbox_dir
       inbox.mkdir(parents=True, exist_ok=True)
       return inbox

   Update the ValidationWatcher class to use this internally — if inbox_dir
   is passed as relative, resolve against DATA_DIR.

2. ouroboros/config.py — No change to the default value
   ("ml-models-to-validate" stays as-is). The resolution happens at runtime
   in the watcher. But verify the default is still correct.

3. docker-compose.yml — Remove the separate inbox bind mount line:
   ```
   - ./ml-models-to-validate:/inbox
   ```
   The inbox is now inside the valoboros-data volume at /data/ml-models-to-validate/.

   Also remove or update the OUROBOROS_VALIDATION_INBOX_DIR env var since it's
   now resolved relative to DATA_DIR:
   ```
   - OUROBOROS_VALIDATION_INBOX_DIR=ml-models-to-validate
   ```

4. Dockerfile — Remove the `/inbox` directory creation if present (inbox is
   now a subdirectory of /data, created at runtime). Keep /data and /repo.

### Verify

```python
"""Test inbox path resolution."""
import pytest
from pathlib import Path
from ouroboros.validation.watcher import ValidationWatcher, resolve_inbox_dir
from ouroboros.validation.types import ValidationConfig


def test_resolve_inbox_relative(tmp_path, monkeypatch):
    """Relative inbox_dir resolves against DATA_DIR."""
    monkeypatch.setattr("ouroboros.validation.watcher.DATA_DIR", tmp_path)
    cfg = ValidationConfig(inbox_dir="ml-models-to-validate")
    resolved = resolve_inbox_dir(cfg)
    assert resolved == tmp_path / "ml-models-to-validate"
    assert resolved.exists()  # created automatically


def test_resolve_inbox_absolute(tmp_path, monkeypatch):
    """Absolute inbox_dir is used as-is."""
    abs_path = tmp_path / "custom_inbox"
    abs_path.mkdir()
    monkeypatch.setattr("ouroboros.validation.watcher.DATA_DIR", tmp_path)
    cfg = ValidationConfig(inbox_dir=str(abs_path))
    resolved = resolve_inbox_dir(cfg)
    assert resolved == abs_path


def test_watcher_uses_resolved_path(tmp_path, monkeypatch):
    """ValidationWatcher resolves relative inbox_dir against DATA_DIR."""
    monkeypatch.setattr("ouroboros.validation.watcher.DATA_DIR", tmp_path)
    cfg = ValidationConfig(inbox_dir="ml-models-to-validate")
    watcher = ValidationWatcher(
        inbox_dir=resolve_inbox_dir(cfg),
        validations_dir=tmp_path / "validations",
        repo_dir=tmp_path,
        config=cfg,
    )
    assert watcher._inbox_dir == tmp_path / "ml-models-to-validate"
```

Save as tests/test_inbox_resolution.py and run:
`.venv/bin/python -m pytest tests/test_inbox_resolution.py -v`

Also verify docker-compose is still valid:
`docker compose config --quiet 2>&1 && echo "compose: OK" || echo "compose: check needed"`

Also run existing watcher tests to confirm no regressions:
`.venv/bin/python -m pytest tests/test_watcher.py -v`
```

---

## Prompt 2 of 4: Server Validation API Endpoints

```
Read the web upload plan aux_notes/web_upload_plan.md, sections §3.2 and §4.2.

Create 4 new API endpoints for validation operations.

### Files to create:

1. ouroboros/server_validation_api.py — Module with 4 endpoints:

   a) POST /api/validation/upload
      - Accept multipart form: "file" (UploadFile) + "task" (string, optional)
      - Resolve inbox path using resolve_inbox_dir(config)
      - Save the uploaded ZIP to inbox directory (reuse chunked upload pattern
        from file_browser_api.py — 1MB chunks, but 1GB max limit)
      - After save, call watcher.ingest(zip_path, task) to create the bundle
      - Return {"ok": true, "bundle_id": "...", "status": "ingested", "filename": "..."}
      - On error: return appropriate HTTP status + error message

   b) GET /api/validation/list
      - Accept optional query param: status (all/pending/validating/completed/failed)
      - Call _list_validations_impl(validations_dir, status)
      - Return JSON array of bundles

   c) POST /api/validation/run
      - Accept JSON body: {"bundle_id": "..."}
      - Create ValidationPipeline and run it (asyncio)
      - Return {"ok": true, "bundle_id": "...", "status": "started"}
      - Note: for the initial implementation, run synchronously and return
        the result. Async task scheduling can be added later.

   d) GET /api/validation/report
      - Accept query param: bundle_id
      - Read results/report.json from the bundle directory
      - Return the report JSON
      - Also support ?format=md to return the markdown report as text

   Read ouroboros/file_browser_api.py lines 505-558 for the upload pattern.
   Read ouroboros/tools/model_intake.py for _ingest_model_artifacts_impl and
   _list_validations_impl.
   Read ouroboros/validation/watcher.py for resolve_inbox_dir and ValidationWatcher.

   Important: the upload endpoint needs access to DATA_DIR and config.
   Import from ouroboros.config and ouroboros.validation.config_loader.

### Files to modify:

2. server.py — Register the new routes.
   Find where file_browser_routes() is called and add validation_api_routes()
   in the same place:

   ```python
   from ouroboros.server_validation_api import validation_api_routes
   # In routes list:
   *validation_api_routes(),
   ```

### Verify

```bash
# 1. Start the server briefly to check routes load without error
timeout 5 .venv/bin/python server.py 2>&1 | head -20 || true

# 2. Verify the new module imports correctly
.venv/bin/python -c "from ouroboros.server_validation_api import validation_api_routes; print(f'{len(validation_api_routes())} routes OK')"

# 3. Run existing tests to confirm no regressions
.venv/bin/python -m pytest tests/test_validation_types.py tests/test_intake.py tests/test_watcher.py tests/test_integration.py --tb=short -q
```

Write and run tests/test_validation_api.py:

```python
"""Tests for the validation API endpoints."""
import json, pytest, zipfile, io
from pathlib import Path
from starlette.testclient import TestClient


@pytest.fixture
def test_app(tmp_path, monkeypatch):
    """Create a minimal Starlette app with validation routes for testing."""
    monkeypatch.setenv("OUROBOROS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OUROBOROS_REPO_DIR", str(tmp_path / "repo"))
    monkeypatch.setenv("OUROBOROS_FILE_BROWSER_DEFAULT", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    (tmp_path / "repo").mkdir()

    from starlette.applications import Starlette
    from starlette.routing import Route
    from ouroboros.server_validation_api import validation_api_routes
    app = Starlette(routes=validation_api_routes())
    return TestClient(app)


def _make_zip_bytes(filename="train.py", content="print('hello')"):
    """Create a ZIP file in memory and return bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
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
    # Should contain the bundle (either as JSON array or message)
    body = list_resp.text
    assert bundle_id in body


def test_report_not_found(test_app):
    """Report for nonexistent bundle returns 404."""
    response = test_app.get("/api/validation/report?bundle_id=nonexistent")
    assert response.status_code == 404
```

Run: `.venv/bin/python -m pytest tests/test_validation_api.py -v`
All tests must pass. Install starlette test client if needed:
`.venv/bin/pip install httpx` (starlette TestClient requires httpx)
```

---

## Prompt 3 of 4: Web UI Validation Tab

```
Read the web upload plan aux_notes/web_upload_plan.md, sections §3.3 and §3.4.

Create the Validation tab in the web UI with upload zone, validation list,
and report viewer.

### Files to modify:

1. web/index.html — Add a nav button for the Validation tab.
   Find the existing nav buttons (data-page="chat", "files", "logs", etc.)
   and add AFTER the "files" button and BEFORE the "logs" button:

   ```html
   <button class="nav-btn" data-page="validation" title="Validation">
       <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="2" stroke-linecap="round"
            stroke-linejoin="round">
           <path d="M9 11l3 3L22 4"/>
           <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
       </svg>
   </button>
   ```

   (This is a checkmark-in-box icon representing validation.)

2. web/app.js — Import and initialize the validation module.
   Add after the `initFiles(ctx);` line:

   ```javascript
   import { initValidation } from './modules/validation.js';
   // ... after initFiles(ctx):
   initValidation(ctx);
   ```

### Files to create:

3. web/modules/validation.js — Full validation tab implementation (~300 lines):

   Structure:
   - Export function initValidation({ ws, state })
   - Create the page HTML with 3 sections:
     a) Upload zone — drag-drop area + file input + task textarea + upload button
     b) Validation list — table with columns: Model, Status, Verdict, Actions
     c) Report viewer — hidden by default, shows when user clicks "View Report"

   Key behaviors:
   - On page show: fetch /api/validation/list and populate the table
   - Upload:
     - Validate file is .zip before sending
     - POST /api/validation/upload with FormData (file + task)
     - On success: show bundle_id, add to list, start polling
   - Polling: every 10 seconds, re-fetch /api/validation/list to update statuses
   - "Validate" button on pending bundles: POST /api/validation/run
   - "View Report" button on completed bundles: GET /api/validation/report
   - Report displayed as formatted JSON or markdown (use <pre> for simplicity)

   Follow the same patterns as other modules (files.js, evolution.js):
   - Create page element with innerHTML
   - Attach event listeners
   - Register with the page system via state

   Drag-drop upload pattern (from files.js):
   - dragover → add highlight class
   - dragleave → remove highlight
   - drop → validate .zip → upload

   Status badge styling:
   - pending → gray
   - validating → blue/animated
   - completed → green (approved) / yellow (conditional) / red (rejected)
   - failed → red

### Verify

```bash
# 1. Verify files exist
test -f web/modules/validation.js && echo "validation.js: OK" || echo "validation.js: MISSING"

# 2. Verify nav button added
grep -q 'data-page="validation"' web/index.html && echo "nav button: OK" || echo "nav button: MISSING"

# 3. Verify app.js imports the module
grep -q "initValidation" web/app.js && echo "app.js import: OK" || echo "app.js import: MISSING"

# 4. Verify the module exports initValidation
grep -q "export function initValidation" web/modules/validation.js && echo "export: OK" || echo "export: MISSING"

# 5. Verify upload endpoint is called
grep -q "/api/validation/upload" web/modules/validation.js && echo "upload API: OK" || echo "upload API: MISSING"

# 6. Verify list endpoint is called
grep -q "/api/validation/list" web/modules/validation.js && echo "list API: OK" || echo "list API: MISSING"

# 7. Run all existing tests to confirm nothing broke
.venv/bin/python -m pytest tests/test_validation_types.py tests/test_sandbox.py tests/test_check_registry.py tests/test_intake.py tests/test_integration.py tests/test_watcher.py --tb=short -q
```
```

---

## Prompt 4 of 4: Styles + Polish + README Update

```
Read the web upload plan aux_notes/web_upload_plan.md.

Final polish: add CSS styles for the Validation tab, update the README
and tutorial to document the new upload feature.

### Files to modify:

1. web/style.css — Add styles for the validation tab. Append at the end:

   - .validation-layout: flexbox column, padding
   - .upload-zone: border dashed, rounded corners, centered text, min-height 150px
   - .upload-zone.drag-over: border color highlight (accent color)
   - .upload-zone .drop-area: cursor pointer, hover highlight
   - #val-task: full-width textarea, 3 rows, matching input styles
   - #val-upload-btn: button matching existing app button styles
   - .validation-list table: full width, matching existing table styles
   - .status-badge: inline pill with color per status
     - .status-pending: gray background
     - .status-validating: blue background, optional pulse animation
     - .status-completed: green background
     - .status-rejected: red background
     - .status-failed: red background
   - .report-viewer: bordered container, max-height with scroll, monospace for reports
   - .report-viewer pre: white-space pre-wrap

   Match the existing dark theme from style.css (look at .form-field, .nav-btn,
   table styles for color values and patterns).

2. README.md — Add a brief mention of the web upload feature in the
   "Docker Deployment" section, after "Drop models for validation":

   ### Upload via web UI

   Navigate to the **Validation** tab in the web UI to:
   - Drag-and-drop model ZIP files for validation
   - Add a task description
   - Monitor validation progress in real-time
   - View validation reports in-browser

3. aux_notes/valoboros_tutorial.md — Add a brief section in Part 1 after
   "Step 1: Ingest the model":

   ### Alternative: Upload via Web UI

   Instead of using Python scripts, you can upload models through the web UI:
   1. Open `http://your-server:8765` in your browser
   2. Click the **Validation** tab (checkmark icon in the sidebar)
   3. Drag your model ZIP into the upload zone (or click to browse)
   4. Type a task description (e.g., "Predict early repayment rate for consumer loans")
   5. Click "Upload & Validate"
   6. Watch the validation progress in the list below
   7. Click "View Report" when complete

### Verify

```bash
# 1. Verify styles added
grep -q "validation-layout" web/style.css && echo "styles: OK" || echo "styles: MISSING"
grep -q "upload-zone" web/style.css && echo "upload zone style: OK" || echo "upload zone style: MISSING"
grep -q "status-badge" web/style.css && echo "status badges: OK" || echo "status badges: MISSING"

# 2. Verify README updated
grep -q "Upload via web UI" README.md && echo "README: OK" || echo "README: MISSING"

# 3. Verify tutorial updated
grep -q "Upload via Web UI" aux_notes/valoboros_tutorial.md && echo "tutorial: OK" || echo "tutorial: MISSING"

# 4. Run full test suite
.venv/bin/python -m pytest tests/test_validation_types.py tests/test_sandbox.py tests/test_check_registry.py tests/test_seed_checks.py tests/test_stage_orchestrators.py tests/test_intake.py tests/test_synthesis_report.py tests/test_effectiveness.py tests/test_improvement_cycle.py tests/test_integration.py tests/test_dependency_extractor.py tests/test_watcher.py tests/test_reflection_engine.py tests/test_methodology_planner.py tests/test_literature_and_evolution.py tests/test_project_structure.py tests/test_model_researcher.py tests/test_research_pipeline_integration.py --tb=short -q
```
```

---

## Summary

| Prompt | Creates | Modifies | Tests | LOC est. |
|--------|---------|----------|-------|----------|
| 1 | — | `watcher.py`, `docker-compose.yml`, `Dockerfile` | 3 | ~40 |
| 2 | `server_validation_api.py` | `server.py` | 6 | ~200 |
| 3 | `web/modules/validation.js` | `web/index.html`, `web/app.js` | grep checks | ~300 |
| 4 | — | `web/style.css`, `README.md`, `valoboros_tutorial.md` | grep + full suite | ~80 |
| **Total** | **2 new files** | **8 modified** | **9 + greps** | **~620** |
