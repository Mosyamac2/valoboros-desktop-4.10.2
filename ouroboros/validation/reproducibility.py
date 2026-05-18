"""S1 stage orchestrator: reproducibility check.

Runs the model code in the sandbox twice and checks that output is deterministic.
This is a special stage — not check-based like others.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ouroboros.validation.types import (
    CheckResult,
    ModelProfile,
    ValidationConfig,
    ValidationStageResult,
)


async def run_stage(
    bundle_dir: Path,
    model_profile: ModelProfile,
    check_registry: Any,
    sandbox: Any,
    config: ValidationConfig,
) -> ValidationStageResult:
    """Run model code twice in sandbox and compare outputs."""
    start = time.monotonic()

    if sandbox is None:
        return ValidationStageResult(
            stage="S1", stage_name="Reproducibility", status="skipped",
            checks=[], duration_sec=0.0,
            error_message="No sandbox available — skipped.",
        )

    # Find the main code file
    code_dir = Path(bundle_dir) / "raw" / "model_code"
    candidates = sorted(code_dir.glob("*.py")) + sorted(code_dir.glob("*.ipynb"))
    if not candidates:
        return ValidationStageResult(
            stage="S1", stage_name="Reproducibility", status="skipped",
            checks=[], duration_sec=0.0,
            error_message="No code files found to execute.",
        )

    main_file = candidates[0]
    if main_file.suffix == ".ipynb":
        r1 = sandbox.run_notebook(str(main_file.relative_to(bundle_dir)), timeout=config.stage_timeout_sec)
        r2 = sandbox.run_notebook(str(main_file.relative_to(bundle_dir)), timeout=config.stage_timeout_sec)
    else:
        script = main_file.read_text(encoding="utf-8", errors="replace")
        r1 = sandbox.run(script, timeout=config.stage_timeout_sec)
        r2 = sandbox.run(script, timeout=config.stage_timeout_sec)

    duration = time.monotonic() - start
    checks: list[CheckResult] = []

    # Check: did it run?
    if r1.returncode != 0:
        checks.append(CheckResult(
            check_id="S1.EXECUTION", check_name="Code executes",
            severity="critical", passed=False, score=None,
            details=f"Code failed to execute (rc={r1.returncode}): {r1.stderr[:500]}",
            evidence={"returncode": r1.returncode, "stderr": r1.stderr[:1000]},
            methodology_version="seed", improvement_suggestion=None,
        ))
    else:
        checks.append(CheckResult(
            check_id="S1.EXECUTION", check_name="Code executes",
            severity="pass", passed=True, score=None,
            details="Code executed successfully.",
            evidence={"returncode": 0},
            methodology_version="seed", improvement_suggestion=None,
        ))

        # Check: deterministic?
        deterministic = r1.stdout.strip() == r2.stdout.strip()
        checks.append(CheckResult(
            check_id="S1.DETERMINISM", check_name="Deterministic output",
            severity="warning" if not deterministic else "pass",
            passed=deterministic, score=None,
            details="Output is deterministic across 2 runs." if deterministic
                    else "Output differs between runs — model may not be reproducible.",
            evidence={"run1_len": len(r1.stdout), "run2_len": len(r2.stdout)},
            methodology_version="seed",
            improvement_suggestion=None if deterministic
                else "Set random seeds (random_state, np.random.seed, torch.manual_seed) for reproducibility.",
        ))

    any_failed = any(not c.passed for c in checks)
    return ValidationStageResult(
        stage="S1", stage_name="Reproducibility",
        status="failed" if any_failed else "passed",
        checks=checks, duration_sec=round(duration, 3),
        error_message=None,
    )
