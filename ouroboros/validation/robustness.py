"""S7 stage orchestrator: robustness / perturbation analysis."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from ouroboros.validation._stage_runner import run_checks_for_stage
from ouroboros.validation.check_registry import CheckRegistry
from ouroboros.validation.types import ModelProfile, ValidationConfig, ValidationStageResult

async def run_stage(bundle_dir: Path, model_profile: ModelProfile,
                    check_registry: CheckRegistry, sandbox: Any,
                    config: ValidationConfig) -> ValidationStageResult:
    return await run_checks_for_stage(
        "S7", "Robustness", bundle_dir, model_profile,
        check_registry, sandbox, config,
    )
