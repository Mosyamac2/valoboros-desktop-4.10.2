"""S8 check: deterministic code analysis for common ML code smells."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ouroboros.validation.types import CheckResult

CHECK_ID = "S8.CODE_SMELLS"

# Patterns to flag
_HARDCODED_PATH_RE = re.compile(r'''['"](/[a-zA-Z]|[A-Z]:\\)[\w/\\.-]+['"]''')
_NO_RANDOM_SEED = re.compile(r'random_state|random\.seed|np\.random\.seed|torch\.manual_seed|seed=')
_TRAIN_TEST_SPLIT = re.compile(r'train_test_split|\.split\(|cross_val|KFold|StratifiedKFold')


def run(bundle_dir: Path, model_profile: dict[str, Any], sandbox=None) -> CheckResult:
    code_dir = Path(bundle_dir) / "raw" / "model_code"
    if not code_dir.exists():
        return CheckResult(
            check_id=CHECK_ID, check_name="Code smells",
            severity="info", passed=True, score=None,
            details="No model code directory.", evidence={},
            methodology_version="seed", improvement_suggestion=None,
        )

    findings: list[str] = []
    evidence: dict[str, Any] = {}
    all_code = ""

    for f in sorted(code_dir.rglob("*")):
        if f.suffix == ".py":
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            all_code += content + "\n"

            # Hardcoded absolute paths
            for m in _HARDCODED_PATH_RE.finditer(content):
                path_str = m.group(0)
                findings.append(f"Hardcoded path in {f.name}: {path_str}")

        elif f.suffix == ".ipynb":
            try:
                import json
                nb = json.loads(f.read_text(encoding="utf-8"))
                for cell in nb.get("cells", []):
                    if cell.get("cell_type") == "code":
                        src = "".join(cell.get("source", []))
                        all_code += src + "\n"
                        for m in _HARDCODED_PATH_RE.finditer(src):
                            findings.append(f"Hardcoded path in {f.name}: {m.group(0)}")
            except Exception:
                continue

    if not all_code.strip():
        return CheckResult(
            check_id=CHECK_ID, check_name="Code smells",
            severity="info", passed=True, score=None,
            details="No readable code found.", evidence={},
            methodology_version="seed", improvement_suggestion=None,
        )

    # Check for missing random seed
    if not _NO_RANDOM_SEED.search(all_code):
        findings.append("No random seed set — results may not be reproducible")

    # Check for missing train/test split
    if not _TRAIN_TEST_SPLIT.search(all_code):
        findings.append("No visible train/test split or cross-validation")

    if findings:
        evidence["findings"] = findings
        return CheckResult(
            check_id=CHECK_ID, check_name="Code smells",
            severity="warning", passed=False,
            score=None,
            details=f"{len(findings)} code smell(s) found: {'; '.join(findings)}",
            evidence=evidence, methodology_version="seed",
            improvement_suggestion="; ".join(findings),
        )

    return CheckResult(
        check_id=CHECK_ID, check_name="Code smells",
        severity="pass", passed=True, score=None,
        details="No code smells detected.",
        evidence={}, methodology_version="seed", improvement_suggestion=None,
    )
