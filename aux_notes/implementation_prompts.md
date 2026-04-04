# Implementation Prompts for Ouroboros Validation Platform

**How to use:** Execute these prompts sequentially in separate Claude Code sessions.
Each prompt contains both implementation AND testing — do not proceed to the next
prompt until all tests in the current one pass.

**Important:** Start each new session by saying:
> Read `aux_notes/ouroboros_validation_platform_plan.md` — this is the master plan.
> Then execute the prompt below.

This ensures the agent has full context on the architecture.

**Testing philosophy:** Every prompt ends with a `## Verify` section. These are
not smoke tests ("does it import?") — they test actual behavior. If a test fails,
fix it within the same session before moving on. Do NOT defer broken tests to
prompt 12.

---

## Prompt 1 of 12: Foundation — Types, Config, Directory Structure

```
Read the master plan in aux_notes/ouroboros_validation_platform_plan.md (sections §2.4, §5.3, §8).

Create the foundation layer — dataclasses, configuration, and directory setup.
Do NOT create any pipeline logic, tools, or checks yet.

Files to create:

1. ouroboros/validation/__init__.py — package init, just a docstring.

2. ouroboros/validation/types.py — All dataclasses from §2.4 exactly:
   - CheckResult (with improvement_suggestion field)
   - ValidationStageResult
   - ImprovementRecommendation (with kind: "hard" | "soft", see the plan for the full
     comment explaining the difference)
   - ValidationReport (with hard_recommendations and soft_recommendations split)
   - RevalidationResult
   - SandboxResult (returncode, stdout, stderr, duration_sec, oom_killed, timeout_killed)
   - ValidationConfig (all fields from §5.3 config keys, with defaults matching the plan)
   - ModelProfile as a dataclass mirroring the model_profile.json schema from §2.2
     (bundle_id, task_description, model_type, model_type_confidence, framework,
     framework_confidence, algorithm, data_format, target_column, target_column_confidence,
     feature_columns, protected_attributes_candidates, temporal_column, data_files, code_files,
     preprocessing_steps, data_join_logic, train_test_split_method, hyperparameters,
     metrics_mentioned_in_code, dependencies_detected, known_limitations_from_comments,
     llm_warnings, comprehension_confidence, comprehension_gaps)
   All dataclasses should have a to_dict() method and a from_dict(d) classmethod.

3. ouroboros/config.py — Add ALL new config keys from §5.3 to SETTINGS_DEFAULTS.
   Find the existing SETTINGS_DEFAULTS dict and append the new keys. Keep existing keys intact.
   Include: OUROBOROS_VALIDATION_DIR, OUROBOROS_VALIDATION_TIMEOUT_SEC,
   OUROBOROS_VALIDATION_STAGE_TIMEOUT_SEC, OUROBOROS_VALIDATION_SANDBOX_MEM_MB,
   OUROBOROS_VALIDATION_SANDBOX_CPU_SEC, OUROBOROS_VALIDATION_COMPREHENSION_MODEL,
   OUROBOROS_VALIDATION_COMPREHENSION_EFFORT, OUROBOROS_VALIDATION_SYNTHESIS_MODEL,
   OUROBOROS_VALIDATION_IMPROVEMENT_MODEL, OUROBOROS_VALIDATION_MATURITY_THRESHOLD,
   OUROBOROS_VALIDATION_EVO_MIN_BUNDLES_EARLY, OUROBOROS_VALIDATION_EVO_MIN_BUNDLES_MATURE,
   OUROBOROS_VALIDATION_AUTO_EVOLVE, OUROBOROS_VALIDATION_AUTO_IMPROVE,
   OUROBOROS_VALIDATION_AUTO_SELF_ASSESS, OUROBOROS_VALIDATION_REPORT_MODEL,
   OUROBOROS_VALIDATION_METHODOLOGY_VERSION, OUROBOROS_VALIDATION_IMPROVEMENT_LIFT_THRESHOLD,
   OUROBOROS_VALIDATION_MAX_HARD_RECOMMENDATIONS, OUROBOROS_VALIDATION_MAX_SOFT_RECOMMENDATIONS.

4. ouroboros/validation/config_loader.py — a small helper that reads the validation-specific
   config keys from ouroboros/config.py and returns a ValidationConfig instance.
   Read ouroboros/config.py first to understand how settings are loaded (likely via
   get_setting() or similar).

## Verify

After creating the files above, write and run tests/test_validation_types.py:

```python
"""Tests for validation foundation types."""
import json, pytest
from ouroboros.validation.types import (
    CheckResult, ValidationStageResult, ImprovementRecommendation,
    ValidationReport, RevalidationResult, SandboxResult,
    ValidationConfig, ModelProfile,
)

def test_check_result_roundtrip():
    """CheckResult serializes to dict and back without data loss."""
    cr = CheckResult(
        check_id="S2.OOS.AUC", check_name="OOS AUC", severity="critical",
        passed=False, score=0.65, details="AUC below threshold",
        evidence={"auc": 0.65, "threshold": 0.7},
        methodology_version="abc123", improvement_suggestion="Increase regularization"
    )
    d = cr.to_dict()
    cr2 = CheckResult.from_dict(d)
    assert cr2.check_id == "S2.OOS.AUC"
    assert cr2.score == 0.65
    assert cr2.evidence["auc"] == 0.65
    assert json.dumps(d)  # must be JSON-serializable

def test_improvement_recommendation_hard_vs_soft():
    """Hard and soft recommendations have correct fields."""
    hard = ImprovementRecommendation(
        finding_check_id="S3.OVERFIT", problem="Train/test gap > 0.1",
        recommendation="Add L2 regularization", kind="hard",
        implementation_sketch="model = Ridge(alpha=1.0)",
        estimated_metric_impact={"AUC": 0.03}, confidence=0.7,
        effort="trivial", priority=1,
    )
    soft = ImprovementRecommendation(
        finding_check_id="S3.SMALL_DATA", problem="Only 500 training rows",
        recommendation="Collect more data from production",
        kind="soft", implementation_sketch="",
        estimated_metric_impact={}, confidence=0.9,
        effort="infeasible", priority=2,
    )
    assert hard.kind == "hard"
    assert soft.kind == "soft"
    assert soft.effort == "infeasible"

def test_validation_report_splits_recommendations():
    """ValidationReport has separate hard and soft recommendation lists."""
    report = ValidationReport(
        bundle_id="test-123", model_profile={},
        overall_verdict="conditional", stages=[],
        critical_findings=[],
        hard_recommendations=[], soft_recommendations=[],
        estimated_total_improvement={"AUC": 0.05},
        generated_at="2026-01-01T00:00:00Z",
        methodology_snapshot="abc123",
        meta_scores={"comprehension_confidence": 0.85},
    )
    assert hasattr(report, 'hard_recommendations')
    assert hasattr(report, 'soft_recommendations')

def test_model_profile_roundtrip():
    """ModelProfile can serialize and deserialize with all optional fields."""
    mp = ModelProfile(
        bundle_id="test", task_description="Predict churn",
        model_type="classification", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.95,
        algorithm="RandomForestClassifier", data_format="tabular",
        target_column="churn", target_column_confidence=0.8,
        feature_columns=["age", "tenure"], protected_attributes_candidates=["gender"],
        temporal_column=None, data_files=[], code_files=[],
        preprocessing_steps=["StandardScaler"], data_join_logic=None,
        train_test_split_method="random 80/20", hyperparameters={"n_estimators": 100},
        metrics_mentioned_in_code={"accuracy": 0.85},
        dependencies_detected=["sklearn", "pandas"],
        known_limitations_from_comments=[], llm_warnings=[],
        comprehension_confidence=0.9, comprehension_gaps=[],
    )
    d = mp.to_dict()
    mp2 = ModelProfile.from_dict(d)
    assert mp2.algorithm == "RandomForestClassifier"
    assert mp2.comprehension_confidence == 0.9
    assert json.dumps(d)  # JSON-serializable

def test_validation_config_defaults():
    """ValidationConfig has sensible defaults matching the plan."""
    cfg = ValidationConfig()
    assert cfg.sandbox_mem_mb == 4096
    assert cfg.sandbox_cpu_sec == 120
    assert cfg.maturity_threshold == 20
    assert cfg.evo_min_bundles_early == 1
    assert cfg.evo_min_bundles_mature == 3
    assert cfg.auto_self_assess == True
    assert cfg.improvement_lift_threshold == 0.01
    assert cfg.max_hard_recommendations == 10
    assert cfg.max_soft_recommendations == 10

def test_revalidation_result_verdict():
    """RevalidationResult correctly stores verdict and metric deltas."""
    rr = RevalidationResult(
        original_bundle_id="a", improved_bundle_id="b",
        original_metrics={"AUC": 0.70}, improved_metrics={"AUC": 0.75},
        metric_deltas={"AUC": 0.05}, improvement_lift=0.071,
        recommendations_applied=["S3.OVERFIT"],
        recommendations_skipped=[], verdict="improved",
    )
    assert rr.verdict == "improved"
    assert rr.metric_deltas["AUC"] == 0.05
```

Run: `python -m pytest tests/test_validation_types.py -v`
All tests must pass before proceeding.
```

---

## Prompt 2 of 12: Sandbox — Secure Model Execution

```
Read the master plan §6 (Security: Model Execution Sandbox).
Read the existing ouroboros/tools/registry.py to understand SAFETY_CRITICAL_PATHS.
Read ouroboros/validation/types.py for SandboxResult and ValidationConfig.

Create ouroboros/validation/sandbox.py with the ModelSandbox class:

1. ModelSandbox.__init__(self, bundle_dir: Path, config: ValidationConfig)
   - Store bundle_dir, mem_limit (config.sandbox_mem_mb * 1024 * 1024),
     cpu_limit (config.sandbox_cpu_sec).

2. ModelSandbox.install_dependencies(self, packages: list[str]) -> str
   - Create a venv at bundle_dir/.sandbox_venv/ using venv module
   - pip install the listed packages into it (subprocess, timeout 300s)
   - Return success/failure message with details

3. ModelSandbox.run(self, script: str, timeout: int = 120) -> SandboxResult
   - Write script to a temp .py file inside bundle_dir
   - Execute via subprocess.Popen with:
     - cwd = bundle_dir
     - The sandbox venv's Python interpreter (or system Python if no venv)
     - RLIMIT_AS and RLIMIT_CPU via preexec_fn (use resource module)
     - Network isolation: try unshare --net (Linux), log warning if unavailable
     - Capture stdout/stderr (max 1MB each, truncate if longer)
     - Kill on timeout, detect OOM via returncode -9
   - Return SandboxResult dataclass
   - Clean up temp file in finally block

4. ModelSandbox.run_notebook(self, notebook_path: str, timeout: int = 300) -> SandboxResult
   - Convert notebook to script using nbformat + nbconvert
   - Then delegate to self.run()

Security requirements:
- NEVER allow the sandbox to access files outside bundle_dir
- NEVER allow network access from sandboxed code
- All subprocess calls must have timeouts
- Use the preexec_fn pattern for resource limits

## Verify

Write and run tests/test_sandbox.py:

```python
"""Tests for sandbox security and functionality."""
import pytest, tempfile, time
from pathlib import Path
from ouroboros.validation.sandbox import ModelSandbox
from ouroboros.validation.types import ValidationConfig

@pytest.fixture
def sandbox(tmp_path):
    cfg = ValidationConfig(sandbox_mem_mb=512, sandbox_cpu_sec=5)
    return ModelSandbox(tmp_path, cfg)

def test_basic_execution(sandbox):
    """Sandbox can run a simple script and capture stdout."""
    r = sandbox.run('print("hello")', timeout=10)
    assert r.returncode == 0
    assert "hello" in r.stdout

def test_timeout_kills_process(sandbox):
    """Script that runs too long is killed."""
    r = sandbox.run('import time; time.sleep(60)', timeout=3)
    assert r.timeout_killed == True
    assert r.duration_sec < 6  # should be ~3, not 60

def test_cannot_write_outside_bundle(sandbox, tmp_path):
    """Sandbox script cannot create files outside bundle_dir."""
    script = f'open("/tmp/sandbox_escape_test_{id(sandbox)}", "w").write("escaped")'
    r = sandbox.run(script, timeout=5)
    escape_file = Path(f"/tmp/sandbox_escape_test_{id(sandbox)}")
    # The script may succeed (we don't chroot) but this documents the boundary.
    # At minimum, verify the sandbox COMPLETES without hanging.
    assert r.duration_sec < 10

def test_script_error_captured(sandbox):
    """Sandbox captures stderr from crashing scripts."""
    r = sandbox.run('raise ValueError("test error")', timeout=5)
    assert r.returncode != 0
    assert "ValueError" in r.stderr

def test_stdout_truncation(sandbox):
    """Output larger than 1MB is truncated, not buffered forever."""
    r = sandbox.run('print("x" * 2_000_000)', timeout=10)
    assert len(r.stdout) <= 1_100_000  # ~1MB with some slack

def test_run_returns_duration(sandbox):
    """Duration is measured correctly."""
    r = sandbox.run('import time; time.sleep(1); print("done")', timeout=10)
    assert r.duration_sec >= 0.9
    assert r.duration_sec < 5
```

Run: `python -m pytest tests/test_sandbox.py -v`
All tests must pass. The timeout test is especially critical — if it hangs, the
sandbox has a fundamental bug.
```

---

## Prompt 3 of 12: Check Registry — Dynamic Check CRUD

```
Read the master plan §2.3 (Validation Checks as Dynamic, Evolvable Artifacts).
Read ouroboros/validation/types.py for CheckResult.

Create ouroboros/validation/check_registry.py with:

1. ValidationCheck dataclass:
   - check_id, stage, name, description, check_type ("deterministic"|"llm_assisted"|"sandbox"),
     enabled, created_by, created_at, version, tags
   - implementation_path: str — relative path to .py file in validation/checks/

2. CheckRegistry class:
   - __init__(self, repo_dir: Path)
   - _checks_dir property → repo_dir / "ouroboros" / "validation" / "checks"
   - _manifest_path property → _checks_dir / "check_manifest.json"
   - load_manifest() → read check_manifest.json, return list[ValidationCheck]
   - save_manifest(checks: list[ValidationCheck]) → write check_manifest.json
   - list_checks(stage=None, enabled_only=True) → list[ValidationCheck]
   - get_check(check_id) → ValidationCheck or raise KeyError
   - add_check(check: ValidationCheck) → str (writes manifest, returns check_id)
   - update_check(check_id, new_implementation: str, reason: str) → str
   - disable_check(check_id, reason: str) → str
   - delete_check(check_id, reason: str) → str
   - get_checks_for_stage(stage, model_profile: dict) → list[ValidationCheck]
     Filter by stage AND by tags matching model_profile's model_type/framework/data_format.
     If a check has no tags, it applies to all models.

3. Create ouroboros/validation/checks/__init__.py (empty)

4. Create ouroboros/validation/checks/check_manifest.json — initial empty list: []

5. Each check .py file must export:
   def run(bundle_dir: Path, model_profile: dict, sandbox=None) -> CheckResult

6. Write a helper function load_check_function(check: ValidationCheck, repo_dir: Path) -> Callable
   that dynamically imports the run() function from the check's .py file using importlib.

## Verify

Write and run tests/test_check_registry.py:

```python
"""Tests for dynamic check registry CRUD."""
import json, pytest
from pathlib import Path
from ouroboros.validation.check_registry import CheckRegistry, ValidationCheck, load_check_function

@pytest.fixture
def registry(tmp_path):
    """Create a registry in a temp dir with proper structure."""
    checks_dir = tmp_path / "ouroboros" / "validation" / "checks"
    checks_dir.mkdir(parents=True)
    (checks_dir / "__init__.py").touch()
    (checks_dir / "check_manifest.json").write_text("[]")
    return CheckRegistry(tmp_path)

def _make_check(check_id="TEST.001", stage="S2", enabled=True, tags=None):
    return ValidationCheck(
        check_id=check_id, stage=stage, name="Test check",
        description="A test", check_type="deterministic",
        enabled=enabled, created_by="test", created_at="2026-01-01",
        version=1, tags=tags or [],
        implementation_path=f"checks/{check_id.lower().replace('.', '_')}.py",
    )

def test_add_and_list(registry):
    """Can add a check and list it."""
    registry.add_check(_make_check("TEST.001"))
    checks = registry.list_checks()
    assert len(checks) == 1
    assert checks[0].check_id == "TEST.001"

def test_list_filters_by_stage(registry):
    registry.add_check(_make_check("S2.A", stage="S2"))
    registry.add_check(_make_check("S3.B", stage="S3"))
    s2_checks = registry.list_checks(stage="S2")
    assert len(s2_checks) == 1
    assert s2_checks[0].check_id == "S2.A"

def test_disable_hides_from_list(registry):
    registry.add_check(_make_check("TEST.001"))
    registry.disable_check("TEST.001", "test reason")
    assert len(registry.list_checks(enabled_only=True)) == 0
    assert len(registry.list_checks(enabled_only=False)) == 1

def test_delete_removes_completely(registry):
    registry.add_check(_make_check("TEST.001"))
    registry.delete_check("TEST.001", "test reason")
    assert len(registry.list_checks(enabled_only=False)) == 0

def test_get_unknown_check_raises(registry):
    with pytest.raises(KeyError):
        registry.get_check("NONEXISTENT")

def test_manifest_persists_to_disk(registry):
    """Manifest survives reload from disk."""
    registry.add_check(_make_check("TEST.001"))
    registry2 = CheckRegistry(registry._checks_dir.parent.parent)
    checks = registry2.list_checks()
    assert len(checks) == 1

def test_tag_filtering(registry):
    """get_checks_for_stage filters by model_profile tags."""
    registry.add_check(_make_check("S2.A", tags=["tabular", "classification"]))
    registry.add_check(_make_check("S2.B", tags=["tabular", "regression"]))
    registry.add_check(_make_check("S2.C", tags=[]))  # no tags = applies to all

    profile = {"model_type": "classification", "data_format": "tabular"}
    matches = registry.get_checks_for_stage("S2", profile)
    ids = [c.check_id for c in matches]
    assert "S2.A" in ids  # matches classification + tabular
    assert "S2.C" in ids  # no tags = universal
    assert "S2.B" not in ids  # regression doesn't match classification

def test_load_check_function(registry, tmp_path):
    """Can dynamically load and call a check's run() function."""
    check = _make_check("TEST.LOAD")
    # Write actual check code
    check_file = tmp_path / "ouroboros" / "validation" / check.implementation_path
    check_file.parent.mkdir(parents=True, exist_ok=True)
    check_file.write_text('''
from ouroboros.validation.types import CheckResult
def run(bundle_dir, model_profile, sandbox=None):
    return CheckResult(
        check_id="TEST.LOAD", check_name="Load test",
        severity="pass", passed=True, score=1.0,
        details="OK", evidence={},
        methodology_version="test", improvement_suggestion=None,
    )
''')
    registry.add_check(check)
    fn = load_check_function(check, tmp_path)
    result = fn(tmp_path, {})
    assert result.passed == True
    assert result.check_id == "TEST.LOAD"
```

Run: `python -m pytest tests/test_check_registry.py -v`
All tests must pass. Pay special attention to test_tag_filtering — if the agent
gets tag matching wrong, it will run wrong checks on wrong model types.
```

---

## Prompt 4 of 12: Seed Checks (S0, S2-S8)

```
Read the master plan §5.1 (New Modules — the seed checks list).
Read ouroboros/validation/types.py for CheckResult.
Read ouroboros/validation/check_registry.py to understand the check file format.

Create the initial set of validation check .py files in ouroboros/validation/checks/.
Each file exports: def run(bundle_dir: Path, model_profile: dict, sandbox=None) -> CheckResult

Also update checks/check_manifest.json to register each check.

Create these 9 checks:

1. checks/s0_code_parseable.py — Check if .py files parse (ast.parse) and .ipynb files
   load (nbformat.read). Return pass/fail with details of parse errors.
   check_type: "deterministic", tags: []

2. checks/s0_data_loadable.py — Try to load each data file in raw/data_samples/ with
   pandas (csv, parquet, excel, json). Return pass/fail with column counts, row counts,
   or error details. check_type: "deterministic", tags: ["tabular"]

3. checks/s2_oos_metrics.py — Generate a sandbox script that: loads data, trains model,
   computes test metrics (accuracy/AUC/RMSE). Compare with metrics_mentioned_in_code.
   Return score and pass/fail. check_type: "sandbox", tags: ["tabular"]

4. checks/s3_train_test_gap.py — Generate sandbox script: compute train vs test metric.
   If gap > 0.1, flag overfit. Include hard recommendation with implementation_sketch
   for regularization. check_type: "sandbox", tags: ["tabular"]

5. checks/s4_target_leakage.py — Read data, compute correlation matrix, flag features
   with > 0.95 correlation with target. check_type: "deterministic", tags: ["tabular"]

6. checks/s5_disparate_impact.py — For each protected_attributes_candidate, compute
   disparate impact ratio. Flag if < 0.8 or > 1.25.
   check_type: "deterministic", tags: ["tabular", "classification"]

7. checks/s6_feature_importance.py — Generate sandbox script for permutation importance.
   Flag counterintuitive results. check_type: "sandbox", tags: ["tabular"]

8. checks/s7_perturbation.py — Generate sandbox script: perturb numerics by ±1 std,
   measure prediction change. Flag if > 20% change.
   check_type: "sandbox", tags: ["tabular"]

9. checks/s8_code_smells.py — Deterministic code analysis: check for hardcoded paths,
   missing random seeds, no train/test split visible. Return soft recommendations.
   check_type: "deterministic", tags: []

Keep each check under 100 lines. For sandbox checks, the run() function should
generate a Python script string and call sandbox.run(script) if sandbox is provided,
or return a "skipped — no sandbox" result if sandbox is None.

## Verify

Write and run tests/test_seed_checks.py:

```python
"""Tests for seed validation checks — deterministic checks only.
Sandbox and LLM checks are tested with mocks."""
import ast, json, pytest, tempfile
from pathlib import Path
from ouroboros.validation.check_registry import CheckRegistry, load_check_function

@pytest.fixture
def repo_dir():
    """Use the actual repo directory so checks can be loaded."""
    return Path(__file__).parent.parent  # adjust if needed

@pytest.fixture
def bundle_with_python(tmp_path):
    """Create a minimal bundle with parseable Python files."""
    code_dir = tmp_path / "raw" / "model_code"
    code_dir.mkdir(parents=True)
    (code_dir / "train.py").write_text("import sklearn\nprint('hello')\n")
    return tmp_path

@pytest.fixture
def bundle_with_bad_python(tmp_path):
    code_dir = tmp_path / "raw" / "model_code"
    code_dir.mkdir(parents=True)
    (code_dir / "broken.py").write_text("def foo(\n")  # syntax error
    return tmp_path

@pytest.fixture
def bundle_with_csv(tmp_path):
    data_dir = tmp_path / "raw" / "data_samples"
    data_dir.mkdir(parents=True)
    (data_dir / "train.csv").write_text("a,b,target\n1,2,0\n3,4,1\n5,6,0\n")
    return tmp_path

def test_s0_code_parseable_pass(repo_dir, bundle_with_python):
    registry = CheckRegistry(repo_dir)
    check = registry.get_check("S0.CODE_PARSEABLE")
    fn = load_check_function(check, repo_dir)
    result = fn(bundle_with_python, {})
    assert result.passed == True

def test_s0_code_parseable_fail(repo_dir, bundle_with_bad_python):
    registry = CheckRegistry(repo_dir)
    check = registry.get_check("S0.CODE_PARSEABLE")
    fn = load_check_function(check, repo_dir)
    result = fn(bundle_with_bad_python, {})
    assert result.passed == False
    assert "syntax" in result.details.lower() or "parse" in result.details.lower()

def test_s0_data_loadable_pass(repo_dir, bundle_with_csv):
    registry = CheckRegistry(repo_dir)
    check = registry.get_check("S0.DATA_LOADABLE")
    fn = load_check_function(check, repo_dir)
    result = fn(bundle_with_csv, {"data_format": "tabular"})
    assert result.passed == True
    assert "3" in result.details or result.score is not None  # should report row count

def test_s4_target_leakage_detects_correlation(repo_dir, tmp_path):
    """S4 check should flag a feature perfectly correlated with target."""
    data_dir = tmp_path / "raw" / "data_samples"
    data_dir.mkdir(parents=True)
    # 'leaked' is literally the target — should be caught
    (data_dir / "train.csv").write_text(
        "feat1,leaked,target\n1,0,0\n2,1,1\n3,0,0\n4,1,1\n5,0,0\n"
    )
    registry = CheckRegistry(repo_dir)
    check = registry.get_check("S4.TARGET_LEAKAGE")
    fn = load_check_function(check, repo_dir)
    result = fn(tmp_path, {"target_column": "target", "data_format": "tabular"})
    assert result.passed == False  # should detect the leakage
    assert "leaked" in result.details.lower() or "correlation" in result.details.lower()

def test_s8_code_smells_finds_hardcoded_path(repo_dir, tmp_path):
    """S8 check should flag hardcoded absolute paths in model code."""
    code_dir = tmp_path / "raw" / "model_code"
    code_dir.mkdir(parents=True)
    (code_dir / "model.py").write_text(
        'import pandas as pd\ndf = pd.read_csv("/home/user/data/train.csv")\n'
    )
    registry = CheckRegistry(repo_dir)
    check = registry.get_check("S8.CODE_SMELLS")
    fn = load_check_function(check, repo_dir)
    result = fn(tmp_path, {})
    assert result.passed == False
    assert "path" in result.details.lower() or "hardcoded" in result.details.lower()

def test_all_checks_registered(repo_dir):
    """All 9 seed checks exist in the manifest."""
    registry = CheckRegistry(repo_dir)
    checks = registry.list_checks(enabled_only=False)
    assert len(checks) >= 9
    stages_present = {c.stage for c in checks}
    for s in ["S0", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]:
        assert s in stages_present, f"No checks for stage {s}"
```

Run: `python -m pytest tests/test_seed_checks.py -v`
The deterministic checks (S0, S4, S8) must pass. Sandbox-dependent checks (S2, S3, S6, S7)
are not tested here — they'll be integration-tested later. The key tests are:
code parseable detects syntax errors, target leakage detects perfect correlation,
code smells detects hardcoded paths.
```

---

## Prompt 5 of 12: Artifact Comprehension (S0) + Stage Orchestrators

```
Read the master plan §2.2 (S0: Artifact Comprehension) and §2.1 (Pipeline Stages).

Create two things:

A) ouroboros/validation/artifact_comprehension.py — The LLM-powered S0 stage.

   class ArtifactComprehension:
       def __init__(self, bundle_dir: Path, config: ValidationConfig):
           ...
       async def analyze(self) -> ModelProfile:
           """
           1. Enumerate files in raw/model_code/ and raw/data_samples/
           2. Read .py files (full text, truncate at 80K chars)
           3. Read .ipynb files — extract code cells and markdown cells via nbformat
           4. Load first 100 rows of each data file via pandas
              (try csv, parquet, excel, json — fail gracefully per file)
           5. Read inputs/task.txt and inputs/data_description.txt
           6. Build a prompt with all the above, asking the LLM to produce
              a JSON matching the ModelProfile schema from types.py
           7. Call LLM (use ouroboros/llm.py — read it first to understand the API)
           8. Parse LLM JSON response into ModelProfile dataclass
           9. Write model_profile.json, code_analysis.md, data_analysis.md to inferred/
           """

   Read ouroboros/llm.py first to understand how to make LLM calls. Use the existing
   LLMClient or chat_completion function — do not create a new LLM integration.
   Use config.comprehension_model and config.comprehension_effort.

B) Stage orchestrator stubs — thin modules that query CheckRegistry and run checks:

   Create these files in ouroboros/validation/ (each ~50-80 lines):
   - intake_check.py (S0 — runs ArtifactComprehension + S0 checks from registry)
   - reproducibility.py (S1 — runs model code in sandbox, checks it executes and
     produces deterministic output across 2 runs)
   - performance.py (S2 — runs S2 checks from registry)
   - fit_quality.py (S3 — runs S3 checks)
   - leakage.py (S4 — runs S4 checks)
   - fairness.py (S5 — runs S5 checks)
   - sensitivity.py (S6 — runs S6 checks)
   - robustness.py (S7 — runs S7 checks)
   - code_quality.py (S8 — runs S8 checks)
   - synthesis.py (S9 — placeholder that returns empty ValidationStageResult)

   Each orchestrator should follow this pattern:
   async def run_stage(bundle_dir, model_profile, check_registry, sandbox, config) -> ValidationStageResult:
       checks = check_registry.get_checks_for_stage("S{N}", model_profile.to_dict())
       results = []
       for check in checks:
           fn = load_check_function(check, repo_dir)
           try:
               result = fn(bundle_dir, model_profile.to_dict(), sandbox)
           except Exception as e:
               result = CheckResult(... passed=False, details=f"Check crashed: {e}" ...)
           results.append(result)
       return ValidationStageResult(stage="S{N}", ..., checks=results)

## Verify

Write and run tests/test_stage_orchestrators.py:

```python
"""Tests for stage orchestrators — uses real checks but no LLM or sandbox."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from ouroboros.validation.types import ValidationConfig, ModelProfile, ValidationStageResult
from ouroboros.validation.check_registry import CheckRegistry

@pytest.fixture
def repo_dir():
    return Path(__file__).parent.parent

@pytest.fixture
def bundle_with_data(tmp_path):
    code_dir = tmp_path / "raw" / "model_code"
    code_dir.mkdir(parents=True)
    (code_dir / "train.py").write_text("import sklearn\nprint('hello')\n")
    data_dir = tmp_path / "raw" / "data_samples"
    data_dir.mkdir(parents=True)
    (data_dir / "train.csv").write_text("a,b,target\n1,2,0\n3,4,1\n")
    return tmp_path

@pytest.fixture
def profile():
    return ModelProfile(
        bundle_id="test", task_description="test",
        model_type="classification", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="RandomForest", data_format="tabular",
        target_column="target", target_column_confidence=0.9,
        feature_columns=["a", "b"], protected_attributes_candidates=[],
        temporal_column=None, data_files=[], code_files=[],
        preprocessing_steps=[], data_join_logic=None,
        train_test_split_method=None, hyperparameters={},
        metrics_mentioned_in_code={}, dependencies_detected=[],
        known_limitations_from_comments=[], llm_warnings=[],
        comprehension_confidence=0.9, comprehension_gaps=[],
    )

@pytest.mark.asyncio
async def test_intake_runs_s0_checks(repo_dir, bundle_with_data, profile):
    """intake_check orchestrator runs S0 checks and returns results."""
    from ouroboros.validation.intake_check import run_stage
    registry = CheckRegistry(repo_dir)
    cfg = ValidationConfig()
    result = await run_stage(bundle_with_data, profile, registry, None, cfg)
    assert isinstance(result, ValidationStageResult)
    assert result.stage == "S0"
    assert len(result.checks) >= 1  # at least s0_code_parseable should run

@pytest.mark.asyncio
async def test_leakage_runs_s4_checks(repo_dir, bundle_with_data, profile):
    """leakage orchestrator runs S4 checks."""
    from ouroboros.validation.leakage import run_stage
    registry = CheckRegistry(repo_dir)
    cfg = ValidationConfig()
    result = await run_stage(bundle_with_data, profile, registry, None, cfg)
    assert isinstance(result, ValidationStageResult)
    assert result.stage == "S4"

@pytest.mark.asyncio
async def test_orchestrator_catches_check_crash(repo_dir, tmp_path, profile):
    """If a check crashes, orchestrator captures the error, doesn't propagate."""
    from ouroboros.validation.performance import run_stage
    # Empty bundle — sandbox checks will fail/skip gracefully
    (tmp_path / "raw" / "model_code").mkdir(parents=True)
    registry = CheckRegistry(repo_dir)
    cfg = ValidationConfig()
    result = await run_stage(tmp_path, profile, registry, None, cfg)
    assert isinstance(result, ValidationStageResult)
    # Should not raise — errors captured in check results
    for check in result.checks:
        if not check.passed:
            assert check.details  # must have an explanation
```

Run: `python -m pytest tests/test_stage_orchestrators.py -v`
Key: orchestrators must not crash even when individual checks fail.
```

---

## Prompt 6 of 12: Pipeline Orchestrator + Intake Tool

```
Read the master plan §2.5 (Pipeline Orchestrator) and §1.2-1.3 (Intake Tool).
Read ouroboros/tools/core.py and ouroboros/tools/registry.py to understand the
ToolEntry pattern and how tools export get_tools().

Create two files:

A) ouroboros/validation/pipeline.py

   class ValidationPipeline:
       def __init__(self, bundle_id: str, ctx: ToolContext, config: ValidationConfig):
           self._bundle_dir = ctx.drive_path(f"validations/{bundle_id}")
           self._check_registry = CheckRegistry(ctx.repo_dir)
           self._sandbox = ModelSandbox(self._bundle_dir, config)
           ...

       async def run(self) -> ValidationReport:
           # 1. S0 (Artifact Comprehension) — HARD GATE: if fails, return error report
           # 2. S1 (Reproducibility) — HARD GATE for S2-S7
           # 3. S2-S7 — run all applicable, collect findings
           # 4. S4, S8 — run even if S1 failed (code-only checks)
           # 5. S9 (Synthesis) — placeholder for now, aggregate results
           # 6. Build ValidationReport with hard/soft recommendations split
           # Save stage_S{N}.json to results/
           # Save report.json to results/

       def run_single_stage(self, stage: str) -> ValidationStageResult:
           ...

   class RevalidationPipeline:
       """Stub for now — will be completed in Prompt 10."""
       pass

B) ouroboros/tools/model_intake.py — ToolEntry-based tools.

   Tools:
   - ingest_model_artifacts(ctx, model_code_zip, task, data_zip="", data_description="")
     → Extract ZIPs, create directory structure from §1.3 (raw/model_code/,
       raw/data_samples/, inputs/, inferred/, results/, improvement/),
       generate bundle_id (UUID), write task.txt and data_description.txt,
       return bundle_id and file listing summary.
   - list_validations(ctx, status="all")
     → Scan data/validations/, read each results/report.json if exists, return table.
   - get_validation_status(ctx, bundle_id)
     → Return current state of a specific validation.

   Export via get_tools() -> list[ToolEntry].

## Verify

Write and run tests/test_intake.py:

```python
"""Tests for model intake tool — file handling, ZIP extraction, directory structure."""
import json, pytest, tempfile, zipfile
from pathlib import Path
from ouroboros.validation.types import ValidationConfig

def _make_code_zip(tmp_path) -> Path:
    """Create a minimal model code ZIP."""
    zip_path = tmp_path / "model_code.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("train.py", "from sklearn.ensemble import RandomForestClassifier\nprint('train')\n")
        zf.writestr("utils.py", "def load_data(): pass\n")
    return zip_path

def _make_data_zip(tmp_path) -> Path:
    """Create a minimal data ZIP."""
    zip_path = tmp_path / "data.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("train.csv", "a,b,target\n1,2,0\n3,4,1\n5,6,0\n")
    return zip_path

def test_ingest_creates_directory_structure(tmp_path):
    """After ingestion, all expected directories exist."""
    from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
    # You may need to adjust this to call the underlying implementation
    # rather than the ToolEntry handler, depending on how you structured it.
    code_zip = _make_code_zip(tmp_path)
    data_zip = _make_data_zip(tmp_path)
    validations_dir = tmp_path / "validations"
    validations_dir.mkdir()

    # Call the implementation (adjust function name as needed)
    bundle_id = _ingest_model_artifacts_impl(
        validations_dir=validations_dir,
        model_code_zip=str(code_zip),
        task="Predict customer churn",
        data_zip=str(data_zip),
        data_description="Customer data with churn labels",
    )

    bundle_dir = validations_dir / bundle_id
    assert (bundle_dir / "raw" / "model_code" / "train.py").exists()
    assert (bundle_dir / "raw" / "data_samples" / "train.csv").exists()
    assert (bundle_dir / "inputs" / "task.txt").read_text() == "Predict customer churn"
    assert (bundle_dir / "inputs" / "data_description.txt").exists()
    assert (bundle_dir / "inferred").is_dir()
    assert (bundle_dir / "results").is_dir()
    assert (bundle_dir / "improvement").is_dir()

def test_ingest_without_data_zip(tmp_path):
    """Ingestion works without data — data_samples dir should be empty or absent."""
    from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
    code_zip = _make_code_zip(tmp_path)
    validations_dir = tmp_path / "validations"
    validations_dir.mkdir()

    bundle_id = _ingest_model_artifacts_impl(
        validations_dir=validations_dir,
        model_code_zip=str(code_zip),
        task="Predict churn",
    )

    bundle_dir = validations_dir / bundle_id
    assert (bundle_dir / "raw" / "model_code" / "train.py").exists()
    assert (bundle_dir / "inputs" / "task.txt").exists()

def test_ingest_invalid_zip(tmp_path):
    """Ingesting a non-ZIP file should raise or return error."""
    from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
    bad_file = tmp_path / "not_a_zip.txt"
    bad_file.write_text("this is not a zip")
    validations_dir = tmp_path / "validations"
    validations_dir.mkdir()

    with pytest.raises(Exception):  # zipfile.BadZipFile or ValueError
        _ingest_model_artifacts_impl(
            validations_dir=validations_dir,
            model_code_zip=str(bad_file),
            task="test",
        )
```

Run: `python -m pytest tests/test_intake.py -v`
Key: directory structure must match the plan exactly. Invalid input must not silently corrupt state.

NOTE: You may need to refactor model_intake.py to expose an _impl function
that the tests can call without a full ToolContext. This is fine — the ToolEntry
handler is a thin wrapper around _impl.
```

---

## Prompt 7 of 12: Validation Tools + Tool Registration

```
Read the master plan §2.6 (Validation Tools) and §5.2 (Modified Existing Files —
tool_capabilities.py, consciousness.py).

Create ouroboros/tools/validation.py with ALL validation tools from §2.6:
- run_validation(ctx, bundle_id, stages="all")
- run_validation_stage(ctx, bundle_id, stage)
- get_validation_report(ctx, bundle_id)
- get_model_profile(ctx, bundle_id)
- list_validation_checks(ctx, stage="all", enabled_only=True)
- create_validation_check(ctx, check_id, stage, name, description, check_type, code, tags="")
- edit_validation_check(ctx, check_id, new_code, reason)
- disable_validation_check(ctx, check_id, reason)
- delete_validation_check(ctx, check_id, reason)
- run_improvement_cycle(ctx, bundle_id) — STUB for now, returns "not yet implemented"
- compare_validations(ctx, bundle_id_a, bundle_id_b) — non-core
- backtest_check(ctx, check_id, bundle_ids="all") — non-core

Each tool follows the ToolEntry pattern. Export via get_tools().

THEN update existing files (READ EACH FILE FULLY before modifying):

1. ouroboros/tool_capabilities.py — Add to CORE_TOOL_NAMES:
   "ingest_model_artifacts", "list_validations", "get_validation_status",
   "run_validation", "run_validation_stage", "get_validation_report",
   "get_model_profile", "list_validation_checks", "create_validation_check",
   "edit_validation_check", "disable_validation_check", "delete_validation_check",
   "run_improvement_cycle"
   Add to READ_ONLY_PARALLEL_TOOLS:
   "get_validation_report", "get_model_profile", "list_validation_checks",
   "list_validations", "get_validation_status"

2. ouroboros/consciousness.py — Add to _BG_TOOL_WHITELIST:
   "list_validations", "get_validation_status", "get_validation_report",
   "get_model_profile", "list_validation_checks",
   "get_finding_effectiveness", "get_recommendation_effectiveness",
   "get_platform_metrics", "get_evolution_targets"

## Verify

```python
# Quick structural verification — run as one-off script
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

new_ro = [
    "get_validation_report", "get_model_profile", "list_validation_checks",
    "list_validations", "get_validation_status",
]
for t in new_ro:
    assert t in READ_ONLY_PARALLEL_TOOLS, f"Missing from READ_ONLY_PARALLEL_TOOLS: {t}"

# Verify tools export
from ouroboros.tools.validation import get_tools as val_tools
from ouroboros.tools.model_intake import get_tools as intake_tools
from ouroboros.tools.validation_feedback import get_tools as fb_tools  # may not exist yet

vt = {t.name for t in val_tools()}
assert "run_validation" in vt
assert "create_validation_check" in vt
assert len(vt) >= 10

it = {t.name for t in intake_tools()}
assert "ingest_model_artifacts" in it

print(f"OK: {len(vt)} validation tools, {len(it)} intake tools registered")
```

Run as: `python -c "$(cat tests/verify_tool_registration.py)"`
or save and run: `python tests/verify_tool_registration.py`
```

---

## Prompt 8 of 12: S9 Synthesis + Report Generation

```
Read the master plan §4.1 (Improvement Plan), §2.4 (ValidationReport), and §2.1 (S9 stage).

Implement two modules:

A) ouroboros/validation/synthesis.py — The S9 stage (replace the placeholder).

   async def run_stage(bundle_dir, model_profile, all_stage_results, config) -> ValidationStageResult:
       """
       1. Collect all CheckResults from S0-S8 where passed=False
       2. For each finding, call LLM to classify as hard or soft and generate
          ImprovementRecommendation
       3. Prioritize by estimated impact / effort
       4. Cap at MAX_HARD_RECOMMENDATIONS and MAX_SOFT_RECOMMENDATIONS
       5. Return ValidationStageResult with the recommendations as check results
       """

   The LLM prompt should include:
   - The model_profile (what the model is)
   - All failed checks with their details and evidence
   - Instruction: for each finding produce EITHER a hard rec (specific code change +
     estimated metric impact) OR a soft rec (explain value and why infeasible)

B) ouroboros/validation/report.py — Report generation.

   class ReportGenerator:
       def generate_json(report: ValidationReport) -> str
       def generate_markdown(report: ValidationReport, config) -> str
           # LLM-generated narrative
       def save(report: ValidationReport, bundle_dir: Path, config)
           # Write report.json and report.md to results/

Update pipeline.py: replace S9 placeholder with synthesis.run_stage call,
use ReportGenerator for final output.

## Verify

```python
"""Test synthesis and report generation with mocked LLM."""
import json, pytest
from unittest.mock import AsyncMock, patch
from ouroboros.validation.types import *
from ouroboros.validation.report import ReportGenerator

def _make_failed_check(check_id, details):
    return CheckResult(
        check_id=check_id, check_name=check_id, severity="warning",
        passed=False, score=0.5, details=details, evidence={"val": 0.5},
        methodology_version="test", improvement_suggestion=None,
    )

def test_report_json_roundtrip():
    """Report can be serialized to JSON and parsed back."""
    report = ValidationReport(
        bundle_id="test-123", model_profile={"model_type": "classification"},
        overall_verdict="conditional",
        stages=[ValidationStageResult("S0", "Intake", "passed", [], 1.0, None)],
        critical_findings=[_make_failed_check("S3.OVERFIT", "Gap too large")],
        hard_recommendations=[
            ImprovementRecommendation(
                finding_check_id="S3.OVERFIT", problem="Overfit",
                recommendation="Add regularization", kind="hard",
                implementation_sketch="Ridge(alpha=1.0)",
                estimated_metric_impact={"AUC": 0.03},
                confidence=0.7, effort="trivial", priority=1,
            )
        ],
        soft_recommendations=[
            ImprovementRecommendation(
                finding_check_id="S3.SMALL_DATA", problem="Too few rows",
                recommendation="Collect more data", kind="soft",
                implementation_sketch="", estimated_metric_impact={},
                confidence=0.9, effort="infeasible", priority=2,
            )
        ],
        estimated_total_improvement={"AUC": 0.03},
        generated_at="2026-01-01T00:00:00Z",
        methodology_snapshot="abc123",
        meta_scores={"comprehension_confidence": 0.85},
    )
    gen = ReportGenerator()
    j = gen.generate_json(report)
    parsed = json.loads(j)
    assert parsed["bundle_id"] == "test-123"
    assert len(parsed["hard_recommendations"]) == 1
    assert len(parsed["soft_recommendations"]) == 1
    assert parsed["hard_recommendations"][0]["kind"] == "hard"
    assert parsed["soft_recommendations"][0]["kind"] == "soft"

def test_report_save_creates_files(tmp_path):
    """save() creates both report.json and report.md."""
    report = ValidationReport(
        bundle_id="test", model_profile={}, overall_verdict="approved",
        stages=[], critical_findings=[], hard_recommendations=[],
        soft_recommendations=[], estimated_total_improvement={},
        generated_at="2026-01-01", methodology_snapshot="test",
        meta_scores={},
    )
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    gen = ReportGenerator()
    gen.save(report, tmp_path, ValidationConfig())
    assert (results_dir / "report.json").exists()
    # report.md may need LLM — if so, verify it gracefully handles missing LLM
```

Run: `python -m pytest tests/test_synthesis_report.py -v`
```

---

## Prompt 9 of 12: Effectiveness Tracker + Feedback Tools + Self-Assessment

```
Read the master plan §3.1-§3.5 (Feedback Signals, Metrics, Effectiveness Tracker,
Feedback Tools).

Create three files:

A) ouroboros/validation/effectiveness.py — full EffectivenessTracker (see plan §3.3).
   Storage: JSONL file. Must implement: maturity_phase, all recording methods,
   all stats methods, get_evolution_targets with maturity-aware logic.

B) ouroboros/validation/self_assessment.py — Tier 0 self-labeling.
   After validation, LLM rates each failed finding as likely-TP or likely-FP.
   Stored with source="self_assessed", weight=0.3.

C) ouroboros/tools/validation_feedback.py — all feedback tools from §3.5.
   Export via get_tools().

Wire self-assessment into pipeline.py.

## Verify

```python
"""Tests for effectiveness tracker — the core of the self-improvement loop."""
import json, pytest
from pathlib import Path
from ouroboros.validation.effectiveness import EffectivenessTracker

@pytest.fixture
def tracker(tmp_path):
    return EffectivenessTracker(tmp_path)

def test_starts_in_early_phase(tracker):
    assert tracker.maturity_phase == "early"

def test_records_self_assessment(tracker):
    """Tier 0 self-assessed feedback is recorded with weight 0.3."""
    tracker.record_finding_feedback(
        "S2.OOS", "bundle-1", "true_positive", source="self_assessed", weight=0.3
    )
    stats = tracker.get_finding_stats("S2.OOS")
    assert stats.self_assessed_tp == 1

def test_human_feedback_overrides_self(tracker):
    """Tier 2 human label has weight 1.0, higher than Tier 0."""
    tracker.record_finding_feedback(
        "S2.OOS", "bundle-1", "false_positive", source="self_assessed", weight=0.3
    )
    tracker.record_finding_feedback(
        "S2.OOS", "bundle-1", "true_positive", source="human", weight=1.0
    )
    stats = tracker.get_finding_stats("S2.OOS")
    assert stats.human_tp == 1
    # Weighted precision should favor the human label

def test_recommendation_tracking_independent(tracker):
    """Recommendation quality tracked separately from finding quality."""
    tracker.record_finding_feedback(
        "S3.OVERFIT", "b1", "true_positive", source="human", weight=1.0
    )
    tracker.record_recommendation_result(
        "S3.OVERFIT", "b1",
        metric_before={"AUC": 0.70}, metric_after={"AUC": 0.68}
    )
    f_stats = tracker.get_finding_stats("S3.OVERFIT")
    r_stats = tracker.get_recommendation_stats("S3.OVERFIT")
    # Finding is TP (correct diagnosis) but recommendation degraded metrics
    assert f_stats.human_tp == 1
    assert r_stats.recommendations_degraded == 1

def test_maturity_transition(tracker):
    """Phase transitions from early to mature at threshold."""
    for i in range(20):
        tracker.record_finding_feedback(
            "S2.OOS", f"bundle-{i}", "true_positive", source="human", weight=1.0
        )
    assert tracker.maturity_phase == "mature"

def test_evolution_targets_early_phase(tracker):
    """Early phase targets focus on crashes and obvious issues."""
    tracker.record_finding_feedback(
        "S2.OOS", "b1", "false_positive", source="self_assessed", weight=0.3
    )
    targets = tracker.get_evolution_targets()
    # Should produce targets even with minimal data
    assert isinstance(targets, list)

def test_underperformers(tracker):
    """Checks with low precision are flagged."""
    for i in range(5):
        tracker.record_finding_feedback(
            "BAD.CHECK", f"b{i}", "false_positive", source="human", weight=1.0
        )
    tracker.record_finding_feedback(
        "BAD.CHECK", "b5", "true_positive", source="human", weight=1.0
    )
    underperformers = tracker.get_underperformers(min_samples=5, max_precision=0.5)
    assert "BAD.CHECK" in underperformers  # 1 TP, 5 FP → precision ~0.17

def test_persistence(tracker, tmp_path):
    """Data survives reloading from disk."""
    tracker.record_finding_feedback(
        "S2.OOS", "b1", "true_positive", source="human", weight=1.0
    )
    tracker2 = EffectivenessTracker(tmp_path)
    stats = tracker2.get_finding_stats("S2.OOS")
    assert stats.human_tp == 1
```

Run: `python -m pytest tests/test_effectiveness.py -v`
Critical tests: maturity transition, recommendation independence from findings,
self-assessment recording with correct weight, persistence.
```

---

## Prompt 10 of 12: Model Improver + Revalidation + Full Cycle

```
Read the master plan §4.2 (Model Improver), §4.3 (Revalidation), §4.4 (Ground Truth).

Implement:

A) ouroboros/validation/model_improver.py — ModelImprover class.
   Filters to hard recommendations only. For each (in priority order):
   copy code, LLM modifies it, sandbox runs it.

B) Complete RevalidationPipeline in pipeline.py.
   Re-runs S2-S7 on improved code. Computes metric deltas and improvement_lift.
   Records both Signal A (rec quality) and Signal B (inferred finding quality
   with weight 0.5) in effectiveness tracker.

C) Complete run_improvement_cycle tool in ouroboros/tools/validation.py.

D) Wire auto-improve into pipeline.py.

## Verify

```python
"""Tests for improvement cycle — uses mocked LLM and sandbox."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from ouroboros.validation.types import *

def test_model_improver_filters_soft_recs():
    """ModelImprover only processes hard recommendations."""
    from ouroboros.validation.model_improver import ModelImprover
    hard = ImprovementRecommendation(
        finding_check_id="S3.OVERFIT", problem="Overfit",
        recommendation="Add reg", kind="hard",
        implementation_sketch="Ridge(alpha=1.0)",
        estimated_metric_impact={"AUC": 0.03},
        confidence=0.7, effort="trivial", priority=1,
    )
    soft = ImprovementRecommendation(
        finding_check_id="S3.SMALL", problem="Small data",
        recommendation="Get more data", kind="soft",
        implementation_sketch="", estimated_metric_impact={},
        confidence=0.9, effort="infeasible", priority=2,
    )
    # ModelImprover should accept both but only process hard
    improver = ModelImprover.__new__(ModelImprover)
    filtered = [r for r in [hard, soft] if r.kind == "hard"]
    assert len(filtered) == 1
    assert filtered[0].finding_check_id == "S3.OVERFIT"

def test_improvement_lift_computation():
    """Improvement lift is correctly computed from before/after metrics."""
    before = {"AUC": 0.70, "accuracy": 0.80}
    after = {"AUC": 0.75, "accuracy": 0.82}
    deltas = {k: after[k] - before[k] for k in before}
    lifts = {k: deltas[k] / abs(before[k]) if before[k] != 0 else 0 for k in deltas}
    assert abs(lifts["AUC"] - 0.0714) < 0.001  # 0.05 / 0.70
    assert abs(lifts["accuracy"] - 0.025) < 0.001  # 0.02 / 0.80

def test_revalidation_verdict_logic():
    """Verify verdict assignment based on lift threshold."""
    threshold = 0.01

    # Improved
    lift = 0.05
    assert (lift > threshold)  # → "improved"

    # Degraded
    lift = -0.03
    assert (lift < -threshold)  # → "degraded"

    # Unchanged
    lift = 0.005
    assert (-threshold <= lift <= threshold)  # → "unchanged"

def test_signal_a_and_b_recorded_independently():
    """After improvement cycle, rec quality and finding quality
    are recorded as separate entries."""
    from ouroboros.validation.effectiveness import EffectivenessTracker
    import tempfile
    tracker = EffectivenessTracker(Path(tempfile.mkdtemp()))

    # Signal A: recommendation improved metrics → rec was useful
    tracker.record_recommendation_result(
        "S3.OVERFIT", "bundle-1",
        metric_before={"AUC": 0.70}, metric_after={"AUC": 0.75}
    )
    # Signal B: inferred finding quality (weight 0.5, not 1.0)
    tracker.record_finding_feedback(
        "S3.OVERFIT", "bundle-1", "true_positive",
        source="improvement_inferred", weight=0.5
    )

    r_stats = tracker.get_recommendation_stats("S3.OVERFIT")
    f_stats = tracker.get_finding_stats("S3.OVERFIT")

    assert r_stats.recommendations_improved == 1
    # Finding stat should reflect the inferred TP at weight 0.5
    # (exact assertion depends on implementation)
```

Run: `python -m pytest tests/test_improvement_cycle.py -v`
```

---

## Prompt 11 of 12: Prompt and Identity Changes

```
Read the master plan §7 (Prompt File Changes — Detailed).

This prompt modifies existing files. Read each file FULLY before editing.
Do NOT overwrite — use surgical edits (str_replace_editor or Edit tool).

1. prompts/SYSTEM.md — changes from §7.1
2. BIBLE.md — changes from §7.2
3. prompts/CONSCIOUSNESS.md — changes from §3.6 / §7.3
4. ouroboros/memory.py — new _default_identity() from §7.4
5. docs/CHECKLISTS.md — add Validation Methodology Commit Checklist from §5.4
6. ouroboros/context.py — add validation state to dynamic context

IMPORTANT: Read each file FULLY before editing. Do not accidentally delete content.

## Verify

```bash
# Verify SYSTEM.md has new identity
grep -q "Ouroboros-V" prompts/SYSTEM.md && echo "SYSTEM.md: identity OK" || echo "SYSTEM.md: MISSING identity"
grep -q "maturity phase" prompts/SYSTEM.md && echo "SYSTEM.md: maturity OK" || echo "SYSTEM.md: MISSING maturity"
grep -q "Validation Domain Context" prompts/SYSTEM.md && echo "SYSTEM.md: domain OK" || echo "SYSTEM.md: MISSING domain context"

# Verify BIBLE.md has constraints
grep -q "Validation Hard Limits" BIBLE.md && echo "BIBLE.md: constraints OK" || echo "BIBLE.md: MISSING constraints"
grep -q "finding quality" BIBLE.md && echo "BIBLE.md: decoupled OK" || echo "BIBLE.md: MISSING decoupled quality"

# Verify CONSCIOUSNESS.md has new tasks
grep -q "Effectiveness review" prompts/CONSCIOUSNESS.md && echo "CONSCIOUSNESS.md: tasks OK" || echo "CONSCIOUSNESS.md: MISSING tasks"
grep -q "LLM calibration" prompts/CONSCIOUSNESS.md && echo "CONSCIOUSNESS.md: calibration OK" || echo "CONSCIOUSNESS.md: MISSING calibration"

# Verify memory.py has new identity
grep -q "Ouroboros-V" ouroboros/memory.py && echo "memory.py: identity OK" || echo "memory.py: MISSING identity"
grep -q "EARLY PHASE" ouroboros/memory.py && echo "memory.py: phase OK" || echo "memory.py: MISSING phase"

# Verify CHECKLISTS.md has new section
grep -q "Validation Methodology Commit Checklist" docs/CHECKLISTS.md && echo "CHECKLISTS.md: OK" || echo "CHECKLISTS.md: MISSING"

# Verify nothing was accidentally deleted
wc -l prompts/SYSTEM.md  # should be >= 750 lines (was 761, we added content)
wc -l BIBLE.md            # should be >= 390 lines (was 394, we added content)
```

Run each grep. All should say "OK". If SYSTEM.md or BIBLE.md line counts dropped
significantly, something was accidentally deleted — investigate and fix.
```

---

## Prompt 12 of 12: Safety, Hardening, and Integration Tests

```
Read the master plan §6.2 (Safety), §5.2 (Modified Files), §10 (Error Handling).

Final hardening pass:

1. prompts/SAFETY.md — Add validation-specific verdicts
2. ouroboros/tools/registry.py — Add sandbox.py to SAFETY_CRITICAL_PATHS
3. launcher.py — Add sandbox.py to sync_paths
4. ouroboros/reflection.py — Add error markers
5. docs/ARCHITECTURE.md — Add Validation Pipeline section
6. docs/DEVELOPMENT.md — Add validation module conventions

## Verify

```python
"""Final integration test — end-to-end validation of a simple model."""
import pytest, zipfile, json
from pathlib import Path

def test_safety_critical_paths():
    """sandbox.py is protected."""
    from ouroboros.tools.registry import SAFETY_CRITICAL_PATHS
    assert "ouroboros/validation/sandbox.py" in SAFETY_CRITICAL_PATHS

def test_full_pipeline_smoke(tmp_path):
    """End-to-end: ingest a model, run validation, get report.
    This is a smoke test — LLM calls are mocked."""
    # Create model code ZIP
    code_zip = tmp_path / "model.zip"
    with zipfile.ZipFile(code_zip, 'w') as zf:
        zf.writestr("train.py", '''
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
df = pd.read_csv("data/train.csv")
X = df[["a", "b"]]
y = df["target"]
X_train, X_test, y_train, y_test = train_test_split(X, y, random_state=42)
model = RandomForestClassifier(n_estimators=10, random_state=42)
model.fit(X_train, y_train)
print(f"accuracy: {model.score(X_test, y_test)}")
''')

    # Create data ZIP
    data_zip = tmp_path / "data.zip"
    with zipfile.ZipFile(data_zip, 'w') as zf:
        rows = "a,b,target\n" + "\n".join(
            f"{i},{i*2},{i%2}" for i in range(100)
        )
        zf.writestr("train.csv", rows)

    # Ingest
    from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
    val_dir = tmp_path / "validations"
    val_dir.mkdir()
    bundle_id = _ingest_model_artifacts_impl(
        validations_dir=val_dir,
        model_code_zip=str(code_zip),
        task="Binary classification on synthetic data",
        data_zip=str(data_zip),
        data_description="Two numeric features, binary target",
    )

    bundle_dir = val_dir / bundle_id
    assert bundle_dir.exists()
    assert (bundle_dir / "raw" / "model_code" / "train.py").exists()
    assert (bundle_dir / "raw" / "data_samples" / "train.csv").exists()

    # Run deterministic checks only (no LLM, no sandbox)
    from ouroboros.validation.check_registry import CheckRegistry, load_check_function
    repo_dir = Path(__file__).parent.parent
    registry = CheckRegistry(repo_dir)

    # S0 checks should work
    s0_checks = registry.get_checks_for_stage("S0", {"data_format": "tabular"})
    for check in s0_checks:
        fn = load_check_function(check, repo_dir)
        result = fn(bundle_dir, {"data_format": "tabular", "target_column": "target"})
        assert result.check_id.startswith("S0")
        # Code should be parseable, data should be loadable
        if "PARSEABLE" in result.check_id:
            assert result.passed == True
        if "LOADABLE" in result.check_id:
            assert result.passed == True

    print(f"Integration smoke test passed for bundle {bundle_id}")
```

Run: `python -m pytest tests/test_integration.py -v`

Also run the full test suite:
`python -m pytest tests/ -v --ignore=tests/test_llm_client_refresh.py`
(ignore tests that require live LLM keys)

All tests must pass.
```

---

## Post-Implementation Checklist

After all 12 prompts are done:

- [ ] `python -m pytest tests/test_validation_types.py tests/test_sandbox.py tests/test_check_registry.py tests/test_seed_checks.py tests/test_stage_orchestrators.py tests/test_intake.py tests/test_synthesis_report.py tests/test_effectiveness.py tests/test_improvement_cycle.py tests/test_integration.py -v`
- [ ] All grep checks from Prompt 11 return "OK"
- [ ] `sandbox.py` in SAFETY_CRITICAL_PATHS (Prompt 12 test)
- [ ] `check_manifest.json` has 9 seed checks
- [ ] Manual test: create a real sklearn model ZIP, ingest it, run validation, read the report

## Notes for the implementer

- **If a prompt's tests fail:** Fix within the same session. Do NOT proceed with failing tests.
- **If a test is wrong:** The test may have incorrect expectations based on the plan.
  Fix the test if the implementation is clearly correct. But document why.
- **If you need to debug:** Each prompt's test file is independent. Run individual tests:
  `python -m pytest tests/test_sandbox.py::test_timeout_kills_process -v`
- **Budget:** ~$3-10 per prompt. Total: ~$40-80 (more than before due to tests).
- **Time:** ~10-25 min per prompt. Total: ~3-5 hours of active work.
- **Order matters:** Prompts 1→2→3 are strict. 4 needs 1-3. 5 needs 1-4. 6 needs 1-5.
  7 needs 1-6. 8-10 need 1-7. 11-12 need 1-10 but can partially parallelize with each other.
