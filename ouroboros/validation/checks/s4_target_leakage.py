"""S4 check: detect target leakage via high feature-target correlations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ouroboros.validation.types import CheckResult

CHECK_ID = "S4.TARGET_LEAKAGE"
_CORR_THRESHOLD = 0.95


def run(bundle_dir: Path, model_profile: dict[str, Any], sandbox=None) -> CheckResult:
    target = model_profile.get("target_column")
    if not target:
        return CheckResult(
            check_id=CHECK_ID, check_name="Target leakage",
            severity="info", passed=True, score=None,
            details="No target_column specified — skipped.",
            evidence={}, methodology_version="seed", improvement_suggestion=None,
        )

    data_dir = Path(bundle_dir) / "raw" / "data_samples"
    if not data_dir.exists():
        return CheckResult(
            check_id=CHECK_ID, check_name="Target leakage",
            severity="info", passed=True, score=None,
            details="No data_samples — skipped.",
            evidence={}, methodology_version="seed", improvement_suggestion=None,
        )

    try:
        import pandas as pd
    except ImportError:
        return CheckResult(
            check_id=CHECK_ID, check_name="Target leakage",
            severity="warning", passed=False, score=None,
            details="pandas not installed.", evidence={},
            methodology_version="seed", improvement_suggestion=None,
        )

    # Load first CSV
    csvs = sorted(data_dir.glob("*.csv"))
    if not csvs:
        return CheckResult(
            check_id=CHECK_ID, check_name="Target leakage",
            severity="info", passed=True, score=None,
            details="No CSV files in data_samples.", evidence={},
            methodology_version="seed", improvement_suggestion=None,
        )

    try:
        df = pd.read_csv(csvs[0])
    except Exception as exc:
        return CheckResult(
            check_id=CHECK_ID, check_name="Target leakage",
            severity="warning", passed=False, score=None,
            details=f"Failed to load {csvs[0].name}: {exc}", evidence={},
            methodology_version="seed", improvement_suggestion=None,
        )

    if target not in df.columns:
        return CheckResult(
            check_id=CHECK_ID, check_name="Target leakage",
            severity="info", passed=True, score=None,
            details=f"Target column '{target}' not found in {csvs[0].name}.",
            evidence={}, methodology_version="seed", improvement_suggestion=None,
        )

    # Compute correlations of numeric columns with target
    numeric = df.select_dtypes(include=["number"])
    if target not in numeric.columns:
        return CheckResult(
            check_id=CHECK_ID, check_name="Target leakage",
            severity="info", passed=True, score=None,
            details="Target column is not numeric — correlation check skipped.",
            evidence={}, methodology_version="seed", improvement_suggestion=None,
        )

    corr = numeric.corr()[target].drop(target, errors="ignore").abs()
    leaked = corr[corr > _CORR_THRESHOLD]

    if leaked.empty:
        return CheckResult(
            check_id=CHECK_ID, check_name="Target leakage",
            severity="pass", passed=True,
            score=float(corr.max()) if len(corr) > 0 else None,
            details=f"No features with >{_CORR_THRESHOLD} correlation to target.",
            evidence={"max_correlation": float(corr.max()) if len(corr) > 0 else 0},
            methodology_version="seed", improvement_suggestion=None,
        )

    leaked_list = [
        {"feature": feat, "correlation": round(float(val), 4)}
        for feat, val in leaked.items()
    ]
    names = ", ".join(d["feature"] for d in leaked_list)
    return CheckResult(
        check_id=CHECK_ID, check_name="Target leakage",
        severity="critical", passed=False,
        score=float(leaked.max()),
        details=f"Potential target leakage: feature(s) with >{_CORR_THRESHOLD} correlation to target: {names}",
        evidence={"leaked_features": leaked_list},
        methodology_version="seed",
        improvement_suggestion=f"Remove or investigate leaked feature(s): {names}. These have >{_CORR_THRESHOLD} correlation with the target and likely leak future information.",
    )
