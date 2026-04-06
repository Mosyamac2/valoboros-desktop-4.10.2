"""
Valoboros — validation API endpoints.

Provides model upload, validation listing, run trigger, and report access
via HTTP endpoints for the web UI.
"""

from __future__ import annotations

import json
import logging
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

log = logging.getLogger(__name__)

_UPLOAD_CHUNK_SIZE = 1_048_576       # 1 MB
_MAX_UPLOAD_BYTES = 1_073_741_824    # 1 GB


def _get_paths() -> tuple[Path, Path]:
    """Return (validations_dir, inbox_dir) resolved from config."""
    from ouroboros.config import DATA_DIR
    from ouroboros.validation.config_loader import load_validation_config
    from ouroboros.validation.watcher import resolve_inbox_dir

    config = load_validation_config()
    validations_dir = DATA_DIR / config.validation_dir
    validations_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir = resolve_inbox_dir(config)
    return validations_dir, inbox_dir


# ---------------------------------------------------------------------------
# POST /api/validation/upload
# ---------------------------------------------------------------------------

async def api_validation_upload(request: Request) -> JSONResponse:
    """Upload a model ZIP to the inbox and ingest it."""
    try:
        form = await request.form()
        upload = form.get("file")
        task = str(form.get("task") or "")

        if not isinstance(upload, UploadFile):
            return JSONResponse({"error": "Missing file upload."}, status_code=400)

        filename = (upload.filename or "model.zip").strip()
        if not filename.lower().endswith(".zip"):
            return JSONResponse(
                {"error": "Only .zip files are accepted for validation."},
                status_code=400,
            )

        validations_dir, inbox_dir = _get_paths()

        # Save to inbox
        destination = inbox_dir / filename
        tmp_destination = destination.with_name(f".{destination.name}.uploading")
        bytes_written = 0
        try:
            with tmp_destination.open("wb") as handle:
                while True:
                    chunk = await upload.read(_UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > _MAX_UPLOAD_BYTES:
                        return JSONResponse(
                            {"error": f"Upload exceeds {_MAX_UPLOAD_BYTES // (1024*1024)} MB limit."},
                            status_code=413,
                        )
                    handle.write(chunk)
            tmp_destination.replace(destination)
        finally:
            await upload.close()
            if tmp_destination.exists():
                with suppress(Exception):
                    tmp_destination.unlink()

        # Verify it's a valid ZIP
        if not zipfile.is_zipfile(destination):
            with suppress(Exception):
                destination.unlink()
            return JSONResponse(
                {"error": "Uploaded file is not a valid ZIP archive."},
                status_code=400,
            )

        # Ingest via watcher
        from ouroboros.validation.config_loader import load_validation_config
        from ouroboros.validation.watcher import ValidationWatcher

        config = load_validation_config()
        from ouroboros.config import REPO_DIR
        watcher = ValidationWatcher(inbox_dir, validations_dir, REPO_DIR, config)
        bundle_id = watcher.ingest(destination, task=task)

        return JSONResponse({
            "ok": True,
            "bundle_id": bundle_id,
            "status": "ingested",
            "filename": filename,
            "size": bytes_written,
        })

    except Exception as exc:
        log.exception("Validation upload failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# GET /api/validation/list
# ---------------------------------------------------------------------------

async def api_validation_list(request: Request) -> JSONResponse:
    """List all validation bundles with status."""
    try:
        status_filter = request.query_params.get("status", "all")
        validations_dir, _ = _get_paths()

        from ouroboros.tools.model_intake import _list_validations_impl
        result = _list_validations_impl(validations_dir, status_filter)

        # _list_validations_impl returns a JSON string or a message
        try:
            parsed = json.loads(result)
            return JSONResponse(parsed)
        except (json.JSONDecodeError, TypeError):
            return JSONResponse({"message": result})

    except Exception as exc:
        log.exception("Validation list failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# POST /api/validation/run
# ---------------------------------------------------------------------------

async def api_validation_run(request: Request) -> JSONResponse:
    """Trigger validation for a bundle (non-blocking — runs in background)."""
    try:
        body = await request.json()
        bundle_id = body.get("bundle_id", "")
        if not bundle_id:
            return JSONResponse({"error": "Missing bundle_id."}, status_code=400)

        validations_dir, _ = _get_paths()
        bundle_dir = validations_dir / bundle_id
        if not bundle_dir.exists():
            return JSONResponse({"error": f"Bundle not found: {bundle_id}"}, status_code=404)

        import asyncio

        # Start pipeline in background — return immediately
        asyncio.create_task(_run_pipeline_background(bundle_id, bundle_dir))

        return JSONResponse({
            "ok": True,
            "bundle_id": bundle_id,
            "status": "validating",
        })

    except Exception as exc:
        log.exception("Validation run failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
async def _run_pipeline_background(bundle_id: str, bundle_dir: Path) -> None:
    """Run the validation pipeline as a background task."""
    try:
        from ouroboros.config import REPO_DIR
        from ouroboros.validation.config_loader import load_validation_config
        from ouroboros.validation.pipeline import ValidationPipeline

        config = load_validation_config()
        pipeline = ValidationPipeline(bundle_id, bundle_dir, REPO_DIR, config)
        await pipeline.run()
        # Status updated to "completed" by pipeline._update_status()
    except Exception as exc:
        log.exception("Background validation failed for %s", bundle_id)
        # Write failed status
        try:
            status_file = bundle_dir / "status.json"
            data = {}
            if status_file.exists():
                data = json.loads(status_file.read_text(encoding="utf-8"))
            data["status"] = "failed"
            data["error"] = str(exc)
            from datetime import datetime, timezone
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GET /api/validation/report
# ---------------------------------------------------------------------------

async def api_validation_report(request: Request) -> JSONResponse | PlainTextResponse:
    """Get the validation report for a bundle."""
    try:
        bundle_id = request.query_params.get("bundle_id", "")
        fmt = request.query_params.get("format", "json")

        if not bundle_id:
            return JSONResponse({"error": "Missing bundle_id parameter."}, status_code=400)

        validations_dir, _ = _get_paths()
        bundle_dir = validations_dir / bundle_id

        if not bundle_dir.exists():
            return JSONResponse({"error": f"Bundle not found: {bundle_id}"}, status_code=404)

        if fmt == "md":
            md_path = bundle_dir / "results" / "report.md"
            if not md_path.exists():
                return JSONResponse({"error": "Report not yet generated."}, status_code=404)
            return PlainTextResponse(md_path.read_text(encoding="utf-8"))

        json_path = bundle_dir / "results" / "report.json"
        if not json_path.exists():
            return JSONResponse({"error": "Report not yet generated."}, status_code=404)

        report_data = json.loads(json_path.read_text(encoding="utf-8"))
        return JSONResponse(report_data)

    except Exception as exc:
        log.exception("Validation report failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# GET /api/validation/download
# ---------------------------------------------------------------------------

async def api_validation_download(request: Request) -> Response:
    """Download a validation bundle as ZIP (inferred, methodology, inputs, results, log)."""
    import io
    from starlette.responses import StreamingResponse

    try:
        bundle_id = request.query_params.get("bundle_id", "")
        if not bundle_id:
            return JSONResponse({"error": "Missing bundle_id parameter."}, status_code=400)

        validations_dir, _ = _get_paths()
        bundle_dir = validations_dir / bundle_id
        if not bundle_dir.exists():
            return JSONResponse({"error": f"Bundle not found: {bundle_id}"}, status_code=404)

        # Directories to include (skip raw/ — too large, and .sandbox_venv/)
        include_dirs = ["inferred", "methodology", "inputs", "results", "improvement"]

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Include selected directories
            for subdir in include_dirs:
                subdir_path = bundle_dir / subdir
                if subdir_path.exists():
                    for f in sorted(subdir_path.rglob("*")):
                        if f.is_file():
                            arcname = f"{bundle_id}/{f.relative_to(bundle_dir)}"
                            zf.write(f, arcname)
            # Include validation.log at root level
            log_file = bundle_dir / "validation.log"
            if log_file.exists():
                zf.write(log_file, f"{bundle_id}/validation.log")

        buf.seek(0)
        filename = f"validation_{bundle_id}.zip"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as exc:
        log.exception("Validation download failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def validation_api_routes() -> list[Route]:
    return [
        Route("/api/validation/upload", endpoint=api_validation_upload, methods=["POST"]),
        Route("/api/validation/list", endpoint=api_validation_list),
        Route("/api/validation/run", endpoint=api_validation_run, methods=["POST"]),
        Route("/api/validation/report", endpoint=api_validation_report),
        Route("/api/validation/download", endpoint=api_validation_download),
    ]
