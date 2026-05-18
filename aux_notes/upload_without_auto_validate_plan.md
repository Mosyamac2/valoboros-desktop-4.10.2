# Plan: Upload Without Auto-Validation + Dynamic Status

**Date:** 2026-04-07
**Status:** PLAN — do not implement yet

---

## Goal

When a user uploads a model ZIP via the web UI, it should:
1. Ingest the bundle (create directory structure) — as today
2. Show as "pending" in the validation list — **NOT start validation automatically**
3. User can click "Validate" to start validation manually
4. Alternatively, Valoboros consciousness can discover pending models and validate them
5. User can also tell Valoboros via chat: "validate all pending models"

Additionally, improve status tracking so the list accurately reflects:
- `pending` — uploaded, not yet validated
- `validating` — pipeline currently running
- `completed` — validation finished (with verdict: approved/conditional/rejected)
- `failed` — pipeline crashed or errored
- `revalidating` — improvement cycle running

---

## Current Behavior (what to change)

### Upload flow (broken: auto-triggers validation)

```
User clicks "Upload & Validate"
  → POST /api/validation/upload (saves ZIP, ingests bundle)
  → JS immediately calls POST /api/validation/run (starts pipeline)
  → UI blocks until pipeline completes (~2-5 minutes)
  → User sees "Validation complete: {verdict}"
```

**Problems:**
1. Upload button blocks the UI for minutes while pipeline runs
2. Can't upload multiple models quickly — each one blocks
3. No way to batch-upload then validate selectively
4. Status detection is file-based heuristics (unreliable)

### Status detection (broken: heuristic-based)

Current `_list_validations_impl()` uses file existence as proxy:
- `results/report.json` exists → "completed"
- `inferred/model_profile.json` exists → "validating"
- Neither → "pending"

**Problems:**
- No explicit status storage — can't distinguish "validating" from "S0 done but pipeline crashed"
- No "failed" status — a crashed pipeline looks like "validating" forever
- No "revalidating" status

---

## Proposed Changes

### Change 1: Remove auto-validation from upload JS

**File:** `web/modules/validation.js`

Remove the block that calls `/api/validation/run` after upload. The upload
handler should:
1. POST `/api/validation/upload`
2. On success: show "Uploaded! Bundle: {id}" + refresh list
3. Stop — do NOT call `/api/validation/run`

The "Validate" button already exists on pending rows — it calls `/api/validation/run`.
No new button needed.

### Change 2: Add explicit status file to bundles

**File:** `ouroboros/tools/model_intake.py` → `_ingest_model_artifacts_impl()`

After creating the bundle directory, write a status file:

```python
# At the end of _ingest_model_artifacts_impl():
status_file = bundle_dir / "status.json"
status_file.write_text(json.dumps({
    "status": "pending",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "verdict": None,
    "error": None,
}), encoding="utf-8")
```

### Change 3: Update status at pipeline start/end/error

**File:** `ouroboros/validation/pipeline.py` → `run()`

At the start of `run()`:
```python
self._update_status("validating")
```

At the end (after report generation):
```python
self._update_status("completed", verdict=report.overall_verdict)
```

In exception handler:
```python
self._update_status("failed", error=str(exc))
```

New method:
```python
def _update_status(self, status: str, verdict: str = None, error: str = None):
    status_file = self._bundle_dir / "status.json"
    data = {}
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    data["status"] = status
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    if verdict is not None:
        data["verdict"] = verdict
    if error is not None:
        data["error"] = error
    status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
```

For RevalidationPipeline, set status to "revalidating" at start, "completed" at end.

### Change 4: Update list API to read status.json

**File:** `ouroboros/tools/model_intake.py` → `_list_validations_impl()`

Replace the file-existence heuristic with reading `status.json`:

```python
status_file = entry / "status.json"
if status_file.exists():
    status_data = json.loads(status_file.read_text(encoding="utf-8"))
    bundle_status = status_data.get("status", "pending")
    verdict = status_data.get("verdict", "-")
else:
    # Legacy bundles without status.json — fall back to heuristic
    if (entry / "results" / "report.json").exists():
        bundle_status = "completed"
    else:
        bundle_status = "pending"
    verdict = "-"
```

### Change 5: Make /api/validation/run non-blocking (async)

**File:** `ouroboros/server_validation_api.py` → `api_validation_run()`

Currently `api_validation_run` calls `await pipeline.run()` which blocks the
HTTP response for minutes. Change to:

1. Set status to "validating" immediately
2. Start the pipeline in a background task (`asyncio.create_task`)
3. Return immediately: `{"ok": true, "status": "validating"}`
4. Pipeline writes status on completion/failure

```python
async def api_validation_run(request: Request) -> JSONResponse:
    body = await request.json()
    bundle_id = body.get("bundle_id")
    # ...validation...
    
    # Set status to validating
    _write_status(bundle_dir, "validating")
    
    # Start pipeline in background (non-blocking)
    asyncio.create_task(_run_pipeline_background(bundle_id, bundle_dir, config))
    
    return JSONResponse({"ok": True, "bundle_id": bundle_id, "status": "validating"})

async def _run_pipeline_background(bundle_id, bundle_dir, config):
    try:
        pipeline = ValidationPipeline(bundle_id, bundle_dir, REPO_DIR, config)
        report = await pipeline.run()
        _write_status(bundle_dir, "completed", verdict=report.overall_verdict)
    except Exception as exc:
        _write_status(bundle_dir, "failed", error=str(exc))
```

This means:
- "Validate" button returns instantly, list shows "validating"
- UI polls every 10 seconds, sees status change to "completed" or "failed"
- User can upload more models while one is validating

### Change 6: Update JS upload handler and status display

**File:** `web/modules/validation.js`

Upload handler: remove the auto-run block. After successful upload, just refresh
the list and show "Uploaded! Click Validate when ready."

Status badges: add "failed" and "revalidating" styles:
```javascript
if (item.status === 'completed') {
    // "View Report" + "Download" buttons
} else if (item.status === 'pending') {
    // "Validate" button
} else if (item.status === 'validating' || item.status === 'revalidating') {
    // Show spinner or "Running..." text, no action button
} else if (item.status === 'failed') {
    // Show error, "Retry" button (calls /api/validation/run again)
}
```

### Change 7: Consciousness discovers pending models

**Already partially implemented** via the watcher. But the watcher currently
only detects new ZIPs in the inbox — it doesn't check for pending bundles.

**File:** `ouroboros/validation/watcher.py`

Add method:
```python
def list_pending_bundles(self) -> list[str]:
    """Return bundle_ids that have status 'pending'."""
    pending = []
    if not self._validations_dir.exists():
        return pending
    for entry in sorted(self._validations_dir.iterdir()):
        if not entry.is_dir():
            continue
        status_file = entry / "status.json"
        if status_file.exists():
            try:
                data = json.loads(status_file.read_text(encoding="utf-8"))
                if data.get("status") == "pending":
                    pending.append(entry.name)
            except Exception:
                pass
    return pending
```

Consciousness can call this during wakeup and decide to validate pending models.

---

## Files to Change

| File | Change | LOC |
|------|--------|-----|
| `web/modules/validation.js` | Remove auto-run after upload, add failed/revalidating styles, show "Retry" for failed | ~20 |
| `ouroboros/tools/model_intake.py` | Write `status.json` on ingest, read `status.json` in list | ~25 |
| `ouroboros/validation/pipeline.py` | `_update_status()` method, call at start/end/error | ~20 |
| `ouroboros/server_validation_api.py` | Non-blocking `api_validation_run` via `asyncio.create_task` | ~25 |
| `ouroboros/validation/watcher.py` | `list_pending_bundles()` method | ~15 |
| `web/style.css` | Add `.status-failed`, `.status-revalidating` styles | ~5 |
| **Total** | | **~110** |

---

## User Experience After Changes

### Upload workflow
```
User drags model_a.zip → "Uploaded! Bundle: abc123" → row shows "pending"
User drags model_b.zip → "Uploaded! Bundle: def456" → row shows "pending"
User drags model_c.zip → "Uploaded! Bundle: ghi789" → row shows "pending"
User clicks "Validate" on abc123 → status changes to "validating" instantly
User continues uploading while abc123 validates in background
abc123 completes → status changes to "completed", verdict shown
User clicks "Validate" on def456...
```

### Via chat
```
User: "validate all pending models"
Valoboros: "I see 3 pending models. Starting validation of def456..."
```

### Via consciousness
```
[Consciousness wakeup]
→ watcher.list_pending_bundles() returns ["ghi789"]
→ "I notice a pending model. Starting validation..."
→ Schedules validation task for ghi789
```

### Status lifecycle
```
Upload     → pending
Validate   → validating → completed (approved/conditional/rejected)
                        → failed (with error message)
Revalidate → revalidating → completed (new verdict)
```
