"""
Ouroboros validation platform — dynamic check registry.

Manages the collection of validation checks as evolvable artifacts.
Checks are individual .py files in validation/checks/, registered in
check_manifest.json.  The agent can create, edit, disable, and delete
checks as part of its methodology evolution.
"""

from __future__ import annotations

import importlib.util
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ValidationCheck dataclass
# ---------------------------------------------------------------------------

@dataclass
class ValidationCheck:
    check_id: str           # e.g., "S2.OOS.AUC"
    stage: str              # "S0" .. "S9"
    name: str               # human-readable
    description: str        # what it checks and why
    check_type: str         # "deterministic" | "llm_assisted" | "sandbox"
    enabled: bool
    created_by: str         # "system" | "evolution_<commit_hash>"
    created_at: str         # ISO-8601
    version: int
    tags: list[str]         # e.g., ["tabular", "classification"]
    implementation_path: str  # relative to checks dir, e.g. "checks/s2_oos.py"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ValidationCheck:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# CheckRegistry
# ---------------------------------------------------------------------------

class CheckRegistry:
    """Manages the dynamic collection of validation checks."""

    def __init__(self, repo_dir: Path) -> None:
        self._repo_dir = Path(repo_dir).resolve()

    @property
    def _checks_dir(self) -> Path:
        return self._repo_dir / "ouroboros" / "validation" / "checks"

    @property
    def _manifest_path(self) -> Path:
        return self._checks_dir / "check_manifest.json"

    # ------------------------------------------------------------------
    # Manifest I/O
    # ------------------------------------------------------------------

    def load_manifest(self) -> list[ValidationCheck]:
        """Read check_manifest.json from disk."""
        if not self._manifest_path.exists():
            return []
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            return [ValidationCheck.from_dict(entry) for entry in data]
        except Exception as exc:
            log.warning("Failed to load check manifest: %s", exc)
            return []

    def save_manifest(self, checks: list[ValidationCheck]) -> None:
        """Write check_manifest.json to disk."""
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        data = [c.to_dict() for c in checks]
        self._manifest_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_checks(
        self,
        stage: Optional[str] = None,
        enabled_only: bool = True,
    ) -> list[ValidationCheck]:
        """Return checks, optionally filtered by stage and enabled status."""
        checks = self.load_manifest()
        if enabled_only:
            checks = [c for c in checks if c.enabled]
        if stage is not None:
            checks = [c for c in checks if c.stage == stage]
        return checks

    def get_check(self, check_id: str) -> ValidationCheck:
        """Return a single check by ID, or raise KeyError."""
        for c in self.load_manifest():
            if c.check_id == check_id:
                return c
        raise KeyError(f"Check not found: {check_id}")

    def add_check(self, check: ValidationCheck) -> str:
        """Register a check in the manifest.  Returns check_id."""
        checks = self.load_manifest()
        # Reject duplicates
        if any(c.check_id == check.check_id for c in checks):
            raise ValueError(f"Check already exists: {check.check_id}")
        checks.append(check)
        self.save_manifest(checks)
        return check.check_id

    def update_check(self, check_id: str, new_implementation: str, reason: str) -> str:
        """Replace a check's implementation file content, bump version."""
        checks = self.load_manifest()
        for c in checks:
            if c.check_id == check_id:
                # Write new implementation to the file
                impl_path = self._checks_dir / c.implementation_path
                impl_path.parent.mkdir(parents=True, exist_ok=True)
                impl_path.write_text(new_implementation, encoding="utf-8")
                c.version += 1
                self.save_manifest(checks)
                return f"Updated {check_id} to v{c.version}: {reason}"
        raise KeyError(f"Check not found: {check_id}")

    def disable_check(self, check_id: str, reason: str) -> str:
        """Disable a check (keeps it in manifest for audit trail)."""
        checks = self.load_manifest()
        for c in checks:
            if c.check_id == check_id:
                c.enabled = False
                self.save_manifest(checks)
                return f"Disabled {check_id}: {reason}"
        raise KeyError(f"Check not found: {check_id}")

    def delete_check(self, check_id: str, reason: str) -> str:
        """Remove a check from the manifest entirely."""
        checks = self.load_manifest()
        original_len = len(checks)
        checks = [c for c in checks if c.check_id != check_id]
        if len(checks) == original_len:
            raise KeyError(f"Check not found: {check_id}")
        self.save_manifest(checks)
        return f"Deleted {check_id}: {reason}"

    # ------------------------------------------------------------------
    # Stage + tag filtering
    # ------------------------------------------------------------------

    def get_checks_for_stage(
        self,
        stage: str,
        model_profile: dict[str, Any],
    ) -> list[ValidationCheck]:
        """Return enabled checks for *stage* that match the model profile.

        Tag matching logic:
        - A check with NO tags applies to all models (universal).
        - A check WITH tags matches if ALL of its tags appear somewhere
          in the profile's values (model_type, framework, data_format, or
          any string value in the profile dict).
        """
        checks = self.list_checks(stage=stage, enabled_only=True)
        profile_values = _extract_profile_values(model_profile)
        result = []
        for c in checks:
            if not c.tags:
                # Universal check — applies to everything
                result.append(c)
            elif all(tag in profile_values for tag in c.tags):
                result.append(c)
        return result


# ---------------------------------------------------------------------------
# Dynamic check loader
# ---------------------------------------------------------------------------

def load_check_function(check: ValidationCheck, repo_dir: Path) -> Callable:
    """Dynamically import and return the run() function from a check's .py file.

    *implementation_path* is relative to ``ouroboros/validation/``
    (e.g. ``checks/s2_oos.py``).
    """
    validation_dir = repo_dir / "ouroboros" / "validation"
    file_path = validation_dir / check.implementation_path
    if not file_path.exists():
        raise FileNotFoundError(f"Check file not found: {file_path}")

    module_name = f"_sandbox_check_{check.check_id.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fn = getattr(module, "run", None)
    if fn is None:
        raise AttributeError(f"Check {check.check_id} has no run() function in {file_path}")
    return fn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_profile_values(profile: dict[str, Any]) -> set[str]:
    """Collect all string values from a model profile for tag matching."""
    values: set[str] = set()
    for v in profile.values():
        if isinstance(v, str):
            values.add(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    values.add(item)
    return values
