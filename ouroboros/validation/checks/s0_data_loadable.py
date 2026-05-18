"""S0 check: verify data sample files can be loaded by pandas."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ouroboros.validation.types import CheckResult

CHECK_ID = "S0.DATA_LOADABLE"

_LOADERS = {
    ".csv": "read_csv",
    ".tsv": "read_csv",
    ".parquet": "read_parquet",
    ".xlsx": "read_excel",
    ".xls": "read_excel",
    ".json": "read_json",
    ".jsonl": "read_json",
}


def run(bundle_dir: Path, model_profile: dict[str, Any], sandbox=None) -> CheckResult:
    data_dir = Path(bundle_dir) / "raw" / "data_samples"
    if not data_dir.exists():
        return CheckResult(
            check_id=CHECK_ID, check_name="Data loadable",
            severity="info", passed=True, score=None,
            details="No raw/data_samples/ directory — skipped.",
            evidence={}, methodology_version="seed", improvement_suggestion=None,
        )

    try:
        import pandas as pd
    except ImportError:
        return CheckResult(
            check_id=CHECK_ID, check_name="Data loadable",
            severity="warning", passed=False, score=None,
            details="pandas not installed — cannot validate data files.",
            evidence={}, methodology_version="seed", improvement_suggestion=None,
        )

    loaded: list[dict] = []
    errors: list[str] = []

    for f in sorted(data_dir.rglob("*")):
        if f.is_dir():
            continue
        suffix = f.suffix.lower()
        loader_name = _LOADERS.get(suffix)
        if loader_name is None:
            continue
        try:
            kwargs = {}
            if suffix in (".tsv",):
                kwargs["sep"] = "\t"
            if suffix == ".jsonl":
                kwargs["lines"] = True
            loader = getattr(pd, loader_name)
            df = loader(str(f), **kwargs)
            loaded.append({
                "file": f.name,
                "rows": len(df),
                "columns": len(df.columns),
                "column_names": list(df.columns[:20]),
            })
        except Exception as exc:
            errors.append(f"{f.name}: {type(exc).__name__}: {exc}")

    if not loaded and not errors:
        return CheckResult(
            check_id=CHECK_ID, check_name="Data loadable",
            severity="info", passed=True, score=None,
            details="No recognizable data files found in raw/data_samples/.",
            evidence={}, methodology_version="seed", improvement_suggestion=None,
        )

    total_rows = sum(d["rows"] for d in loaded)
    summary_parts = [f"{d['file']}: {d['rows']} rows, {d['columns']} cols" for d in loaded]
    summary = "; ".join(summary_parts)

    if errors:
        return CheckResult(
            check_id=CHECK_ID, check_name="Data loadable",
            severity="warning", passed=False, score=None,
            details=f"Loaded {len(loaded)} file(s) ({total_rows} total rows), {len(errors)} error(s): {'; '.join(errors)}",
            evidence={"loaded": loaded, "errors": errors},
            methodology_version="seed", improvement_suggestion=None,
        )

    return CheckResult(
        check_id=CHECK_ID, check_name="Data loadable",
        severity="pass", passed=True, score=float(total_rows),
        details=f"All {len(loaded)} data file(s) loaded: {summary}",
        evidence={"loaded": loaded},
        methodology_version="seed", improvement_suggestion=None,
    )
