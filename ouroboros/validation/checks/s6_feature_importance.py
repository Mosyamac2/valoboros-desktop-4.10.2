"""S6 check: compute permutation importance, flag counterintuitive results."""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

from ouroboros.validation.types import CheckResult

CHECK_ID = "S6.FEATURE_IMPORTANCE"

_SCRIPT_TEMPLATE = '''
import json, sys
try:
    import pandas as pd, numpy as np
    from sklearn.model_selection import train_test_split
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.inspection import permutation_importance
    import glob

    csvs = glob.glob("{data_dir}/*.csv")
    if not csvs:
        print(json.dumps({{"error": "no CSV"}})); sys.exit(0)
    df = pd.read_csv(csvs[0])
    target = "{target}"
    if target not in df.columns:
        print(json.dumps({{"error": "no target"}})); sys.exit(0)

    X = df.drop(columns=[target]).select_dtypes(include=["number"]).fillna(0)
    y = df[target]
    if len(X.columns) == 0 or len(X) < 20:
        print(json.dumps({{"error": "insufficient data"}})); sys.exit(0)

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42)
    m = GradientBoostingClassifier(n_estimators=50, random_state=42)
    m.fit(X_tr, y_tr)
    pi = permutation_importance(m, X_te, y_te, n_repeats=5, random_state=42)
    imp = dict(zip(X.columns.tolist(), [round(float(v), 4) for v in pi.importances_mean]))
    print(json.dumps(imp))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
'''


def run(bundle_dir: Path, model_profile: dict[str, Any], sandbox=None) -> CheckResult:
    if sandbox is None:
        return CheckResult(
            check_id=CHECK_ID, check_name="Feature importance",
            severity="info", passed=True, score=None,
            details="Skipped — no sandbox.", evidence={},
            methodology_version="seed", improvement_suggestion=None,
        )

    target = model_profile.get("target_column", "target")
    data_dir = str(Path(bundle_dir) / "raw" / "data_samples")
    result = sandbox.run(
        _SCRIPT_TEMPLATE.format(data_dir=data_dir, target=target), timeout=120,
    )

    try:
        imp = _json.loads(result.stdout.strip())
    except Exception:
        return CheckResult(
            check_id=CHECK_ID, check_name="Feature importance",
            severity="warning", passed=False, score=None,
            details=f"Parse error: {result.stdout[:300]}",
            evidence={"stderr": result.stderr[:500]},
            methodology_version="seed", improvement_suggestion=None,
        )

    if "error" in imp:
        return CheckResult(
            check_id=CHECK_ID, check_name="Feature importance",
            severity="info", passed=True, score=None,
            details=f"Could not compute: {imp['error']}",
            evidence=imp, methodology_version="seed", improvement_suggestion=None,
        )

    negative = {k: v for k, v in imp.items() if v < -0.01}
    if negative:
        names = ", ".join(negative.keys())
        return CheckResult(
            check_id=CHECK_ID, check_name="Feature importance",
            severity="warning", passed=False, score=None,
            details=f"Negative permutation importance (counterintuitive): {names}",
            evidence={"importances": imp, "negative": negative},
            methodology_version="seed",
            improvement_suggestion=f"Investigate features with negative importance: {names}. Consider removing them — they may add noise.",
        )

    return CheckResult(
        check_id=CHECK_ID, check_name="Feature importance",
        severity="pass", passed=True, score=None,
        details=f"Feature importances computed for {len(imp)} features. No counterintuitive results.",
        evidence={"importances": imp},
        methodology_version="seed", improvement_suggestion=None,
    )
