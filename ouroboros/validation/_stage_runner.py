"""
Shared logic for thin stage orchestrators.

Each stage module calls run_checks_for_stage() with a stage label and name.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from ouroboros.validation.check_registry import CheckRegistry, load_check_function
from ouroboros.validation.types import (
    CheckResult,
    ModelProfile,
    ValidationConfig,
    ValidationStageResult,
)


async def run_checks_for_stage(
    stage: str,
    stage_name: str,
    bundle_dir: Path,
    model_profile: ModelProfile,
    check_registry: CheckRegistry,
    sandbox: Any,
    config: ValidationConfig,
) -> ValidationStageResult:
    """Execute all registered checks for *stage* and collect results."""
    repo_dir = check_registry._repo_dir
    checks = check_registry.get_checks_for_stage(stage, model_profile.to_dict())
    results: list[CheckResult] = []
    start = time.monotonic()

    for check_meta in checks:
        try:
            fn = load_check_function(check_meta, repo_dir)
            result = fn(bundle_dir, model_profile.to_dict(), sandbox)
        except Exception as exc:
            result = CheckResult(
                check_id=check_meta.check_id,
                check_name=check_meta.name,
                severity="warning",
                passed=False,
                score=None,
                details=f"Check crashed: {type(exc).__name__}: {exc}",
                evidence={"traceback": str(exc)},
                methodology_version="seed",
                improvement_suggestion=None,
            )
        results.append(result)

    duration = time.monotonic() - start
    any_failed = any(not r.passed for r in results)
    status = "failed" if any_failed else "passed"

    return ValidationStageResult(
        stage=stage,
        stage_name=stage_name,
        status=status,
        checks=results,
        duration_sec=round(duration, 3),
        error_message=None,
    )
