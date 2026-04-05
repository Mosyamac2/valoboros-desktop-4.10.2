"""Model intake tools: ingest_model_artifacts, list_validations, get_validation_status."""

from __future__ import annotations

import json
import logging
import uuid
import zipfile
from pathlib import Path
from typing import Any, List

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Implementation (testable without ToolContext)
# ---------------------------------------------------------------------------

def _ingest_model_artifacts_impl(
    validations_dir: Path,
    model_code_zip: str,
    task: str,
    data_zip: str = "",
    data_description: str = "",
) -> str:
    """Extract ZIPs, create directory structure, return bundle_id."""
    code_zip_path = Path(model_code_zip)
    if not code_zip_path.exists():
        raise FileNotFoundError(f"Model code ZIP not found: {model_code_zip}")
    if not zipfile.is_zipfile(code_zip_path):
        raise zipfile.BadZipFile(f"Not a valid ZIP file: {model_code_zip}")

    bundle_id = str(uuid.uuid4())[:12]
    bundle_dir = validations_dir / bundle_id

    # Create directory structure
    (bundle_dir / "raw" / "model_code").mkdir(parents=True)
    (bundle_dir / "raw" / "data_samples").mkdir(parents=True)
    (bundle_dir / "inputs").mkdir(parents=True)
    (bundle_dir / "inferred").mkdir(parents=True)
    (bundle_dir / "results").mkdir(parents=True)
    (bundle_dir / "improvement" / "implementation").mkdir(parents=True)
    (bundle_dir / "improvement" / "revalidation").mkdir(parents=True)

    # Extract model code ZIP
    with zipfile.ZipFile(code_zip_path, "r") as zf:
        zf.extractall(bundle_dir / "raw" / "model_code")

    # Extract data ZIP if provided
    if data_zip:
        data_zip_path = Path(data_zip)
        if data_zip_path.exists() and zipfile.is_zipfile(data_zip_path):
            with zipfile.ZipFile(data_zip_path, "r") as zf:
                zf.extractall(bundle_dir / "raw" / "data_samples")

    # Write inputs
    (bundle_dir / "inputs" / "task.txt").write_text(task, encoding="utf-8")
    if data_description:
        (bundle_dir / "inputs" / "data_description.txt").write_text(
            data_description, encoding="utf-8",
        )

    return bundle_id


def _list_validations_impl(validations_dir: Path, status: str = "all") -> str:
    """List all validation bundles with their status."""
    if not validations_dir.exists():
        return "No validations directory found."

    rows: list[dict[str, Any]] = []
    for entry in sorted(validations_dir.iterdir()):
        if not entry.is_dir():
            continue
        bundle_id = entry.name
        report_path = entry / "results" / "report.json"
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                bundle_status = "completed"
                verdict = report.get("overall_verdict", "?")
            except Exception:
                bundle_status = "error"
                verdict = "?"
        elif (entry / "inferred" / "model_profile.json").exists():
            bundle_status = "validating"
            verdict = "-"
        else:
            bundle_status = "pending"
            verdict = "-"

        if status != "all" and bundle_status != status:
            continue

        task_file = entry / "inputs" / "task.txt"
        task_text = ""
        if task_file.exists():
            task_text = task_file.read_text(encoding="utf-8")[:80]

        rows.append({
            "bundle_id": bundle_id,
            "status": bundle_status,
            "verdict": verdict,
            "task": task_text,
        })

    if not rows:
        return "No validations found."
    return json.dumps(rows, indent=2, ensure_ascii=False)


def _get_validation_status_impl(validations_dir: Path, bundle_id: str) -> str:
    """Return current state of a specific validation."""
    bundle_dir = validations_dir / bundle_id
    if not bundle_dir.exists():
        return f"Bundle not found: {bundle_id}"

    info: dict[str, Any] = {"bundle_id": bundle_id}

    # Check for report
    report_path = bundle_dir / "results" / "report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            info["status"] = "completed"
            info["verdict"] = report.get("overall_verdict", "?")
            info["stages_completed"] = len(report.get("stages", []))
            info["critical_findings"] = len(report.get("critical_findings", []))
        except Exception:
            info["status"] = "error"
    elif (bundle_dir / "inferred" / "model_profile.json").exists():
        info["status"] = "validating"
    else:
        info["status"] = "pending"

    # List files
    code_files = list((bundle_dir / "raw" / "model_code").rglob("*"))
    data_files = list((bundle_dir / "raw" / "data_samples").rglob("*"))
    info["code_files"] = len([f for f in code_files if f.is_file()])
    info["data_files"] = len([f for f in data_files if f.is_file()])

    return json.dumps(info, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool handlers (thin wrappers around _impl functions)
# ---------------------------------------------------------------------------

def _ingest_model_artifacts(
    ctx: ToolContext,
    model_code_zip: str,
    task: str,
    data_zip: str = "",
    data_description: str = "",
) -> str:
    validations_dir = ctx.drive_root / "validations"
    validations_dir.mkdir(parents=True, exist_ok=True)
    bundle_id = _ingest_model_artifacts_impl(
        validations_dir, model_code_zip, task, data_zip, data_description,
    )
    return f"Ingested model artifacts. Bundle ID: {bundle_id}"


def _list_validations(ctx: ToolContext, status: str = "all") -> str:
    return _list_validations_impl(ctx.drive_root / "validations", status)


def _get_validation_status(ctx: ToolContext, bundle_id: str) -> str:
    return _get_validation_status_impl(ctx.drive_root / "validations", bundle_id)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("ingest_model_artifacts", {
            "name": "ingest_model_artifacts",
            "description": (
                "Ingest an ML model bundle for validation. Extracts model code ZIP "
                "and optional data ZIP into the validation directory structure."
            ),
            "parameters": {"type": "object", "properties": {
                "model_code_zip": {
                    "type": "string",
                    "description": "Path to ZIP archive containing model source code (.py/.ipynb files).",
                },
                "task": {
                    "type": "string",
                    "description": "Description of what the ML model was trained to do.",
                },
                "data_zip": {
                    "type": "string",
                    "default": "",
                    "description": "Path to ZIP archive containing data sample files (optional).",
                },
                "data_description": {
                    "type": "string",
                    "default": "",
                    "description": "Additional description of the data (optional).",
                },
            }, "required": ["model_code_zip", "task"]},
        }, _ingest_model_artifacts),

        ToolEntry("list_validations", {
            "name": "list_validations",
            "description": "List all model validation bundles with their status and verdict.",
            "parameters": {"type": "object", "properties": {
                "status": {
                    "type": "string",
                    "default": "all",
                    "description": "Filter by status: all, pending, validating, completed, error.",
                },
            }, "required": []},
        }, _list_validations),

        ToolEntry("get_validation_status", {
            "name": "get_validation_status",
            "description": "Get detailed status of a specific model validation bundle.",
            "parameters": {"type": "object", "properties": {
                "bundle_id": {"type": "string", "description": "The bundle ID to query."},
            }, "required": ["bundle_id"]},
        }, _get_validation_status),
    ]
