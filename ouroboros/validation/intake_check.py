"""S0 stage orchestrator: artifact comprehension + S0 checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ouroboros.validation._stage_runner import run_checks_for_stage
from ouroboros.validation.check_registry import CheckRegistry
from ouroboros.validation.types import ModelProfile, ValidationConfig, ValidationStageResult


async def run_stage(
    bundle_dir: Path,
    model_profile: ModelProfile,
    check_registry: CheckRegistry,
    sandbox: Any,
    config: ValidationConfig,
) -> ValidationStageResult:
    """Run S0 checks (code parseable, data loadable, etc.)."""
    return await run_checks_for_stage(
        "S0", "Intake & Sanity", bundle_dir, model_profile,
        check_registry, sandbox, config,
    )
