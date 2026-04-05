"""
Valoboros — folder watcher for automatic model ingestion.

Monitors an inbox directory for new .zip files and tracks which ones
have been processed.  Designed to be called periodically from the
background consciousness loop (no separate daemon process).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ouroboros.validation.types import ValidationConfig

log = logging.getLogger(__name__)

def _get_data_dir() -> Path:
    """Get DATA_DIR from config, with fallback."""
    try:
        from ouroboros.config import DATA_DIR
        return DATA_DIR
    except Exception:
        return Path.home() / "Ouroboros" / "data"


def resolve_inbox_dir(config: ValidationConfig) -> Path:
    """Resolve inbox_dir relative to DATA_DIR if not absolute.

    If inbox_dir is a relative path (the default "ml-models-to-validate"),
    it is resolved against OUROBOROS_DATA_DIR so it lives inside the
    persistent data directory (important for Docker volumes).
    """
    inbox = Path(config.inbox_dir)
    if not inbox.is_absolute():
        inbox = _get_data_dir() / config.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


class ValidationWatcher:
    """Watches an inbox folder for new model ZIPs and triggers ingestion."""

    def __init__(
        self,
        inbox_dir: Path,
        validations_dir: Path,
        repo_dir: Path,
        config: ValidationConfig,
    ) -> None:
        self._inbox_dir = Path(inbox_dir)
        self._validations_dir = Path(validations_dir)
        self._repo_dir = Path(repo_dir)
        self._config = config
        self._processed_file = self._inbox_dir / ".valoboros_processed.json"

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan_inbox(self) -> list[Path]:
        """Return list of new .zip files not yet in the processed tracking file."""
        if not self._inbox_dir.exists():
            return []

        processed = self._load_processed()
        new_zips: list[Path] = []
        for f in sorted(self._inbox_dir.iterdir()):
            if f.is_file() and f.suffix.lower() == ".zip" and f.name not in processed:
                new_zips.append(f)
        return new_zips

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest(self, zip_path: Path, task: str = "") -> str:
        """Ingest a ZIP file into the validations directory.

        Returns the bundle_id.  Does NOT run the pipeline — that should
        be triggered separately (e.g. via schedule_task).
        """
        from ouroboros.tools.model_intake import _ingest_model_artifacts_impl

        if not task:
            # Use the filename (without extension) as a default task description
            task = f"Validate model: {zip_path.stem}"

        self._validations_dir.mkdir(parents=True, exist_ok=True)

        bundle_id = _ingest_model_artifacts_impl(
            validations_dir=self._validations_dir,
            model_code_zip=str(zip_path),
            task=task,
        )

        self.mark_processed(zip_path.name, bundle_id, "ingested")
        log.info("Ingested %s → bundle %s", zip_path.name, bundle_id)
        return bundle_id

    # ------------------------------------------------------------------
    # Processed tracking
    # ------------------------------------------------------------------

    def mark_processed(self, zip_name: str, bundle_id: str, status: str) -> None:
        """Record that a ZIP has been ingested/validated."""
        processed = self._load_processed()
        processed[zip_name] = {
            "bundle_id": bundle_id,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._save_processed(processed)

    def _load_processed(self) -> dict[str, Any]:
        """Read the processed tracking file."""
        if not self._processed_file.exists():
            return {}
        try:
            return json.loads(self._processed_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to read processed file: %s", exc)
            return {}

    def _save_processed(self, data: dict[str, Any]) -> None:
        """Write the processed tracking file."""
        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        self._processed_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
