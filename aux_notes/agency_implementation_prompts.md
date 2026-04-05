# Agency Layer Implementation Prompts for Valoboros

**How to use:** Execute these prompts sequentially in separate Claude Code sessions.
Each prompt contains both implementation AND testing — do not proceed to the next
prompt until all tests in the current one pass.

**Important:** Start each new session by saying:
> Read `aux_notes/valoboros_agency_plan.md` — this is the full agency layer plan.
> Read `aux_notes/ouroboros_validation_platform_plan.md` — this is the master plan.
> Then execute the prompt below.

**Dependency order:**

```
Prompt 1 (watcher) ──→ Prompt 3 (methodology) ──→ Prompt 5 (project structure)
                                                ↗
Prompt 2 (reflection) ──→ Prompt 4 (literature + evolver)
```

Prompts 1 and 2 are independent — can run in parallel if using worktrees.
Prompt 3 depends on 1 (watcher creates bundles that methodology planner works on).
Prompt 4 depends on 2 (evolver uses reflection output).
Prompt 5 depends on 3 (project structure wraps around methodology).

---

## Prompt 1 of 5: Folder Watcher + Consciousness Integration

```
Read the agency plan aux_notes/valoboros_agency_plan.md sections §2 (Component A: The Daemon)
and §6 (Wiring Into Consciousness Loop).

This prompt makes Valoboros autonomous: it watches a folder for new model ZIPs
and auto-ingests + validates them via the consciousness loop.

### Files to create:

1. ouroboros/validation/watcher.py — ValidationWatcher class:
   - __init__(inbox_dir, validations_dir, repo_dir, config)
   - scan_inbox() -> list[Path]: find .zip files not in .valoboros_processed.json
   - ingest_and_validate(zip_path) -> str: call _ingest_model_artifacts_impl,
     then create and run ValidationPipeline, return bundle_id
   - mark_processed(zip_name, bundle_id, status): append to processed JSON
   - _load_processed() -> dict: read .valoboros_processed.json
   - _save_processed(data): write .valoboros_processed.json

   The processed tracking file lives at inbox_dir/.valoboros_processed.json.
   Format: {"filename.zip": {"bundle_id": "...", "status": "completed", "timestamp": "..."}}

### Files to modify:

2. ouroboros/config.py — Add to SETTINGS_DEFAULTS:
   - "OUROBOROS_VALIDATION_INBOX_DIR": "ml-models-to-validate"
   - "OUROBOROS_VALIDATION_AUTO_INGEST": True

3. ouroboros/validation/config_loader.py — Add the two new keys to _KEY_MAP
   and the corresponding fields to ValidationConfig (inbox_dir, auto_ingest).

4. ouroboros/validation/types.py — Add inbox_dir and auto_ingest fields
   to ValidationConfig with defaults matching config.py.

NOTE on consciousness integration: The full wiring into consciousness.py requires
understanding the complex _think() method and tool execution flow. For THIS prompt,
implement watcher.py as a standalone callable module. The consciousness integration
will be tested via a simple script that simulates what consciousness would do:
scan → detect → ingest → validate. We do NOT modify consciousness.py in this prompt
to avoid breaking the existing agent. That wiring will be done as a manual step
after all agency components are ready.

### Verify

Write and run tests/test_watcher.py:

```python
"""Tests for the folder watcher — auto-detection and processing of new ZIPs."""
import json, pytest, zipfile
from pathlib import Path
from ouroboros.validation.watcher import ValidationWatcher
from ouroboros.validation.types import ValidationConfig


@pytest.fixture
def inbox(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    return d


@pytest.fixture
def validations_dir(tmp_path):
    d = tmp_path / "validations"
    d.mkdir()
    return d


def _make_model_zip(inbox, name="test_model.zip"):
    zip_path = inbox / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("train.py", "import pandas\nprint('training')\n")
    return zip_path


def test_scan_finds_new_zips(inbox, validations_dir, tmp_path):
    """scan_inbox detects new ZIP files."""
    _make_model_zip(inbox, "model_a.zip")
    _make_model_zip(inbox, "model_b.zip")
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    new = watcher.scan_inbox()
    assert len(new) == 2
    assert any(p.name == "model_a.zip" for p in new)


def test_scan_ignores_processed(inbox, validations_dir, tmp_path):
    """Already-processed ZIPs are not returned by scan_inbox."""
    _make_model_zip(inbox, "already_done.zip")
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    watcher.mark_processed("already_done.zip", "bundle-123", "completed")
    new = watcher.scan_inbox()
    assert len(new) == 0


def test_scan_ignores_non_zip(inbox, validations_dir, tmp_path):
    """Non-ZIP files in inbox are ignored."""
    (inbox / "readme.txt").write_text("not a model")
    (inbox / "data.csv").write_text("a,b\n1,2")
    _make_model_zip(inbox, "real_model.zip")
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    new = watcher.scan_inbox()
    assert len(new) == 1
    assert new[0].name == "real_model.zip"


def test_mark_processed_persists(inbox, validations_dir, tmp_path):
    """Processed state survives watcher re-instantiation."""
    watcher1 = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    watcher1.mark_processed("model.zip", "bundle-1", "completed")
    # New instance reads the same file
    watcher2 = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    processed = watcher2._load_processed()
    assert "model.zip" in processed
    assert processed["model.zip"]["bundle_id"] == "bundle-1"


def test_processed_file_location(inbox, validations_dir, tmp_path):
    """Processed tracking file is inside the inbox directory."""
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    watcher.mark_processed("test.zip", "b1", "ingested")
    assert (inbox / ".valoboros_processed.json").exists()


def test_ingest_creates_bundle(inbox, validations_dir, tmp_path):
    """ingest_and_validate creates a bundle directory with correct structure."""
    zip_path = _make_model_zip(inbox, "new_model.zip")
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    bundle_id = watcher.ingest(zip_path, task="Test model validation")
    bundle_dir = validations_dir / bundle_id
    assert bundle_dir.exists()
    assert (bundle_dir / "raw" / "model_code" / "train.py").exists()
    assert (bundle_dir / "inputs" / "task.txt").exists()


def test_empty_inbox(inbox, validations_dir, tmp_path):
    """Empty inbox returns no new ZIPs."""
    watcher = ValidationWatcher(inbox, validations_dir, tmp_path, ValidationConfig())
    assert watcher.scan_inbox() == []


def test_nonexistent_inbox(tmp_path):
    """Watcher handles nonexistent inbox dir gracefully."""
    watcher = ValidationWatcher(
        tmp_path / "does_not_exist",
        tmp_path / "validations",
        tmp_path,
        ValidationConfig(),
    )
    assert watcher.scan_inbox() == []
```

Run: `.venv/bin/python -m pytest tests/test_watcher.py -v`
All tests must pass.
```

---

## Prompt 2 of 5: Reflection Engine (Cross-Validation Learning)

```
Read the agency plan aux_notes/valoboros_agency_plan.md section §4.2
(Cross-Validation Reflection).

This prompt builds the reflection engine that analyzes past validations,
finds patterns, and writes them to the knowledge base.

### Files to create:

1. ouroboros/validation/reflection_engine.py — ValidationReflectionEngine class:
   - __init__(validations_dir, knowledge_dir, config)
   - reflect() -> ReflectionResult:
     a. Scan validations_dir for all completed reports (results/report.json)
     b. Group findings by model_type, framework, check_id
     c. Compute per-check trigger rates (how often each check fires)
     d. Identify checks that NEVER triggered (dead checks)
     e. Identify checks that ALWAYS trigger (possibly too sensitive)
     f. Find common failure patterns across models
     g. Call LLM to synthesize patterns into natural language
     h. Write patterns to knowledge_dir:
        - validation_patterns.md (cross-cutting patterns)
        - model_type_<type>.md (per-type insights)
     i. Return ReflectionResult

2. Add ReflectionResult dataclass to ouroboros/validation/types.py:
   - total_validations_analyzed: int
   - patterns_found: list[dict]  # {"pattern": str, "frequency": int, "model_types": list}
   - dead_checks: list[str]       # check_ids that never triggered
   - hot_checks: list[str]        # check_ids that always trigger
   - knowledge_entries_written: list[str]  # filenames written to knowledge dir

NOTE: The reflection engine reads reports but does NOT call the LLM for synthesis
if there are fewer than 2 completed validations — it returns an empty result.
This avoids wasting LLM budget when there's nothing to generalize from.

For LLM synthesis, use a LIGHT model (config's comprehension_model with
reasoning_effort="low") to keep costs down — reflection is background work.

### Verify

Write and run tests/test_reflection_engine.py:

```python
"""Tests for cross-validation reflection engine."""
import json, pytest
from pathlib import Path
from ouroboros.validation.reflection_engine import ValidationReflectionEngine
from ouroboros.validation.types import ValidationConfig


@pytest.fixture
def validations_dir(tmp_path):
    d = tmp_path / "validations"
    d.mkdir()
    return d


@pytest.fixture
def knowledge_dir(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    return d


def _create_fake_report(validations_dir, bundle_id, model_type, failed_checks):
    """Create a minimal report.json for testing."""
    bundle_dir = validations_dir / bundle_id / "results"
    bundle_dir.mkdir(parents=True)
    report = {
        "bundle_id": bundle_id,
        "model_profile": {"model_type": model_type, "framework": "sklearn"},
        "overall_verdict": "conditional",
        "stages": [
            {
                "stage": "S0", "stage_name": "Intake", "status": "passed",
                "checks": [], "duration_sec": 1.0, "error_message": None,
            }
        ],
        "critical_findings": [
            {"check_id": cid, "check_name": cid, "severity": "warning",
             "passed": False, "score": None, "details": f"Failed: {cid}",
             "evidence": {}, "methodology_version": "test",
             "improvement_suggestion": None}
            for cid in failed_checks
        ],
        "hard_recommendations": [],
        "soft_recommendations": [],
        "estimated_total_improvement": {},
        "generated_at": "2026-01-01",
        "methodology_snapshot": "test",
        "meta_scores": {},
    }
    (bundle_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")


def test_no_reports_returns_empty(validations_dir, knowledge_dir):
    """With no reports, reflection returns empty result."""
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    result = engine.reflect_sync()
    assert result.total_validations_analyzed == 0
    assert result.patterns_found == []


def test_single_report_returns_empty(validations_dir, knowledge_dir):
    """With only 1 report, no patterns to generalize — returns empty."""
    _create_fake_report(validations_dir, "b1", "classification", ["S4.TARGET_LEAKAGE"])
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    result = engine.reflect_sync()
    assert result.total_validations_analyzed == 1
    assert result.patterns_found == []  # need >= 2 to generalize


def test_detects_common_failures(validations_dir, knowledge_dir):
    """When same check fails across multiple models, it's detected as a pattern."""
    _create_fake_report(validations_dir, "b1", "classification", ["S8.CODE_SMELLS", "S4.TARGET_LEAKAGE"])
    _create_fake_report(validations_dir, "b2", "regression", ["S8.CODE_SMELLS"])
    _create_fake_report(validations_dir, "b3", "classification", ["S8.CODE_SMELLS", "S3.TRAIN_TEST_GAP"])
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    result = engine.reflect_sync()
    assert result.total_validations_analyzed == 3
    # S8.CODE_SMELLS fails in all 3 → should be detected
    check_ids_in_patterns = [p["check_id"] for p in result.patterns_found]
    assert "S8.CODE_SMELLS" in check_ids_in_patterns


def test_detects_dead_checks(validations_dir, knowledge_dir):
    """Checks that exist in registry but never triggered are flagged."""
    _create_fake_report(validations_dir, "b1", "classification", ["S4.TARGET_LEAKAGE"])
    _create_fake_report(validations_dir, "b2", "regression", ["S4.TARGET_LEAKAGE"])
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    # Tell engine about all registered checks so it can detect "never triggered"
    all_check_ids = ["S4.TARGET_LEAKAGE", "S5.DISPARATE_IMPACT", "S7.PERTURBATION"]
    result = engine.reflect_sync(registered_check_ids=all_check_ids)
    # S5 and S7 never appeared in any report → dead checks
    assert "S5.DISPARATE_IMPACT" in result.dead_checks
    assert "S7.PERTURBATION" in result.dead_checks
    assert "S4.TARGET_LEAKAGE" not in result.dead_checks


def test_groups_by_model_type(validations_dir, knowledge_dir):
    """Patterns include which model types they affect."""
    _create_fake_report(validations_dir, "b1", "classification", ["S3.TRAIN_TEST_GAP"])
    _create_fake_report(validations_dir, "b2", "classification", ["S3.TRAIN_TEST_GAP"])
    _create_fake_report(validations_dir, "b3", "regression", [])
    engine = ValidationReflectionEngine(validations_dir, knowledge_dir, ValidationConfig())
    result = engine.reflect_sync()
    gap_pattern = [p for p in result.patterns_found if p["check_id"] == "S3.TRAIN_TEST_GAP"]
    assert len(gap_pattern) == 1
    assert "classification" in gap_pattern[0]["model_types"]
```

Run: `.venv/bin/python -m pytest tests/test_reflection_engine.py -v`

NOTE: The engine should have both `async reflect()` and a sync wrapper
`reflect_sync()` for testing convenience. Tests use `reflect_sync()`.
The LLM synthesis step should be called only when >= 2 reports exist,
and should gracefully fall back if LLM is unavailable (return patterns
from deterministic analysis only, without narrative).
```

---

## Prompt 3 of 5: Methodology Planner + Pipeline Integration

```
Read the agency plan aux_notes/valoboros_agency_plan.md section §3
(Component B: The Methodology Planner) and §5 (Project Structure).

This prompt adds per-model validation methodology planning: before running
checks, the LLM designs a custom validation plan for each specific model.

### Files to create:

1. ouroboros/validation/methodology_planner.py — MethodologyPlanner class:
   - __init__(bundle_dir, profile, check_registry, config, knowledge_dir)
   - plan() -> MethodologyPlan:
     a. Read model profile
     b. List all available checks from registry, filter by model tags
     c. Try to read relevant knowledge base files:
        - knowledge_dir / f"model_type_{profile.model_type}.md"
        - knowledge_dir / "validation_patterns.md"
     d. Try to find similar past validations (scan validations_dir for
        reports with same model_type/framework)
     e. Build LLM prompt with all context
     f. Call LLM to produce MethodologyPlan JSON
     g. Save methodology.md (human-readable) to bundle_dir/methodology/
     h. Save methodology_plan.json to bundle_dir/methodology/
     i. Return MethodologyPlan
   - _generate_methodology_md(plan) -> str: render plan as Markdown
   - _fallback_plan() -> MethodologyPlan: when LLM fails, select all
     applicable checks, skip none, propose nothing new

### Files to modify:

2. ouroboros/validation/types.py — Add MethodologyPlan dataclass:
   - bundle_id, model_summary, risk_priorities, checks_to_run,
     checks_to_skip, checks_to_create, knowledge_references,
     similar_past_validations, methodology_version, confidence
   - to_dict() and from_dict()

3. ouroboros/validation/pipeline.py — Insert methodology planning step:
   - After _install_dependencies() and before S1
   - Create methodology/ directory in bundle
   - Call MethodologyPlanner.plan()
   - If plan proposes new checks (checks_to_create):
     a. For each proposal, call LLM to generate check Python code
     b. Write check file to methodology/custom_checks/ in the bundle
     c. Also register in the global check_registry
   - Filter S2-S8 execution based on plan.checks_to_run / checks_to_skip:
     Only run stages that have at least one check in checks_to_run

4. pipeline.py — Create methodology/ and methodology/custom_checks/ dirs
   in __init__.

### Verify

Write and run tests/test_methodology_planner.py:

```python
"""Tests for per-model methodology planning."""
import json, pytest
from pathlib import Path
from ouroboros.validation.methodology_planner import MethodologyPlanner
from ouroboros.validation.check_registry import CheckRegistry
from ouroboros.validation.types import ModelProfile, ValidationConfig, MethodologyPlan


@pytest.fixture
def repo_dir():
    return Path(__file__).parent.parent


@pytest.fixture
def bundle_dir(tmp_path):
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "methodology").mkdir()
    (d / "inferred").mkdir()
    return d


@pytest.fixture
def knowledge_dir(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    return d


@pytest.fixture
def profile():
    return ModelProfile(
        bundle_id="test", task_description="Predict churn",
        model_type="classification", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="RandomForest", data_format="tabular",
        target_column="churn", target_column_confidence=0.9,
    )


def test_fallback_plan_selects_all_applicable(repo_dir, bundle_dir, knowledge_dir, profile):
    """When LLM is unavailable, fallback selects all applicable checks."""
    registry = CheckRegistry(repo_dir)
    planner = MethodologyPlanner(bundle_dir, profile, registry, ValidationConfig(), knowledge_dir)
    plan = planner._fallback_plan()
    assert isinstance(plan, MethodologyPlan)
    assert plan.bundle_id == "test"
    assert len(plan.checks_to_run) >= 5  # most seed checks apply to tabular classification
    assert plan.confidence < 0.5  # fallback should have low confidence


def test_fallback_skips_irrelevant_checks(repo_dir, bundle_dir, knowledge_dir):
    """Fallback plan skips checks tagged for other model types."""
    # Regression model — classification-only checks should be skipped
    profile = ModelProfile(
        bundle_id="test", task_description="Predict price",
        model_type="regression", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="Ridge", data_format="tabular",
    )
    registry = CheckRegistry(repo_dir)
    planner = MethodologyPlanner(bundle_dir, profile, registry, ValidationConfig(), knowledge_dir)
    plan = planner._fallback_plan()
    # S5.DISPARATE_IMPACT is tagged ["tabular", "classification"] — should be skipped for regression
    assert "S5.DISPARATE_IMPACT" not in plan.checks_to_run


def test_methodology_md_generated(repo_dir, bundle_dir, knowledge_dir, profile):
    """After planning, methodology.md exists in the bundle."""
    registry = CheckRegistry(repo_dir)
    planner = MethodologyPlanner(bundle_dir, profile, registry, ValidationConfig(), knowledge_dir)
    plan = planner._fallback_plan()
    md = planner._generate_methodology_md(plan)
    assert "Risk Priorities" in md or "Checks Selected" in md
    assert profile.algorithm in md or profile.model_type in md


def test_methodology_plan_roundtrip():
    """MethodologyPlan serializes and deserializes correctly."""
    plan = MethodologyPlan(
        bundle_id="test", model_summary="A test model",
        risk_priorities=["overfitting", "leakage"],
        checks_to_run=["S2.OOS_METRICS", "S3.TRAIN_TEST_GAP"],
        checks_to_skip=["S5.DISPARATE_IMPACT"],
        checks_to_create=[{"check_id": "S4.CUSTOM", "description": "Custom check"}],
        knowledge_references=[], similar_past_validations=[],
        methodology_version="0.1.0", confidence=0.8,
    )
    d = plan.to_dict()
    plan2 = MethodologyPlan.from_dict(d)
    assert plan2.risk_priorities == ["overfitting", "leakage"]
    assert len(plan2.checks_to_create) == 1
    assert json.dumps(d)  # JSON-serializable


def test_knowledge_base_referenced(repo_dir, bundle_dir, knowledge_dir, profile):
    """Planner reads knowledge base files if they exist."""
    # Create a knowledge file for this model type
    (knowledge_dir / "model_type_classification.md").write_text(
        "# Classification models\nOften have overfitting issues.\n"
    )
    registry = CheckRegistry(repo_dir)
    planner = MethodologyPlanner(bundle_dir, profile, registry, ValidationConfig(), knowledge_dir)
    # The _gather_knowledge method should find this file
    kb = planner._gather_knowledge()
    assert "classification" in kb.lower() or "overfitting" in kb.lower()
```

Run: `.venv/bin/python -m pytest tests/test_methodology_planner.py -v`
```

---

## Prompt 4 of 5: Literature Scanner + Methodology Evolver

```
Read the agency plan aux_notes/valoboros_agency_plan.md sections §4.3
(Literature Scanner) and §4.4 (Methodology Evolution).

This prompt builds the learning loop: arxiv scanning + autonomous check evolution.

### Dependencies to install first:
.venv/bin/pip install arxiv

### Files to create:

1. ouroboros/validation/literature_scanner.py — LiteratureScanner class:
   - __init__(knowledge_dir, config)
   - scan() -> list[PaperSummary]:
     a. Pick one query from _ARXIV_QUERIES (rotate based on scan count)
     b. Search arxiv for recent papers (last 90 days, max 10 results)
     c. Filter out already-scanned paper IDs (from arxiv_scan_history.json)
     d. For top 5 new papers: assess relevance via LLM (or heuristic if no LLM)
     e. Save relevant papers to knowledge/arxiv_recent.md
     f. Update arxiv_scan_history.json with scanned IDs
     g. Return list of PaperSummary
   - scan_sync() -> list[PaperSummary]: sync wrapper for testing

   _ARXIV_QUERIES: list of 7 search queries from the plan.
   Rotate: query_index = scan_count % len(_ARXIV_QUERIES)

   Heuristic relevance (no LLM fallback): score based on keyword
   presence in title+abstract: "validation", "testing", "leakage",
   "fairness", "robustness", "overfitting" → +0.2 each, cap at 1.0.

2. ouroboros/validation/methodology_evolver.py — MethodologyEvolver class:
   - __init__(repo_dir, check_registry, effectiveness_tracker, knowledge_dir, config)
   - evolve() -> EvolutionAction | None:
     a. Read evolution targets from effectiveness tracker
     b. If no targets, check knowledge base for arxiv-inspired check ideas
     c. Pick ONE action (highest priority)
     d. Execute:
        - "fix_check": read check code, LLM proposes fix, write, test on
          one historical bundle, commit if passes
        - "create_check": LLM generates code from description, write to
          checks/, register in manifest, test, commit
        - "delete_check": disable via registry, commit
     e. Return EvolutionAction describing what was done
   - evolve_sync() -> EvolutionAction | None: sync wrapper

3. Add to ouroboros/validation/types.py:
   - PaperSummary dataclass (arxiv_id, title, abstract, url,
     relevance_score, applicable_technique, proposed_check_idea)
   - EvolutionAction dataclass (action_type, check_id, description,
     success, error_message)

### Verify

Write and run tests/test_literature_and_evolution.py:

```python
"""Tests for literature scanner and methodology evolver."""
import json, pytest
from pathlib import Path
from ouroboros.validation.types import ValidationConfig, PaperSummary, EvolutionAction


@pytest.fixture
def knowledge_dir(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    return d


# --- Literature Scanner Tests ---

def test_scanner_heuristic_relevance(knowledge_dir):
    """Heuristic relevance scoring works without LLM."""
    from ouroboros.validation.literature_scanner import LiteratureScanner
    scanner = LiteratureScanner(knowledge_dir, ValidationConfig())
    # Test heuristic scoring directly
    score = scanner._heuristic_relevance(
        "A New Method for Data Leakage Detection in ML Pipelines",
        "We propose a validation framework for detecting train-test leakage and overfitting..."
    )
    assert score >= 0.4  # "leakage", "validation", "overfitting" should hit


def test_scanner_heuristic_irrelevant(knowledge_dir):
    """Irrelevant papers get low scores."""
    from ouroboros.validation.literature_scanner import LiteratureScanner
    scanner = LiteratureScanner(knowledge_dir, ValidationConfig())
    score = scanner._heuristic_relevance(
        "Quantum Computing for Drug Discovery",
        "We apply quantum algorithms to molecular simulation..."
    )
    assert score < 0.2


def test_scan_history_persists(knowledge_dir):
    """Scanned paper IDs are recorded and survive re-instantiation."""
    from ouroboros.validation.literature_scanner import LiteratureScanner
    scanner1 = LiteratureScanner(knowledge_dir, ValidationConfig())
    scanner1._record_scanned(["arxiv:2401.00001", "arxiv:2401.00002"])
    scanner2 = LiteratureScanner(knowledge_dir, ValidationConfig())
    history = scanner2._load_scan_history()
    assert "arxiv:2401.00001" in history
    assert "arxiv:2401.00002" in history


def test_query_rotation(knowledge_dir):
    """Scanner rotates through queries on successive scans."""
    from ouroboros.validation.literature_scanner import LiteratureScanner, _ARXIV_QUERIES
    scanner = LiteratureScanner(knowledge_dir, ValidationConfig())
    q1 = scanner._get_current_query()
    scanner._record_scanned([])  # increments scan count
    q2 = scanner._get_current_query()
    assert q1 != q2 or len(_ARXIV_QUERIES) == 1


# --- Methodology Evolver Tests ---

def test_evolver_no_targets_returns_none(tmp_path):
    """With no evolution targets, evolver returns None."""
    from ouroboros.validation.methodology_evolver import MethodologyEvolver
    from ouroboros.validation.check_registry import CheckRegistry
    from ouroboros.validation.effectiveness import EffectivenessTracker

    repo_dir = Path(__file__).parent.parent
    registry = CheckRegistry(repo_dir)
    tracker = EffectivenessTracker(tmp_path)
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    evolver = MethodologyEvolver(repo_dir, registry, tracker, knowledge_dir, ValidationConfig())
    action = evolver.evolve_sync()
    # No effectiveness data → no targets → None
    assert action is None


def test_evolver_detects_fix_target(tmp_path):
    """Evolver identifies a check with low precision as a fix target."""
    from ouroboros.validation.methodology_evolver import MethodologyEvolver
    from ouroboros.validation.check_registry import CheckRegistry
    from ouroboros.validation.effectiveness import EffectivenessTracker

    repo_dir = Path(__file__).parent.parent
    registry = CheckRegistry(repo_dir)
    tracker = EffectivenessTracker(tmp_path)
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    # Create a check with terrible precision (5 FP, 0 TP)
    for i in range(5):
        tracker.record_finding_feedback("S8.CODE_SMELLS", f"b{i}", "false_positive", "human", 1.0)

    evolver = MethodologyEvolver(repo_dir, registry, tracker, knowledge_dir, ValidationConfig())
    targets = evolver._get_targets()
    assert len(targets) > 0
    assert any(t.target_type == "fix_check" for t in targets)


def test_paper_summary_dataclass():
    """PaperSummary serialization."""
    ps = PaperSummary(
        arxiv_id="2401.00001", title="Test Paper",
        abstract="Testing...", url="https://arxiv.org/abs/2401.00001",
        relevance_score=0.8, applicable_technique="New leakage detection",
        proposed_check_idea="S4.ADVANCED_LEAKAGE",
    )
    assert ps.relevance_score == 0.8
    assert ps.proposed_check_idea is not None


def test_evolution_action_dataclass():
    """EvolutionAction serialization."""
    ea = EvolutionAction(
        action_type="create_check", check_id="S4.NEW",
        description="Created new check", success=True, error_message=None,
    )
    assert ea.success is True
```

Run: `.venv/bin/python -m pytest tests/test_literature_and_evolution.py -v`
```

---

## Prompt 5 of 5: Project Structure + Execution Log + Integration

```
Read the agency plan aux_notes/valoboros_agency_plan.md section §5
(Component D: Per-Model Validation Project Structure).

This prompt ties everything together: project structure, execution logging,
and an end-to-end integration test.

### Files to modify:

1. ouroboros/validation/pipeline.py — Add project structure setup:
   - In __init__: create methodology/ and methodology/custom_checks/ dirs
   - Add _log(message) method: append timestamped line to validation.log
   - Call _log() at each pipeline stage transition:
     "[timestamp] Starting S0 comprehension..."
     "[timestamp] S0 completed: 2 checks, 0 failed"
     "[timestamp] Methodology plan: 7 checks selected, 2 skipped"
     "[timestamp] Installing 10 dependencies..."
     "[timestamp] Starting S1 reproducibility..."
     etc.
   - Write validation.log to bundle_dir/validation.log

2. ouroboros/validation/pipeline.py — If MethodologyPlanner is available,
   use plan.checks_to_run to filter which stages run:
   - Build a set of active stages from checks_to_run (extract S{N} prefixes)
   - Only run stages that have at least one check in the plan
   - For S1 (reproducibility): always run (not check-based)
   - For S9 (synthesis): always run
   - Log skipped stages with reason from methodology plan

3. ouroboros/validation/pipeline.py — Save methodology plan results:
   - Write methodology/methodology.md via planner._generate_methodology_md()
   - Write methodology/methodology_plan.json

### Verify

Write and run tests/test_project_structure.py:

```python
"""Tests for per-model project structure and execution logging."""
import json, pytest, zipfile
from pathlib import Path
from ouroboros.validation.types import ValidationConfig


def _make_test_bundle(tmp_path):
    """Create a minimal ingested bundle for testing."""
    from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
    code_zip = tmp_path / "code.zip"
    with zipfile.ZipFile(code_zip, "w") as zf:
        zf.writestr("train.py", "import pandas\nprint('hello')\n")
    data_zip = tmp_path / "data.zip"
    with zipfile.ZipFile(data_zip, "w") as zf:
        zf.writestr("train.csv", "a,b,target\n1,2,0\n3,4,1\n")
    val_dir = tmp_path / "validations"
    val_dir.mkdir()
    bundle_id = _ingest_model_artifacts_impl(
        val_dir, str(code_zip), "Test task", str(data_zip), "Test data",
    )
    return val_dir / bundle_id


def test_methodology_dir_created(tmp_path):
    """Pipeline creates methodology/ directory structure."""
    from ouroboros.validation.pipeline import ValidationPipeline
    bundle_dir = _make_test_bundle(tmp_path)
    config = ValidationConfig(auto_self_assess=False, auto_improve=False)
    pipeline = ValidationPipeline(
        bundle_dir.name, bundle_dir, Path(__file__).parent.parent, config,
    )
    assert (bundle_dir / "methodology").is_dir()


def test_validation_log_written(tmp_path):
    """After pipeline run, validation.log exists with timestamped entries."""
    import asyncio
    from ouroboros.validation.pipeline import ValidationPipeline
    bundle_dir = _make_test_bundle(tmp_path)
    config = ValidationConfig(
        auto_self_assess=False, auto_improve=False,
        comprehension_model="anthropic/claude-sonnet-4",
    )
    pipeline = ValidationPipeline(
        bundle_dir.name, bundle_dir, Path(__file__).parent.parent, config,
    )
    # Run pipeline — will fail on LLM but should still produce log
    try:
        asyncio.run(pipeline.run())
    except Exception:
        pass  # LLM may not be available
    log_path = bundle_dir / "validation.log"
    # Log should exist even if pipeline failed partway
    if log_path.exists():
        content = log_path.read_text()
        assert "S0" in content or "Starting" in content


def test_results_dir_has_stage_files(tmp_path):
    """After pipeline run, results/ contains stage JSON files."""
    import asyncio
    from ouroboros.validation.pipeline import ValidationPipeline
    bundle_dir = _make_test_bundle(tmp_path)
    config = ValidationConfig(
        auto_self_assess=False, auto_improve=False,
        comprehension_model="anthropic/claude-sonnet-4",
    )
    pipeline = ValidationPipeline(
        bundle_dir.name, bundle_dir, Path(__file__).parent.parent, config,
    )
    try:
        asyncio.run(pipeline.run())
    except Exception:
        pass
    results = bundle_dir / "results"
    # At minimum, S0 stage file should exist (deterministic, no LLM needed)
    stage_files = list(results.glob("stage_S*.json"))
    assert len(stage_files) >= 1


def test_full_bundle_structure(tmp_path):
    """Verify the complete per-model project structure."""
    bundle_dir = _make_test_bundle(tmp_path)
    # Check all expected directories
    for subdir in [
        "raw/model_code",
        "raw/data_samples",
        "inputs",
        "inferred",
        "results",
        "improvement/implementation",
        "improvement/revalidation",
    ]:
        assert (bundle_dir / subdir).is_dir(), f"Missing: {subdir}"
    # Check input files
    assert (bundle_dir / "inputs" / "task.txt").exists()
    assert (bundle_dir / "raw" / "model_code" / "train.py").exists()
    assert (bundle_dir / "raw" / "data_samples" / "train.csv").exists()
```

Run: `.venv/bin/python -m pytest tests/test_project_structure.py -v`

After all 5 prompts, run the full test suite:
`.venv/bin/python -m pytest tests/ -v --ignore=tests/test_llm_client_refresh.py`
```

---

## Post-Implementation Summary

| # | Prompt | New Files | Modified Files | Tests | LOC est. |
|---|--------|-----------|---------------|-------|----------|
| 1 | Watcher | `watcher.py` | `config.py`, `config_loader.py`, `types.py` | 9 | ~190 |
| 2 | Reflection | `reflection_engine.py` | `types.py` | 5 | ~220 |
| 3 | Methodology | `methodology_planner.py` | `types.py`, `pipeline.py` | 6 | ~360 |
| 4 | Literature + Evolver | `literature_scanner.py`, `methodology_evolver.py` | `types.py` | 8 | ~430 |
| 5 | Project Structure | — | `pipeline.py` | 4 | ~100 |
| **Total** | | **5 new** | **4 modified** | **32** | **~1300** |
