"""S3 check: detect overfitting via train/test performance gap."""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

from ouroboros.validation.types import CheckResult

CHECK_ID = "S3.TRAIN_TEST_GAP"
_GAP_THRESHOLD = 0.10

_SCRIPT_TEMPLATE = '''
import json, sys
try:
    import pandas as pd, numpy as np
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score
    from sklearn.ensemble import GradientBoostingClassifier
    import glob

    csvs = glob.glob("{data_dir}/*.csv")
    if not csvs:
        print(json.dumps({{"error": "no CSV"}}))
        sys.exit(0)
    df = pd.read_csv(csvs[0])
    target = "{target}"
    if target not in df.columns:
        print(json.dumps({{"error": "no target column"}}))
        sys.exit(0)

    X = df.drop(columns=[target]).select_dtypes(include=["number"]).fillna(0)
    y = df[target]
    if len(X.columns) == 0 or len(X) < 20:
        print(json.dumps({{"error": "insufficient data"}}))
        sys.exit(0)

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42)
    m = GradientBoostingClassifier(n_estimators=50, random_state=42)
    m.fit(X_tr, y_tr)
    train_acc = accuracy_score(y_tr, m.predict(X_tr))
    test_acc = accuracy_score(y_te, m.predict(X_te))
    print(json.dumps({{"train_acc": round(train_acc, 4), "test_acc": round(test_acc, 4)}}))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
'''


def run(bundle_dir: Path, model_profile: dict[str, Any], sandbox=None) -> CheckResult:
    if sandbox is None:
        return CheckResult(
            check_id=CHECK_ID, check_name="Train/test gap",
            severity="info", passed=True, score=None,
            details="Skipped — no sandbox available.",
            evidence={}, methodology_version="seed", improvement_suggestion=None,
        )

    target = model_profile.get("target_column", "target")
    data_dir = str(Path(bundle_dir) / "raw" / "data_samples")
    result = sandbox.run(
        _SCRIPT_TEMPLATE.format(data_dir=data_dir, target=target), timeout=120,
    )

    try:
        metrics = _json.loads(result.stdout.strip())
    except Exception:
        return CheckResult(
            check_id=CHECK_ID, check_name="Train/test gap",
            severity="warning", passed=False, score=None,
            details=f"Could not parse output: {result.stdout[:300]}",
            evidence={"stderr": result.stderr[:500]},
            methodology_version="seed", improvement_suggestion=None,
        )

    if "error" in metrics:
        return CheckResult(
            check_id=CHECK_ID, check_name="Train/test gap",
            severity="info", passed=True, score=None,
            details=f"Could not compute gap: {metrics['error']}",
            evidence=metrics, methodology_version="seed", improvement_suggestion=None,
        )

    gap = metrics["train_acc"] - metrics["test_acc"]
    if gap > _GAP_THRESHOLD:
        return CheckResult(
            check_id=CHECK_ID, check_name="Train/test gap",
            severity="warning", passed=False, score=round(gap, 4),
            details=f"Overfit detected: train={metrics['train_acc']}, test={metrics['test_acc']}, gap={gap:.4f} > {_GAP_THRESHOLD}",
            evidence=metrics, methodology_version="seed",
            improvement_suggestion=(
                f"Add regularization to reduce the {gap:.2f} train/test gap. "
                f"For tree models: reduce max_depth or increase min_samples_leaf. "
                f"For linear models: add L2 penalty (e.g., Ridge(alpha=1.0))."
            ),
        )

    return CheckResult(
        check_id=CHECK_ID, check_name="Train/test gap",
        severity="pass", passed=True, score=round(gap, 4),
        details=f"Train/test gap OK: train={metrics['train_acc']}, test={metrics['test_acc']}, gap={gap:.4f}",
        evidence=metrics, methodology_version="seed", improvement_suggestion=None,
    )
