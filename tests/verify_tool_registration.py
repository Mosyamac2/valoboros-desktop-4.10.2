"""Verify that all validation tools are properly registered."""
from ouroboros.tool_capabilities import CORE_TOOL_NAMES, READ_ONLY_PARALLEL_TOOLS

new_core = [
    "ingest_model_artifacts", "list_validations", "get_validation_status",
    "run_validation", "run_validation_stage", "get_validation_report",
    "get_model_profile", "list_validation_checks", "create_validation_check",
    "edit_validation_check", "disable_validation_check", "delete_validation_check",
    "run_improvement_cycle",
]
for t in new_core:
    assert t in CORE_TOOL_NAMES, f"Missing from CORE_TOOL_NAMES: {t}"
print(f"CORE_TOOL_NAMES: all {len(new_core)} validation tools present")

new_ro = [
    "get_validation_report", "get_model_profile", "list_validation_checks",
    "list_validations", "get_validation_status",
]
for t in new_ro:
    assert t in READ_ONLY_PARALLEL_TOOLS, f"Missing from READ_ONLY_PARALLEL_TOOLS: {t}"
print(f"READ_ONLY_PARALLEL_TOOLS: all {len(new_ro)} read-only tools present")

# Verify tools export
from ouroboros.tools.validation import get_tools as val_tools
from ouroboros.tools.model_intake import get_tools as intake_tools

vt = {t.name for t in val_tools()}
assert "run_validation" in vt
assert "create_validation_check" in vt
assert len(vt) >= 10

it = {t.name for t in intake_tools()}
assert "ingest_model_artifacts" in it

print(f"OK: {len(vt)} validation tools, {len(it)} intake tools registered")

# Verify consciousness whitelist
# We can't easily import the class without full init, so just read the source
from pathlib import Path
src = (Path(__file__).parent.parent / "ouroboros" / "consciousness.py").read_text()
bg_tools = [
    "list_validations", "get_validation_status", "get_validation_report",
    "get_model_profile", "list_validation_checks",
    "get_finding_effectiveness", "get_recommendation_effectiveness",
    "get_platform_metrics", "get_evolution_targets",
]
for t in bg_tools:
    assert f'"{t}"' in src, f"Missing from _BG_TOOL_WHITELIST: {t}"
print(f"_BG_TOOL_WHITELIST: all {len(bg_tools)} validation tools present in source")

print("\nAll checks passed!")
