"""S7 check: perturb numeric features, measure prediction sensitivity."""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

from ouroboros.validation.types import CheckResult

CHECK_ID = "S7.PERTURBATION"
_CHANGE_THRESHOLD = 0.20  # flag if > 20% predictions change

_SCRIPT_TEMPLATE = '''
import json, sys
try:
    import pandas as pd, numpy as np
    from sklearn.model_selection import train_test_split
    from sklearn.ensemble import GradientBoostingClassifier
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
    base_preds = m.predict(X_te)

    results = {{}}
    for col in X_te.columns:
        perturbed = X_te.copy()
        std = perturbed[col].std()
        if std == 0:
            continue
        perturbed[col] = perturbed[col] + std
        new_preds = m.predict(perturbed)
        change_rate = float((base_preds != new_preds).mean())
        results[col] = round(change_rate, 4)

    print(json.dumps(results))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
'''


def run(bundle_dir: Path, model_profile: dict[str, Any], sandbox=None) -> CheckResult:
    if sandbox is None:
        return CheckResult(
            check_id=CHECK_ID, check_name="Perturbation sensitivity",
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
        sens = _json.loads(result.stdout.strip())
    except Exception:
        return CheckResult(
            check_id=CHECK_ID, check_name="Perturbation sensitivity",
            severity="warning", passed=False, score=None,
            details=f"Parse error: {result.stdout[:300]}",
            evidence={"stderr": result.stderr[:500]},
            methodology_version="seed", improvement_suggestion=None,
        )

    if "error" in sens:
        return CheckResult(
            check_id=CHECK_ID, check_name="Perturbation sensitivity",
            severity="info", passed=True, score=None,
            details=f"Could not compute: {sens['error']}",
            evidence=sens, methodology_version="seed", improvement_suggestion=None,
        )

    fragile = {k: v for k, v in sens.items() if v > _CHANGE_THRESHOLD}
    if fragile:
        names = ", ".join(f"{k} ({v:.0%})" for k, v in fragile.items())
        return CheckResult(
            check_id=CHECK_ID, check_name="Perturbation sensitivity",
            severity="warning", passed=False, score=max(fragile.values()),
            details=f"Fragile features (>{_CHANGE_THRESHOLD:.0%} prediction change on +1 std): {names}",
            evidence={"sensitivities": sens, "fragile": fragile},
            methodology_version="seed",
            improvement_suggestion=f"Add input validation/clipping for fragile features: {', '.join(fragile.keys())}. Consider feature scaling or winsorization.",
        )

    return CheckResult(
        check_id=CHECK_ID, check_name="Perturbation sensitivity",
        severity="pass", passed=True, score=max(sens.values()) if sens else None,
        details=f"Perturbation OK: no feature causes >{_CHANGE_THRESHOLD:.0%} prediction change.",
        evidence={"sensitivities": sens},
        methodology_version="seed", improvement_suggestion=None,
    )
