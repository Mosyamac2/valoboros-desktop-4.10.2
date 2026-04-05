# Valoboros Agency Layer: Daemon, Methodology Planner, Autonomous Learner

**Date:** 2026-04-05  
**Status:** PLAN ONLY — do not implement yet  
**Target audience:** Senior Python developer  
**Base:** Current Valoboros codebase (77 tests, 12 prompts + improvements 0+A)

---

## 0. Goal

Transform Valoboros from a manually-called pipeline into a **continuously running
autonomous agent** that:
1. Watches a folder for new model ZIPs and auto-validates them
2. Plans a custom validation methodology per model before running checks
3. Generates new validation code (checks, tools) when needed
4. Reflects on past validations and learns from academic literature between jobs
5. Saves everything — methodology, code, results, reports — as a self-contained
   validation project per model

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                    BackgroundConsciousness                        │
│            (existing ouroboros/consciousness.py)                  │
│                                                                  │
│  Wakeup cycle:                                                   │
│    ┌─────────┐   ┌──────────┐   ┌──────────┐   ┌─────────────┐ │
│    │ Check   │   │Reflect on│   │ Search   │   │ Evolve      │ │
│    │ inbox   │──▶│ past     │──▶│ arxiv    │──▶│ methodology │ │
│    │ folder  │   │ models   │   │ for new  │   │ (create     │ │
│    └────┬────┘   └──────────┘   │ papers   │   │  checks)    │ │
│         │                       └──────────┘   └─────────────┘ │
│         ▼                                                       │
│    New ZIP found?                                               │
│    ┌────────────────────────────────────────────────────────┐   │
│    │              Validation Job                             │   │
│    │  1. Ingest ZIP                                         │   │
│    │  2. S0 Comprehension + dep extraction                  │   │
│    │  3. Methodology Planner (NEW) → methodology.md         │   │
│    │  4. Generate/select checks per plan                    │   │
│    │  5. Install deps → S1-S9 pipeline                      │   │
│    │  6. Retry failed checks (adjust code)                  │   │
│    │  7. Save results + report to project folder            │   │
│    │  8. Self-assessment + effectiveness recording          │   │
│    └────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Component A: The Daemon (Folder Watcher)

### 2.1. What to build

**New file:** `ouroboros/validation/watcher.py`

A background service that monitors a configurable inbox folder for new `.zip` files
and queues them for validation.

### 2.2. Design

```python
class ValidationWatcher:
    """Watches an inbox folder for new model ZIPs and triggers validation."""

    def __init__(
        self,
        inbox_dir: Path,           # e.g., ml-models-to-validate/
        validations_dir: Path,     # e.g., ~/Ouroboros/data/validations/
        repo_dir: Path,
        config: ValidationConfig,
        on_new_model: Callable,    # callback when a new model is found
    ):
        self._inbox_dir = inbox_dir
        self._validations_dir = validations_dir
        self._repo_dir = repo_dir
        self._config = config
        self._on_new_model = on_new_model
        self._processed_file = inbox_dir / ".valoboros_processed.json"
        self._observer = None

    def start(self) -> None:
        """Start watching. Two modes:
        1. Polling (simple): check folder every N seconds
        2. watchdog (efficient): OS-level file events
        Use polling as default, watchdog if installed.
        """
        ...

    def stop(self) -> None: ...

    def _scan_inbox(self) -> list[Path]:
        """Find new .zip files not yet in processed list."""
        processed = self._load_processed()
        new_zips = []
        for f in sorted(self._inbox_dir.glob("*.zip")):
            if f.name not in processed:
                new_zips.append(f)
        return new_zips

    def _mark_processed(self, zip_path: Path, bundle_id: str, status: str) -> None:
        """Record that a ZIP has been ingested. Prevents re-processing."""
        processed = self._load_processed()
        processed[zip_path.name] = {
            "bundle_id": bundle_id,
            "status": status,   # "ingested" | "validating" | "completed" | "failed"
            "timestamp": utc_now_iso(),
        }
        self._save_processed(processed)

    def _load_processed(self) -> dict: ...
    def _save_processed(self, data: dict) -> None: ...
```

### 2.3. Integration with consciousness

The watcher does NOT run as a separate daemon. Instead, it integrates into the existing
`BackgroundConsciousness._loop()` cycle:

**File:** `ouroboros/consciousness.py`

```python
# In the _think() method, add as the FIRST check before other tasks:

def _think(self):
    # 1. Check inbox for new models (HIGHEST PRIORITY)
    new_models = self._check_validation_inbox()
    if new_models:
        self._process_new_model(new_models[0])  # one per wakeup cycle
        return  # don't do other consciousness tasks this cycle

    # 2. Existing consciousness tasks (effectiveness review, etc.)
    ...
```

This approach:
- Reuses the existing consciousness loop (no new daemon process)
- Respects the pause/resume mechanism (pauses during active tasks)
- Uses the existing budget tracking
- Doesn't require `watchdog` as a dependency (simple polling is fine at 5-min intervals)

### 2.4. Config

**Add to `ouroboros/config.py` → `SETTINGS_DEFAULTS`:**

| Key | Default | Description |
|-----|---------|-------------|
| `OUROBOROS_VALIDATION_INBOX_DIR` | `"ml-models-to-validate"` | Folder to watch for new ZIPs (relative to project root or absolute) |
| `OUROBOROS_VALIDATION_AUTO_INGEST` | `True` | Whether consciousness auto-ingests new ZIPs |

### 2.5. Processed tracking file

`ml-models-to-validate/.valoboros_processed.json`:

```json
{
  "Модель досрочного погашения (EAR CL).zip": {
    "bundle_id": "d6ac58b2-44a",
    "status": "completed",
    "timestamp": "2026-04-05T12:00:00Z"
  }
}
```

This is a simple JSON file (not JSONL) — one entry per ZIP. It lives in the inbox
folder so the watcher knows what's already been processed without scanning the
validations directory.

### 2.6. Estimated effort

~150 LOC for `watcher.py` + ~40 LOC to wire into `consciousness.py`

---

## 3. Component B: The Methodology Planner

### 3.1. What to build

**New file:** `ouroboros/validation/methodology_planner.py`

A new stage that runs AFTER S0 comprehension but BEFORE the validation pipeline.
It produces a per-model `methodology.md` — a custom validation plan that:
- Identifies which risk areas matter most for THIS model type
- Selects existing checks to run (and which to skip)
- Proposes NEW checks to create if existing ones are insufficient
- References patterns learned from previous similar validations
- Cites relevant knowledge base entries

### 3.2. Design

```python
class MethodologyPlanner:
    """Creates a custom validation methodology for each model."""

    def __init__(
        self,
        bundle_dir: Path,
        profile: ModelProfile,
        check_registry: CheckRegistry,
        config: ValidationConfig,
        knowledge_dir: Path,       # ~/Ouroboros/data/memory/knowledge/
    ):
        ...

    async def plan(self) -> MethodologyPlan:
        """
        1. Read model profile (model type, framework, domain, data format)
        2. Query knowledge base for patterns related to this model type
           (knowledge_read "model_type_<type>.md", "validation_patterns.md")
        3. List available checks and filter by model profile tags
        4. Query effectiveness data: which checks work well for this model type?
        5. Call LLM with all the above to produce a methodology plan
        6. Save methodology.md to the bundle folder
        7. Return structured plan
        """
        ...
```

### 3.3. MethodologyPlan dataclass

**Add to `ouroboros/validation/types.py`:**

```python
@dataclass
class MethodologyPlan:
    bundle_id: str
    model_summary: str              # one-paragraph summary of what the model is
    risk_priorities: list[str]      # ordered list: ["temporal_leakage", "overfitting", ...]
    checks_to_run: list[str]        # check_ids selected for this model
    checks_to_skip: list[str]       # check_ids deliberately skipped (with reasons)
    checks_to_create: list[dict]    # proposals: {"check_id": ..., "description": ..., "rationale": ...}
    knowledge_references: list[str] # knowledge base topics consulted
    similar_past_validations: list[str]  # bundle_ids of similar past models
    methodology_version: str
    confidence: float               # LLM's confidence in this plan
```

### 3.4. LLM prompt structure

```
You are a model validation methodology expert.

## Model Under Validation
{model_profile summary}

## Available Checks
{list of checks with tags, descriptions, and effectiveness stats}

## Knowledge Base — Relevant Patterns
{knowledge entries for this model type/framework/domain}

## Past Validations of Similar Models
{summaries of past validation reports for similar models}

## Instructions
Design a validation methodology for this specific model. Produce:
1. Risk priorities — which risk areas matter most for this model type?
   (temporal leakage, overfitting, fairness, data quality, etc.)
2. Checks to run — select from available checks (by check_id)
3. Checks to skip — which available checks are irrelevant? Why?
4. New checks needed — are there risks not covered by existing checks?
   For each, describe: what to check, why, and a rough implementation idea.
5. Confidence — how confident are you in this plan? (0-1)

Return as JSON matching the MethodologyPlan schema.
```

### 3.5. Where it fits in the pipeline

```
Current:  S0 → install deps → S1 → S2-S8 → S9
New:      S0 → install deps → METHODOLOGY PLANNER → S1 → S2-S8 (per plan) → S9
```

**File:** `ouroboros/validation/pipeline.py` — insert between dep install and S1:

```python
# After _install_dependencies(profile):

# --- Methodology planning ---
from ouroboros.validation.methodology_planner import MethodologyPlanner
planner = MethodologyPlanner(
    self._bundle_dir, profile, self._check_registry,
    self._config, self._bundle_dir.parent.parent / "memory" / "knowledge",
)
methodology = await planner.plan()

# Use methodology to filter stages
# If methodology says skip S5 (fairness) — skip it
# If methodology says create a new check — create it via check_registry
```

### 3.6. New check auto-generation

When the methodology planner proposes a new check, the pipeline should **actually create it**:

```python
for proposal in methodology.checks_to_create:
    # Call LLM to generate the check code
    code = await self._generate_check_code(proposal)
    # Register it via CheckRegistry
    self._check_registry.add_check(ValidationCheck(
        check_id=proposal["check_id"],
        stage=proposal.get("stage", "S8"),
        name=proposal["description"][:80],
        ...
        implementation_path=f"checks/{proposal['check_id'].lower().replace('.', '_')}.py",
    ))
    # Write the code file
    check_file = self._repo_dir / "ouroboros" / "validation" / "checks" / f"{...}.py"
    check_file.write_text(code)
```

This is the **self-creation** aspect: Valoboros writes new validation code for each
model that needs it, tests it, and adds it to its repertoire.

### 3.7. Methodology document (human-readable)

Saved as `<bundle_dir>/methodology.md`:

```markdown
# Validation Methodology: EAR Consumer Loans Model

## Model Summary
CatBoost regression model predicting early repayment rates for consumer loans...

## Risk Priorities (ordered)
1. **Temporal leakage** — HIGH: model uses time-series data, temporal split critical
2. **Overfitting** — MEDIUM: 100K rows, 29 features, CatBoost with depth=10
3. **Feature sensitivity** — MEDIUM: financial features may have counterintuitive effects
4. **Data quality** — LOW: data appears clean from S0 analysis

## Checks Selected
- S0.CODE_PARSEABLE ✓
- S0.DATA_LOADABLE ✓
- S2.OOS_METRICS ✓
- S3.TRAIN_TEST_GAP ✓
- S4.TARGET_LEAKAGE ✓
- S4.TEMPORAL_LEAKAGE ✓ (NEW — created for this model)
- S6.FEATURE_IMPORTANCE ✓
- S8.CODE_SMELLS ✓

## Checks Skipped
- S5.DISPARATE_IMPACT — skipped: no protected attributes in credit risk EAR model
- S7.PERTURBATION — skipped: lower priority for regression models with financial features

## New Checks Created
- S4.TEMPORAL_LEAKAGE: Verify that no future data leaks into training...

## Knowledge Referenced
- model_type_regression.md: "Regression models on financial data often have..."
- validation_patterns.md: "CatBoost models in credit scoring..."
```

### 3.8. Estimated effort

~250 LOC for `methodology_planner.py` + ~30 LOC `types.py` + ~50 LOC `pipeline.py`
+ ~30 LOC for check auto-generation helper

---

## 4. Component C: The Autonomous Learner

### 4.1. What to build

Three capabilities that run during background consciousness wakeups
when no new models are waiting:

1. **Cross-validation reflection** — analyze past validations, find patterns
2. **Literature scanner** — search arxiv for new validation techniques
3. **Methodology evolution** — create/improve checks based on 1 and 2

### 4.2. Cross-Validation Reflection

**New file:** `ouroboros/validation/reflection_engine.py`

```python
class ValidationReflectionEngine:
    """Analyzes past validations to find patterns and improve methodology."""

    def __init__(self, validations_dir: Path, knowledge_dir: Path, config: ValidationConfig):
        ...

    async def reflect(self) -> ReflectionResult:
        """
        1. Load all completed validation reports
        2. Cluster by model_type, framework, domain
        3. For each cluster:
           a. Which checks triggered most? → these are the high-value checks
           b. Which checks never triggered? → candidates for deletion
           c. Which models had similar failure patterns?
           d. Were there findings that turned out to be FP across multiple models?
        4. Call LLM to synthesize patterns
        5. Write to knowledge base:
           - model_type_<type>.md — patterns per model type
           - validation_patterns.md — cross-cutting patterns
           - recommendation_patterns.md — which recommendations work
        6. Update scratchpad with evolution targets
        """
        ...
```

**Integration with consciousness task #1 (Effectiveness review):**

```python
# In consciousness _think(), task #1:
if self._should_run_reflection():
    from ouroboros.validation.reflection_engine import ValidationReflectionEngine
    engine = ValidationReflectionEngine(
        self._drive_root / "validations",
        self._drive_root / "memory" / "knowledge",
        config,
    )
    result = await engine.reflect()
    # Write patterns to knowledge base via knowledge_write tool
    ...
```

**Triggers:** Run when > 3 new validations since last reflection.

### 4.3. Literature Scanner

**New file:** `ouroboros/validation/literature_scanner.py`

```python
class LiteratureScanner:
    """Searches arxiv for recent ML model validation papers."""

    def __init__(self, knowledge_dir: Path, config: ValidationConfig):
        self._knowledge_dir = knowledge_dir
        self._config = config
        self._history_file = knowledge_dir / "arxiv_scan_history.json"

    async def scan(self) -> list[PaperSummary]:
        """
        1. Search arxiv for recent papers on relevant topics:
           - "model validation machine learning"
           - "data leakage detection"
           - "fairness testing ML"
           - "model robustness evaluation"
           - "automated testing machine learning"
        2. Filter: published in last 90 days, not already scanned
        3. For each paper (top 5):
           a. Read title + abstract
           b. Call LLM: "Is this paper relevant to ML model validation?
              If yes, what technique could we implement as a validation check?"
           c. If relevant → save to knowledge base
        4. Record scanned paper IDs to avoid re-scanning
        """
        ...

    def _search_arxiv(self, query: str, max_results: int = 10) -> list[dict]:
        """Search arxiv using the arxiv Python library."""
        import arxiv
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
        )
        results = []
        for paper in arxiv.Client().results(search):
            results.append({
                "id": paper.entry_id,
                "title": paper.title,
                "abstract": paper.summary,
                "url": paper.entry_id,
                "published": paper.published.isoformat(),
                "categories": [c for c in paper.categories],
            })
        return results
```

**`PaperSummary` dataclass:**

```python
@dataclass
class PaperSummary:
    arxiv_id: str
    title: str
    abstract: str
    url: str
    relevance_score: float         # LLM-assessed (0-1)
    applicable_technique: str      # what technique could we extract
    proposed_check_idea: str | None  # if directly implementable as a check
```

**Integration with consciousness task #5 (Literature scan):**

```python
# In consciousness _think(), task #5 (every 3rd wakeup):
scanner = LiteratureScanner(knowledge_dir, config)
papers = await scanner.scan()
if papers:
    for p in papers:
        if p.relevance_score > 0.7 and p.proposed_check_idea:
            # Write to scratchpad: "Potential new check from arxiv paper..."
            ...
    # Write summary to knowledge/arxiv_recent.md
    ...
```

**Dependencies:** `pip install arxiv` (lightweight, no auth needed)

**Arxiv search queries** (rotate across wakeups):

```python
_ARXIV_QUERIES = [
    "cat:cs.LG AND (model validation OR model testing)",
    "cat:cs.LG AND (data leakage OR train test contamination)",
    "cat:cs.LG AND (fairness testing OR bias detection)",
    "cat:cs.LG AND (model robustness OR adversarial testing)",
    "cat:stat.ML AND (overfitting detection OR cross-validation)",
    "cat:cs.SE AND (automated testing machine learning)",
    "cat:cs.LG AND (model risk management OR model governance)",
]
```

### 4.4. Methodology Evolution (Autonomous Check Creation)

This ties the reflection and literature scanning into actual code changes.

**File:** `ouroboros/validation/methodology_evolver.py`

```python
class MethodologyEvolver:
    """Creates and improves validation checks based on accumulated knowledge."""

    def __init__(
        self,
        repo_dir: Path,
        check_registry: CheckRegistry,
        effectiveness_tracker: EffectivenessTracker,
        knowledge_dir: Path,
        config: ValidationConfig,
    ):
        ...

    async def evolve(self) -> list[EvolutionAction]:
        """
        1. Read evolution targets from effectiveness tracker
        2. Read recent arxiv findings from knowledge base
        3. Read cross-validation patterns from reflection engine
        4. Decide on ONE action (early phase: just pick the highest priority)
        5. Execute:
           a. "fix_check" → LLM rewrites the check code, test on historical bundle
           b. "create_check" → LLM writes new check, register, test
           c. "delete_check" → disable or remove low-value check
           d. "improve_synthesis" → adjust S9 prompts
        6. Commit the change (git add + commit with methodology version bump)
        7. Return what was done
        """
        ...

    async def _create_check(self, proposal: dict) -> str:
        """
        1. LLM generates check code from description + rationale
        2. Write to validation/checks/
        3. Register in check_manifest.json
        4. Run on one historical bundle to verify it doesn't crash
        5. If crash → fix code and retry (up to 3 times)
        6. Commit
        """
        ...

    async def _fix_check(self, check_id: str, issue: str) -> str:
        """
        1. Read current check code
        2. Read effectiveness data (why is it bad?)
        3. LLM proposes fix
        4. Write new code
        5. Test on historical bundles
        6. If metric improves → commit. If not → revert.
        """
        ...
```

**Integration with consciousness tasks #3 (Methodology freshness) and
the 7-step evolution protocol:**

The evolver IS the evolution protocol applied to validation methodology.
It follows the same steps as original Ouroboros evolution:
1. Assessment → `effectiveness_tracker.get_evolution_targets()`
2. Selection → pick ONE target
3. Implementation → `_create_check()` or `_fix_check()`
4. Smoke test → run on historical bundle (graduated: early = code runs, mature = metric improves)
5. Multi-model review → optional (LLM reviews the diff)
6. Bible check → constitutional compliance
7. Commit + methodology version bump

### 4.5. Estimated effort

| Component | LOC |
|-----------|-----|
| `reflection_engine.py` | ~200 |
| `literature_scanner.py` | ~180 |
| `methodology_evolver.py` | ~250 |
| Consciousness integration | ~100 |
| **Total** | ~730 |

---

## 5. Component D: Per-Model Validation Project Structure

### 5.1. New folder layout per bundle

Each validated model becomes a self-contained project:

```
~/Ouroboros/data/validations/<bundle_id>/
  raw/                          # input artifacts (existing)
    model_code/
    data_samples/
  inputs/                       # task + description (existing)
  inferred/                     # S0 outputs (existing)
    model_profile.json
    dependency_report.json
  methodology/                  # NEW: per-model methodology
    methodology.md              # human-readable validation plan
    methodology_plan.json       # structured plan (MethodologyPlan)
    custom_checks/              # checks created specifically for this model
      s4_temporal_leakage_ear.py
  results/                      # validation results (existing)
    stage_S0.json ... stage_S9.json
    report.json
    report.md
    dependency_install.log
  improvement/                  # improvement cycle (existing)
    plan.json
    plan.md
    implementation/
    revalidation/
  feedback.json                 # human feedback (existing)
  validation.log                # NEW: full execution log (timestamped)
```

### 5.2. Changes to existing code

- `pipeline.py` → create `methodology/` dir during init
- `methodology_planner.py` → write to `methodology/`
- Custom checks → saved to `methodology/custom_checks/` (and also registered in global registry)
- Execution log → append timestamped entries to `validation.log`

### 5.3. Estimated effort

~50 LOC in `pipeline.py` for directory creation and log wiring

---

## 6. Wiring Into Consciousness Loop

### 6.1. Updated consciousness task priority

When the consciousness wakes up, it should check in this order:

```python
def _think(self):
    # Priority 1: New model in inbox? → Validate it
    new_zips = self._watcher.scan_inbox()
    if new_zips:
        self._start_validation(new_zips[0])
        return

    # Priority 2: Ongoing validation needs attention? → Continue it
    stuck = self._check_stuck_validations()
    if stuck:
        self._resume_validation(stuck[0])
        return

    # Priority 3: Effectiveness review (if enough new data)
    if self._should_reflect():
        self._run_reflection()
        return

    # Priority 4: Literature scan (every 3rd idle wakeup)
    if self._should_scan_literature():
        self._run_literature_scan()
        return

    # Priority 5: Methodology evolution (if targets available)
    if self._should_evolve():
        self._run_evolution()
        return

    # Priority 6: Identity & knowledge grooming
    self._run_grooming()
```

### 6.2. Changes to `consciousness.py`

- Import and instantiate `ValidationWatcher` in `__init__`
- Add `_check_validation_inbox()` method
- Add `_start_validation()` → calls `schedule_task` with the validation pipeline
- Wire existing task rotation to the new priority system

The key insight: **validation jobs run as scheduled tasks**, not inline in consciousness.
Consciousness DETECTS that a new model arrived and SCHEDULES a task. The task runner
(existing `supervisor/workers.py`) handles the actual pipeline execution. This preserves
the existing architecture where consciousness is lightweight and tasks are heavy.

### 6.3. Estimated effort

~100 LOC in `consciousness.py`

---

## 7. New Dependencies

| Package | Purpose | Required? |
|---------|---------|-----------|
| `arxiv` | Search arxiv API | Yes (for literature scanner) |
| `watchdog` | OS-level file watching | No (polling fallback works fine at 5-min intervals) |

Install: `.venv/bin/pip install arxiv`

---

## 8. Implementation Prompts

### Prompt A: Watcher + Consciousness Integration (~190 LOC)

1. Create `ouroboros/validation/watcher.py` — `ValidationWatcher` class
2. Add config keys to `ouroboros/config.py`
3. Wire into `consciousness.py` — add inbox check as Priority 1
4. Test: place a ZIP in inbox folder, verify consciousness detects and ingests it

### Prompt B: Methodology Planner (~360 LOC)

1. Add `MethodologyPlan` to `ouroboros/validation/types.py`
2. Create `ouroboros/validation/methodology_planner.py`
3. Wire into `pipeline.py` — insert between dep install and S1
4. Add check auto-generation from methodology proposals
5. Test: run on EAR CL model, verify methodology.md is produced

### Prompt C: Reflection Engine + Literature Scanner (~380 LOC)

1. Create `ouroboros/validation/reflection_engine.py`
2. Create `ouroboros/validation/literature_scanner.py`
3. Add `PaperSummary` to types
4. Wire into consciousness tasks #1 and #5
5. Test: verify arxiv search works, reflection produces knowledge entries

### Prompt D: Methodology Evolver (~250 LOC)

1. Create `ouroboros/validation/methodology_evolver.py`
2. Wire into consciousness — Priority 5
3. Implement the 7-step evolution protocol for validation checks
4. Test: trigger evolution on a check with known low precision

### Prompt E: Project Structure + Execution Log (~100 LOC)

1. Update `pipeline.py` — create methodology/ dir, write validation.log
2. Update folder layout
3. Test: verify complete project structure after validation

---

## 9. Implementation Order

```
Prompt A (watcher) → Prompt B (methodology) → Prompt E (project structure)
                                              ↗
Prompt C (reflection + arxiv) → Prompt D (evolver)
```

- A is independent and highest impact (makes Valoboros autonomous)
- B depends on nothing, produces the most visible change (methodology.md per model)
- C and D are the learning loop — they can follow after A+B are working
- E is a small cleanup that can be done anytime

**Total estimated effort:** ~1280 LOC across 5 prompts

---

## 10. What This Does NOT Include (by design)

| Excluded | Why |
|----------|-----|
| Data-only fallback (training our own model) | Per user's requirement: if model code doesn't show how to train, that's the developer's problem — flag it, don't compensate |
| Web UI for validation results | Valoboros is backend-first; reports are .md/.json files |
| Multi-model parallel validation | Original Ouroboros processes one task at a time via worker queue; parallel validation would require architectural changes |
| Model deployment / serving | Valoboros is analysis-only (BIBLE.md Validation Hard Limits) |
