# Plan: Web UI Model Upload + Validation Inbox Integration

**Date:** 2026-04-05  
**Status:** PLAN ONLY — do not implement yet

---

## 1. Goal

Allow users to upload model ZIP archives via the web UI directly into the
Valoboros validation inbox (`ml-models-to-validate/`), which should live
inside `OUROBOROS_DATA_DIR`. When uploaded, the watcher can detect and
validate them automatically (in full agent mode) or the user can trigger
validation manually (via chat or a UI button).

---

## 2. Current State

### What already exists

| Component | Status | Location |
|-----------|--------|----------|
| File upload API | Fully implemented | `file_browser_api.py` → `/api/files/upload` (POST, multipart, 100MB max, chunked) |
| File browser UI | Fully implemented | `web/modules/files.js` (drag-drop, breadcrumbs, editor, download) |
| Validation watcher | Implemented | `ouroboros/validation/watcher.py` (scans inbox for .zip files) |
| Inbox config | Implemented | `OUROBOROS_VALIDATION_INBOX_DIR` defaults to `"ml-models-to-validate"` |

### What's wrong

1. **Inbox is a relative path** — `"ml-models-to-validate"` is resolved relative
   to the project root, NOT inside `OUROBOROS_DATA_DIR`. On a deployed server,
   this means the inbox is inside the git repo, not in the persistent data volume.

2. **File browser root ≠ inbox** — `OUROBOROS_FILE_BROWSER_DEFAULT` points to
   `OUROBOROS_DATA_DIR` but the inbox is a sibling, not inside it. Users can't
   navigate to the inbox from the Files tab.

3. **No validation-specific upload UX** — the Files tab is a generic file manager.
   There's no "Upload model for validation" button or section. Users would have
   to manually navigate to the inbox folder and drop files there.

4. **No upload-triggers-validation flow** — uploading a ZIP to the inbox doesn't
   trigger anything immediately. The watcher only scans during consciousness
   wakeups (every 5 min). Users expect immediate feedback.

---

## 3. Design

### 3.1. Move inbox inside DATA_DIR

Change `OUROBOROS_VALIDATION_INBOX_DIR` default from `"ml-models-to-validate"`
to `"ml-models-to-validate"` but resolve it relative to `OUROBOROS_DATA_DIR`
instead of project root.

**Resolved paths:**
- Without Docker: `~/Ouroboros/data/ml-models-to-validate/`
- With Docker: `/data/ml-models-to-validate/` (inside the persistent volume)

This ensures:
- Inbox persists across container rebuilds (it's in the data volume)
- File browser can access it (it's under `OUROBOROS_FILE_BROWSER_DEFAULT`)
- Users can upload via the existing Files tab by navigating to `ml-models-to-validate/`

### 3.2. New dedicated validation API endpoints

Add 3 new endpoints to `server.py` (or a new `ouroboros/server_validation_api.py`):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/validation/upload` | POST | Upload a model ZIP directly to inbox (no need to navigate in file browser) |
| `GET /api/validation/list` | GET | List all validation bundles with status |
| `POST /api/validation/run` | POST | Trigger validation for a specific bundle or newly uploaded ZIP |

**`/api/validation/upload`** — thin wrapper around existing upload logic:
- Hardcodes target directory to the inbox path
- Accepts the ZIP + optional `task` description field
- After upload, immediately calls `watcher.ingest()` + queues validation
- Returns `{"bundle_id": "...", "status": "ingested"}`

**`/api/validation/list`** — calls `_list_validations_impl()`:
- Returns JSON array of bundles with status/verdict

**`/api/validation/run`** — triggers validation pipeline:
- Accepts `bundle_id` parameter
- Schedules a validation task (or runs inline for pipeline mode)
- Returns `{"status": "started", "bundle_id": "..."}`

### 3.3. New "Validation" section in the web UI

Two options (in order of increasing complexity):

**Option A: Add to existing Files tab (minimal UI change)**
- Add a "Model Inbox" bookmark/shortcut in the Files tab sidebar
- When in inbox directory, show a "Validate" button next to each .zip file
- Upload here works exactly like regular file upload

**Option B: New "Validation" tab (recommended)**
- New nav button in sidebar: "Validation" (between Files and Logs)
- Tab content:
  - **Upload zone** — drag-drop area for model ZIPs + task description textarea
  - **Inbox queue** — list of uploaded ZIPs with status (pending/validating/completed)
  - **Results list** — completed validations with verdict, link to open report
  - **Report viewer** — inline markdown rendering of the validation report

**Recommended: Option B** — it's cleaner, validation-specific, and doesn't
pollute the general-purpose Files tab. However, Option A is a valid
fast path that requires less work.

### 3.4. Upload-to-validation flow

```
User drags model.zip → Upload zone in Validation tab
  → POST /api/validation/upload (multipart: file + task description)
    → server saves to DATA_DIR/ml-models-to-validate/model.zip
    → server calls watcher.ingest(path, task)
    → server schedules validation task (or returns bundle_id for manual trigger)
  → UI shows "Uploaded: model.zip → Bundle: abc123 — Validating..."
  → UI polls /api/validation/list every 10s for status updates
  → When complete: UI shows verdict + link to report
```

---

## 4. Changes Required

### 4.1. Config: Inbox path resolution

**File:** `ouroboros/validation/watcher.py`

Currently, `inbox_dir` is passed as a `Path` from whoever creates the watcher.
The resolution (relative vs absolute) happens at the call site.

**Change:** When the pipeline or consciousness creates a `ValidationWatcher`,
resolve `config.inbox_dir` relative to `DATA_DIR`:

```python
# In watcher creation (wherever it happens):
from ouroboros.config import DATA_DIR
inbox = Path(config.inbox_dir)
if not inbox.is_absolute():
    inbox = DATA_DIR / config.inbox_dir
inbox.mkdir(parents=True, exist_ok=True)
```

**File:** `docker-compose.yml`

Remove the separate inbox bind mount. The inbox is now inside the data volume:

```yaml
# REMOVE this line:
# - ./ml-models-to-validate:/inbox

# The inbox is now at /data/ml-models-to-validate/ (inside valoboros-data volume)
```

Update `OUROBOROS_VALIDATION_INBOX_DIR`:

```yaml
- OUROBOROS_VALIDATION_INBOX_DIR=ml-models-to-validate
# Resolved as: DATA_DIR / "ml-models-to-validate" = /data/ml-models-to-validate/
```

### 4.2. Server validation API

**New file:** `ouroboros/server_validation_api.py`

```python
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

async def api_validation_upload(request: Request) -> JSONResponse:
    """Upload a model ZIP to the inbox and optionally trigger validation."""
    # 1. Parse multipart form: file + task (optional)
    # 2. Save to DATA_DIR / inbox_dir / filename
    # 3. Call watcher.ingest(path, task)
    # 4. Return {"bundle_id": ..., "status": "ingested"}

async def api_validation_list(request: Request) -> JSONResponse:
    """List all validation bundles with status."""
    # Calls _list_validations_impl(validations_dir)

async def api_validation_run(request: Request) -> JSONResponse:
    """Trigger validation for a bundle."""
    # Reads bundle_id from query params
    # Creates and runs ValidationPipeline (or schedules via supervisor)

async def api_validation_report(request: Request) -> JSONResponse:
    """Get the validation report for a bundle."""
    # Reads bundle_id from query params
    # Returns report.json content

def validation_api_routes() -> list[Route]:
    return [
        Route("/api/validation/upload", endpoint=api_validation_upload, methods=["POST"]),
        Route("/api/validation/list", endpoint=api_validation_list),
        Route("/api/validation/run", endpoint=api_validation_run, methods=["POST"]),
        Route("/api/validation/report", endpoint=api_validation_report),
    ]
```

**File:** `server.py`

Add routes:

```python
from ouroboros.server_validation_api import validation_api_routes

# In routes list:
*validation_api_routes(),
```

### 4.3. Web UI: New Validation tab

**Files to modify/create:**

| File | Change |
|------|--------|
| `web/index.html` | Add nav button: `<button class="nav-btn" data-page="validation" title="Validation">` with appropriate icon |
| `web/modules/validation.js` | New module: upload zone, inbox list, results table, report viewer |
| `web/app.js` | Import and init: `import { initValidation } from './modules/validation.js'; initValidation(ctx);` |
| `web/style.css` | Styles for the validation tab layout |

**`validation.js` structure (~300 lines):**

```javascript
export function initValidation({ ws, state }) {
    const page = document.createElement('div');
    page.id = 'validation-page';
    page.innerHTML = `
        <div class="validation-layout">
            <div class="upload-zone">
                <h3>Upload Model for Validation</h3>
                <div class="drop-area" id="val-drop">
                    Drag & drop a model ZIP here, or click to browse
                </div>
                <textarea id="val-task" placeholder="Describe what this model does (optional)"></textarea>
                <button id="val-upload-btn">Upload & Validate</button>
            </div>
            <div class="validation-list">
                <h3>Validations</h3>
                <table id="val-table">
                    <thead><tr><th>Model</th><th>Status</th><th>Verdict</th><th>Actions</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div class="report-viewer" id="val-report" style="display:none">
                <h3>Validation Report</h3>
                <div id="val-report-content"></div>
            </div>
        </div>
    `;

    // Upload handler: POST /api/validation/upload
    // Poll handler: GET /api/validation/list every 10s
    // Report viewer: GET /api/validation/report?bundle_id=...
    // Markdown rendering: use a simple md→html converter or raw <pre>
}
```

### 4.4. File size limit increase

The current upload limit is 100 MB (`_FILE_BROWSER_MAX_UPLOAD_BYTES`).
Model ZIPs with data samples can be larger.

**File:** `ouroboros/file_browser_api.py`

Option A: Increase global limit to 500 MB.
Option B: The new `/api/validation/upload` endpoint has its own higher limit
(e.g., 1 GB) separate from the general file browser.

**Recommended: Option B** — keep the general file browser at 100 MB, give
the validation upload its own 1 GB limit.

### 4.5. Backward compatibility

The `ml-models-to-validate/` folder in the project root still works for
development/testing (just set `OUROBOROS_VALIDATION_INBOX_DIR` to an absolute path).
Docker deployment uses the data volume path. Both work without code changes
to the watcher — only the path resolution changes.

---

## 5. Implementation Steps

### Step 1: Config and path resolution (~30 LOC)

- Update default inbox path resolution to be relative to `DATA_DIR`
- Create inbox directory on startup if it doesn't exist
- Update `docker-compose.yml` to remove separate inbox mount
- **Smoke test:** `python -c "from ouroboros.config import DATA_DIR; print(DATA_DIR / 'ml-models-to-validate')"`

### Step 2: Server validation API (~150 LOC)

- Create `ouroboros/server_validation_api.py` with 4 endpoints
- Wire into `server.py` routes
- Reuse existing `_ingest_model_artifacts_impl` and `_list_validations_impl`
- **Smoke test:** `curl -X POST http://localhost:8765/api/validation/upload -F "file=@model.zip" -F "task=test"` → returns bundle_id

### Step 3: Web UI Validation tab (~350 LOC)

- Add nav button in `index.html`
- Create `web/modules/validation.js` with upload zone, list, report viewer
- Register in `app.js`
- Add styles in `style.css`
- **Smoke test:** Open web UI → click Validation tab → see upload zone and empty list

### Step 4: Upload-triggers-validation flow (~50 LOC)

- After upload, immediately call `watcher.ingest()` + schedule validation task
- UI polls `/api/validation/list` for status
- Show progress/verdict in real-time
- **Smoke test:** Upload a ZIP → see it appear in list as "validating" → wait → see verdict

### Step 5: Docker adjustments (~10 LOC)

- Update `docker-compose.yml`: remove inbox bind mount, inbox is now in data volume
- Update `Dockerfile` if needed
- **Smoke test:** `docker compose up -d --build` → upload via UI → verify file lands in volume

---

## 6. Estimated Effort

| Component | LOC | Complexity |
|-----------|-----|-----------|
| Config/path resolution | ~30 | Low |
| `server_validation_api.py` | ~150 | Medium |
| `web/modules/validation.js` | ~300 | Medium |
| `web/index.html` changes | ~5 | Low |
| `web/app.js` changes | ~3 | Low |
| `web/style.css` additions | ~50 | Low |
| `docker-compose.yml` update | ~10 | Low |
| Tests | ~80 | Low |
| **Total** | **~630** | **Medium** |

---

## 7. What This Enables

After implementation:

```
User opens http://server:8765
  → Clicks "Validation" tab
  → Drags model.zip into upload zone
  → Types "Predict early repayment rate for consumer loans"
  → Clicks "Upload & Validate"
  → Sees: "Uploaded ✓ — Bundle abc123 — Validating..."
  → [10 seconds later] Sees: "S0 Comprehension: done"
  → [2 minutes later] Sees: "Verdict: conditional — 3 findings, 2 recommendations"
  → Clicks "View Report" → reads the full validation report in-browser
```

No terminal, no SSH, no file system access needed.
