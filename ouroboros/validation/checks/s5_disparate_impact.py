"""S5 check: compute disparate impact ratio for protected attributes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ouroboros.validation.types import CheckResult

CHECK_ID = "S5.DISPARATE_IMPACT"
_DI_LOW = 0.8
_DI_HIGH = 1.25


def run(bundle_dir: Path, model_profile: dict[str, Any], sandbox=None) -> CheckResult:
    protected = model_profile.get("protected_attributes_candidates", [])
    target = model_profile.get("target_column")
    if not protected or not target:
        return CheckResult(
            check_id=CHECK_ID, check_name="Disparate impact",
            severity="info", passed=True, score=None,
            details="No protected attributes or target column — skipped.",
            evidence={}, methodology_version="seed", improvement_suggestion=None,
        )

    data_dir = Path(bundle_dir) / "raw" / "data_samples"
    csvs = sorted(data_dir.glob("*.csv")) if data_dir.exists() else []
    if not csvs:
        return CheckResult(
            check_id=CHECK_ID, check_name="Disparate impact",
            severity="info", passed=True, score=None,
            details="No CSV data — skipped.", evidence={},
            methodology_version="seed", improvement_suggestion=None,
        )

    try:
        import pandas as pd
    except ImportError:
        return CheckResult(
            check_id=CHECK_ID, check_name="Disparate impact",
            severity="warning", passed=False, score=None,
            details="pandas not installed.", evidence={},
            methodology_version="seed", improvement_suggestion=None,
        )

    df = pd.read_csv(csvs[0])
    findings: list[dict] = []

    for attr in protected:
        if attr not in df.columns or target not in df.columns:
            continue
        groups = df.groupby(attr)[target].mean()
        if len(groups) < 2:
            continue
        max_rate = groups.max()
        if max_rate == 0:
            continue
        for group_val, rate in groups.items():
            di = rate / max_rate
            if di < _DI_LOW or di > _DI_HIGH:
                findings.append({
                    "attribute": attr, "group": str(group_val),
                    "positive_rate": round(float(rate), 4),
                    "disparate_impact": round(float(di), 4),
                })

    if findings:
        summary = "; ".join(
            f"{f['attribute']}={f['group']} DI={f['disparate_impact']}"
            for f in findings
        )
        return CheckResult(
            check_id=CHECK_ID, check_name="Disparate impact",
            severity="warning", passed=False, score=None,
            details=f"Disparate impact detected: {summary}",
            evidence={"findings": findings},
            methodology_version="seed",
            improvement_suggestion="Consider rebalancing training data or applying fairness constraints during model training.",
        )

    return CheckResult(
        check_id=CHECK_ID, check_name="Disparate impact",
        severity="pass", passed=True, score=None,
        details="No disparate impact issues detected.",
        evidence={}, methodology_version="seed", improvement_suggestion=None,
    )
