"""S0 check: verify all .py files parse and .ipynb files load."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from ouroboros.validation.types import CheckResult

CHECK_ID = "S0.CODE_PARSEABLE"


def run(bundle_dir: Path, model_profile: dict[str, Any], sandbox=None) -> CheckResult:
    code_dir = Path(bundle_dir) / "raw" / "model_code"
    if not code_dir.exists():
        return _fail("No raw/model_code/ directory found.")

    errors: list[str] = []
    total = 0

    for f in sorted(code_dir.rglob("*")):
        if f.suffix == ".py":
            total += 1
            try:
                ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(f.name))
            except SyntaxError as exc:
                errors.append(f"{f.name}: SyntaxError at line {exc.lineno}: {exc.msg}")
        elif f.suffix == ".ipynb":
            total += 1
            try:
                import nbformat
                nbformat.read(str(f), as_version=4)
            except ImportError:
                errors.append(f"{f.name}: nbformat not installed, cannot validate notebook")
            except Exception as exc:
                errors.append(f"{f.name}: parse error: {exc}")

    if total == 0:
        return _fail("No .py or .ipynb files found in raw/model_code/.")

    if errors:
        return CheckResult(
            check_id=CHECK_ID, check_name="Code parseable",
            severity="critical", passed=False, score=None,
            details=f"{len(errors)} parse error(s): " + "; ".join(errors),
            evidence={"errors": errors, "total_files": total},
            methodology_version="seed", improvement_suggestion=None,
        )

    return CheckResult(
        check_id=CHECK_ID, check_name="Code parseable",
        severity="pass", passed=True, score=None,
        details=f"All {total} code file(s) parsed successfully.",
        evidence={"total_files": total},
        methodology_version="seed", improvement_suggestion=None,
    )


def _fail(msg: str) -> CheckResult:
    return CheckResult(
        check_id=CHECK_ID, check_name="Code parseable",
        severity="critical", passed=False, score=None,
        details=msg, evidence={},
        methodology_version="seed", improvement_suggestion=None,
    )
