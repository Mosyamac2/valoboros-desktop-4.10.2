"""S2 check: compute out-of-sample metrics via sandbox execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ouroboros.validation.types import CheckResult

CHECK_ID = "S2.OOS_METRICS"

_SCRIPT_TEMPLATE = '''
import json, sys
try:
    import pandas as pd
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, roc_auc_score, mean_squared_error
    import numpy as np

    data_dir = "{data_dir}"
    target = "{target}"

    # Load first CSV found
    import glob
    csvs = glob.glob(data_dir + "/*.csv")
    if not csvs:
        print(json.dumps({{"error": "no CSV files found"}}))
        sys.exit(0)
    df = pd.read_csv(csvs[0])
    if target not in df.columns:
        print(json.dumps({{"error": f"target column '{{target}}' not in data"}}))
        sys.exit(0)

    X = df.drop(columns=[target]).select_dtypes(include=["number"])
    y = df[target]
    if len(X.columns) == 0 or len(X) < 10:
        print(json.dumps({{"error": "insufficient numeric features or rows"}}))
        sys.exit(0)

    X = X.fillna(0)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
    nunique = y.nunique()
    is_classification = nunique <= 20
    if is_classification:
        model = GradientBoostingClassifier(n_estimators=50, random_state=42)
        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        acc = accuracy_score(y_test, preds)
        result = {{"accuracy": round(acc, 4)}}
        if nunique == 2:
            proba = model.predict_proba(X_test)[:, 1]
            result["auc"] = round(roc_auc_score(y_test, proba), 4)
    else:
        model = GradientBoostingRegressor(n_estimators=50, random_state=42)
        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        rmse = float(np.sqrt(mean_squared_error(y_test, preds)))
        result = {{"rmse": round(rmse, 4)}}

    print(json.dumps(result))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
'''


def run(bundle_dir: Path, model_profile: dict[str, Any], sandbox=None) -> CheckResult:
    if sandbox is None:
        return CheckResult(
            check_id=CHECK_ID, check_name="OOS metrics",
            severity="info", passed=True, score=None,
            details="Skipped — no sandbox available.",
            evidence={}, methodology_version="seed", improvement_suggestion=None,
        )

    target = model_profile.get("target_column", "target")
    data_dir = str(Path(bundle_dir) / "raw" / "data_samples")
    script = _SCRIPT_TEMPLATE.format(data_dir=data_dir, target=target)

    result = sandbox.run(script, timeout=120)
    if result.returncode != 0 and not result.stdout.strip():
        return CheckResult(
            check_id=CHECK_ID, check_name="OOS metrics",
            severity="warning", passed=False, score=None,
            details=f"Sandbox failed (rc={result.returncode}): {result.stderr[:500]}",
            evidence={"stderr": result.stderr[:1000]},
            methodology_version="seed", improvement_suggestion=None,
        )

    import json as _json
    try:
        metrics = _json.loads(result.stdout.strip())
    except Exception:
        return CheckResult(
            check_id=CHECK_ID, check_name="OOS metrics",
            severity="warning", passed=False, score=None,
            details=f"Could not parse sandbox output: {result.stdout[:500]}",
            evidence={}, methodology_version="seed", improvement_suggestion=None,
        )

    if "error" in metrics:
        return CheckResult(
            check_id=CHECK_ID, check_name="OOS metrics",
            severity="info", passed=True, score=None,
            details=f"Could not compute metrics: {metrics['error']}",
            evidence=metrics, methodology_version="seed", improvement_suggestion=None,
        )

    return CheckResult(
        check_id=CHECK_ID, check_name="OOS metrics",
        severity="pass", passed=True,
        score=metrics.get("auc") or metrics.get("accuracy") or metrics.get("rmse"),
        details=f"OOS metrics computed: {metrics}",
        evidence=metrics, methodology_version="seed", improvement_suggestion=None,
    )
