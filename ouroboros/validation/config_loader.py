"""
Load validation-specific configuration from ouroboros settings.

Reads from the merged settings (file + env) via ouroboros.config.load_settings()
and maps the OUROBOROS_VALIDATION_* keys to a ValidationConfig dataclass.
"""

from __future__ import annotations

from ouroboros.validation.types import ValidationConfig


# Map from settings key (OUROBOROS_VALIDATION_*) to ValidationConfig field name.
_KEY_MAP: dict[str, str] = {
    "OUROBOROS_VALIDATION_DIR": "validation_dir",
    "OUROBOROS_VALIDATION_TIMEOUT_SEC": "timeout_sec",
    "OUROBOROS_VALIDATION_STAGE_TIMEOUT_SEC": "stage_timeout_sec",
    "OUROBOROS_VALIDATION_SANDBOX_MEM_MB": "sandbox_mem_mb",
    "OUROBOROS_VALIDATION_SANDBOX_CPU_SEC": "sandbox_cpu_sec",
    "OUROBOROS_VALIDATION_COMPREHENSION_MODEL": "comprehension_model",
    "OUROBOROS_VALIDATION_COMPREHENSION_EFFORT": "comprehension_effort",
    "OUROBOROS_VALIDATION_SYNTHESIS_MODEL": "synthesis_model",
    "OUROBOROS_VALIDATION_IMPROVEMENT_MODEL": "improvement_model",
    "OUROBOROS_VALIDATION_MATURITY_THRESHOLD": "maturity_threshold",
    "OUROBOROS_VALIDATION_EVO_MIN_BUNDLES_EARLY": "evo_min_bundles_early",
    "OUROBOROS_VALIDATION_EVO_MIN_BUNDLES_MATURE": "evo_min_bundles_mature",
    "OUROBOROS_VALIDATION_AUTO_EVOLVE": "auto_evolve",
    "OUROBOROS_VALIDATION_AUTO_IMPROVE": "auto_improve",
    "OUROBOROS_VALIDATION_AUTO_SELF_ASSESS": "auto_self_assess",
    "OUROBOROS_VALIDATION_REPORT_MODEL": "report_model",
    "OUROBOROS_VALIDATION_METHODOLOGY_VERSION": "methodology_version",
    "OUROBOROS_VALIDATION_IMPROVEMENT_LIFT_THRESHOLD": "improvement_lift_threshold",
    "OUROBOROS_VALIDATION_MAX_HARD_RECOMMENDATIONS": "max_hard_recommendations",
    "OUROBOROS_VALIDATION_MAX_SOFT_RECOMMENDATIONS": "max_soft_recommendations",
    "OUROBOROS_VALIDATION_INBOX_DIR": "inbox_dir",
    "OUROBOROS_VALIDATION_AUTO_INGEST": "auto_ingest",
    "OUROBOROS_VALIDATION_PRE_RESEARCH": "pre_research",
    "OUROBOROS_VALIDATION_RESEARCH_MAX_QUERIES": "research_max_queries",
    "OUROBOROS_VALIDATION_RESEARCH_MAX_PAPERS": "research_max_papers",
}


def load_validation_config() -> ValidationConfig:
    """Load settings and return a populated ValidationConfig."""
    from ouroboros.config import load_settings

    settings = load_settings()
    kwargs: dict = {}
    for settings_key, field_name in _KEY_MAP.items():
        if settings_key in settings:
            kwargs[field_name] = settings[settings_key]
    return ValidationConfig(**kwargs)
