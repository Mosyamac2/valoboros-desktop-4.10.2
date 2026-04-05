# Valoboros Tutorial: How It Works and How to Use It

A step-by-step guide to the self-evolving ML model validation platform.

---

## Prerequisites

```bash
# 1. Python 3.10+ with venv
cd /path/to/ouroboros-desktop-4.10.2
source .venv/bin/activate

# 2. Set your OpenRouter API key (the only required key)
export OPENROUTER_API_KEY="sk-or-v1-your-key-here"

# 3. Ensure pandas is installed (for data analysis)
pip install pandas nbformat nbconvert arxiv
```

---

## Part 1: Manual Validation (step by step)

This is the simplest way to validate a model. You control each phase.

### Step 1: Ingest the model

Place your model ZIP in the project and ingest it:

```python
from pathlib import Path
from ouroboros.tools.model_intake import _ingest_model_artifacts_impl

# Create the validations directory
val_dir = Path("validation_data/validations")
val_dir.mkdir(parents=True, exist_ok=True)

# Ingest — provide the model code ZIP + task description
bundle_id = _ingest_model_artifacts_impl(
    validations_dir=val_dir,
    model_code_zip="ml-models-to-validate/your_model.zip",
    task="Predict early repayment rate for consumer loans",
    data_zip="ml-models-to-validate/your_data.zip",       # optional
    data_description="Customer loan data with 29 columns",  # optional
)
print(f"Bundle ID: {bundle_id}")
```

**What happens internally:**
1. Generates a unique `bundle_id` (12-char UUID)
2. Extracts the model code ZIP into `raw/model_code/`
3. Extracts the data ZIP (if provided) into `raw/data_samples/`
4. Writes `inputs/task.txt` and `inputs/data_description.txt`
5. Creates empty directories: `inferred/`, `results/`, `methodology/`, `improvement/`

**Result:** A bundle directory at `validation_data/validations/<bundle_id>/`

### Step 2: Run the full pipeline

```python
import asyncio
from ouroboros.validation.pipeline import ValidationPipeline
from ouroboros.validation.types import ValidationConfig

bundle_dir = val_dir / bundle_id
config = ValidationConfig(
    comprehension_model="anthropic/claude-sonnet-4",  # use sonnet to save cost
    synthesis_model="anthropic/claude-sonnet-4",
    auto_self_assess=False,  # skip for first test
    auto_improve=False,       # skip improvement cycle for now
)

pipeline = ValidationPipeline(bundle_id, bundle_dir, Path("."), config)
report = asyncio.run(pipeline.run())

print(f"Verdict: {report.overall_verdict}")
print(f"Stages completed: {len(report.stages)}")
print(f"Critical findings: {len(report.critical_findings)}")
print(f"Hard recommendations: {len(report.hard_recommendations)}")
print(f"Soft recommendations: {len(report.soft_recommendations)}")
```

### What the pipeline does (10 phases)

Here is exactly what happens when you call `pipeline.run()`, in order:

#### Phase 1: Artifact Comprehension (S0)

```
Pipeline → ArtifactComprehension.analyze()
```

1. **Dependency extraction** (deterministic, no LLM):
   - AST-parses every `.py` file for `import` statements
   - Parses `.ipynb` notebook code cells for imports
   - Checks for `%pip install` / `!pip install` magic commands
   - Checks for `requirements.txt`
   - Maps import names to pip names (`sklearn` → `scikit-learn`)
   - Result: `DependencyReport` with merged package list

2. **LLM comprehension** (one LLM call):
   - Reads all code files (truncated at 80K chars)
   - Reads first 100 rows of each data file
   - Reads `task.txt` and `data_description.txt`
   - Sends everything to the LLM with the prompt: "Analyze these ML artifacts and produce a structured JSON profile"
   - LLM infers: model type, framework, algorithm, target column, features, preprocessing steps, hyperparameters, etc.

3. **Merge and save**:
   - Deterministic deps + LLM-detected deps → merged, deduplicated
   - Writes `inferred/model_profile.json`
   - Writes `inferred/dependency_report.json`

#### Phase 2: Intake Checks (S0 checks)

Runs deterministic checks from the check registry:
- **S0.CODE_PARSEABLE**: Can all `.py`/`.ipynb` files be parsed?
- **S0.DATA_LOADABLE**: Can pandas load the data files?

If S0 comprehension confidence is < 0.1 → pipeline aborts (hard gate).

#### Phase 3: Dependency Installation

```
Pipeline → sandbox.install_dependencies(profile.dependencies_detected)
```

Creates a virtual environment inside the bundle directory (`.sandbox_venv/`)
and pip-installs all detected packages. This is why the dependency extractor
runs first — it tells the sandbox what to install.

#### Phase 4: Methodology Planning

```
Pipeline → MethodologyPlanner.plan()
```

The LLM designs a custom validation plan for THIS specific model:
- Reads the model profile
- Lists all available checks with their tags
- Reads knowledge base files (`model_type_regression.md`, `validation_patterns.md`)
- Produces a `MethodologyPlan`:
  - Risk priorities (e.g., "temporal leakage" first for time-series models)
  - Checks to run (selected by relevance)
  - Checks to skip (with reasons)
  - New checks to create (if existing ones are insufficient)
- Saves `methodology/methodology.md` and `methodology/methodology_plan.json`

If LLM fails → fallback: select all applicable checks, skip none.

#### Phase 5: Reproducibility (S1)

```
Pipeline → reproducibility.run_stage()
```

Runs the model code in the sandbox twice and compares output:
- If the code crashes → **hard gate**: S2-S7 are skipped
- If output differs between runs → warning: model is non-deterministic

This is the step that was failing on the EAR CL model because `lightgbm`
wasn't installed. With the dependency extractor (Phase 3), this is now fixed.

#### Phase 6: Quantitative Checks (S2, S3, S6, S7)

Only run if S1 passed AND the methodology plan includes them:

| Stage | What it does |
|-------|-------------|
| **S2 Performance** | Trains a model on the data, computes OOS metrics (AUC, RMSE, accuracy) |
| **S3 Fit Quality** | Compares train vs test performance to detect overfitting |
| **S6 Sensitivity** | Computes permutation importance, flags counterintuitive features |
| **S7 Robustness** | Perturbs features by ±1 std, measures prediction change |

Each check generates a Python script and runs it in the sandbox.

#### Phase 7: Code-Only Checks (S4, S5, S8)

Run regardless of S1 — they don't need the model to execute:

| Stage | What it does |
|-------|-------------|
| **S4 Leakage** | Computes feature-target correlations, flags > 0.95 |
| **S5 Fairness** | Computes disparate impact ratio for protected attributes |
| **S8 Code Quality** | Scans for hardcoded paths, missing seeds, no train/test split |

#### Phase 8: Synthesis (S9)

```
Pipeline → synthesis.run_stage(prior_stages)
```

Collects ALL failed checks from S0-S8, then calls the LLM to produce
improvement recommendations for each:

- **Hard recommendations**: Specific, implementable code changes with estimated metric impact.
  Example: "Replace `RandomForest(n_estimators=100)` with `GradientBoosting(n_estimators=200, learning_rate=0.05)`"

- **Soft recommendations**: Valuable observations that can't be automated.
  Example: "Training data has only 500 rows — model is likely overfit to noise"

Saves to `improvement/plan.json`.

#### Phase 9: Report Generation

```
Pipeline → ReportGenerator.save()
```

Produces two files:
- `results/report.json` — structured, machine-readable
- `results/report.md` — human-readable with executive summary (LLM-generated)

The report includes: verdict (approved/conditional/rejected), all stage results,
critical findings, hard and soft recommendations, confidence scores.

#### Phase 10: Self-Assessment (optional)

If `config.auto_self_assess=True`:

The LLM reviews its own findings and rates each as likely-TP or likely-FP.
Stored with weight 0.3 in the effectiveness tracker. This bootstraps the
learning loop from day one.

### Step 3: Read the results

```bash
# Human-readable report
cat validation_data/validations/<bundle_id>/results/report.md

# Structured report
cat validation_data/validations/<bundle_id>/results/report.json

# Model profile (what the LLM understood)
cat validation_data/validations/<bundle_id>/inferred/model_profile.json

# Dependency report (what was detected and installed)
cat validation_data/validations/<bundle_id>/inferred/dependency_report.json

# Methodology plan (which checks were selected and why)
cat validation_data/validations/<bundle_id>/methodology/methodology.md

# Improvement recommendations
cat validation_data/validations/<bundle_id>/improvement/plan.json

# Full execution log (timestamped)
cat validation_data/validations/<bundle_id>/validation.log
```

---

## Part 2: Using the Folder Watcher

The watcher monitors a folder and auto-ingests new ZIPs.

### Setup

```python
from pathlib import Path
from ouroboros.validation.watcher import ValidationWatcher
from ouroboros.validation.types import ValidationConfig

watcher = ValidationWatcher(
    inbox_dir=Path("ml-models-to-validate"),
    validations_dir=Path("validation_data/validations"),
    repo_dir=Path("."),
    config=ValidationConfig(),
)
```

### Scan for new models

```python
# Check what's new
new_zips = watcher.scan_inbox()
print(f"Found {len(new_zips)} new model(s):")
for z in new_zips:
    print(f"  - {z.name}")
```

### Ingest a model

```python
if new_zips:
    bundle_id = watcher.ingest(
        new_zips[0],
        task="Predict early repayment rate for consumer loans",
    )
    print(f"Ingested as bundle {bundle_id}")
    # The ZIP is now marked as processed — won't appear in next scan
```

### Run validation after ingestion

```python
import asyncio
from ouroboros.validation.pipeline import ValidationPipeline

bundle_dir = Path(f"validation_data/validations/{bundle_id}")
config = ValidationConfig(comprehension_model="anthropic/claude-sonnet-4")
pipeline = ValidationPipeline(bundle_id, bundle_dir, Path("."), config)
report = asyncio.run(pipeline.run())
print(f"Verdict: {report.overall_verdict}")
```

---

## Part 3: The Learning Loop

Valoboros learns from every validation it performs.

### Check effectiveness metrics

```python
from ouroboros.validation.effectiveness import EffectivenessTracker

tracker = EffectivenessTracker(Path("validation_data"))

# Platform-level metrics
metrics = tracker.get_platform_metrics()
print(f"Phase: {metrics.maturity_phase}")
print(f"Validations: {metrics.total_validations}")
print(f"Mean finding precision: {metrics.mean_finding_precision:.2f}")
print(f"Mean improvement lift: {metrics.mean_improvement_lift:.4f}")
```

### Submit human feedback

When you review a report and know whether a finding was correct:

```python
# Mark a finding as true positive (real issue)
tracker.record_finding_feedback(
    check_id="S4.TARGET_LEAKAGE",
    bundle_id="d6ac58b2-44a",
    verdict="true_positive",
    source="human",
    weight=1.0,
)

# Mark a finding as false positive (false alarm)
tracker.record_finding_feedback(
    check_id="S8.CODE_SMELLS",
    bundle_id="d6ac58b2-44a",
    verdict="false_positive",
    source="human",
    weight=1.0,
)
```

### Run the reflection engine

After validating several models, reflect on patterns:

```python
from ouroboros.validation.reflection_engine import ValidationReflectionEngine

engine = ValidationReflectionEngine(
    validations_dir=Path("validation_data/validations"),
    knowledge_dir=Path("validation_data/knowledge"),
    config=ValidationConfig(),
)
result = engine.reflect_sync()
print(f"Analyzed {result.total_validations_analyzed} validations")
print(f"Patterns found: {len(result.patterns_found)}")
print(f"Dead checks: {result.dead_checks}")
print(f"Hot checks: {result.hot_checks}")
print(f"Knowledge written: {result.knowledge_entries_written}")
```

### Scan arxiv for new techniques

```python
from ouroboros.validation.literature_scanner import LiteratureScanner

scanner = LiteratureScanner(
    knowledge_dir=Path("validation_data/knowledge"),
    config=ValidationConfig(),
)
papers = scanner.scan_sync()
print(f"Found {len(papers)} papers")
for p in papers:
    if p.relevance_score >= 0.4:
        print(f"  [{p.relevance_score:.1f}] {p.title}")
```

### Evolve the methodology

```python
from ouroboros.validation.methodology_evolver import MethodologyEvolver
from ouroboros.validation.check_registry import CheckRegistry

evolver = MethodologyEvolver(
    repo_dir=Path("."),
    check_registry=CheckRegistry(Path(".")),
    effectiveness_tracker=tracker,
    knowledge_dir=Path("validation_data/knowledge"),
    config=ValidationConfig(),
)
action = evolver.evolve_sync()
if action:
    print(f"Evolution: {action.action_type} — {action.description}")
else:
    print("No evolution targets available yet.")
```

---

## Part 4: One-Liner Full Validation

For quick use, here's the complete flow in one script:

```bash
OPENROUTER_API_KEY="your-key" python -c "
import asyncio
from pathlib import Path
from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
from ouroboros.validation.pipeline import ValidationPipeline
from ouroboros.validation.types import ValidationConfig

val_dir = Path('validation_data/validations')
val_dir.mkdir(parents=True, exist_ok=True)

bid = _ingest_model_artifacts_impl(
    val_dir,
    'ml-models-to-validate/your_model.zip',
    task='Describe what the model does here',
)
print(f'Bundle: {bid}')

config = ValidationConfig(
    comprehension_model='anthropic/claude-sonnet-4',
    synthesis_model='anthropic/claude-sonnet-4',
    report_model='anthropic/claude-sonnet-4',
    auto_self_assess=True,
    auto_improve=False,
)
pipeline = ValidationPipeline(bid, val_dir / bid, Path('.'), config)
report = asyncio.run(pipeline.run())

print(f'Verdict: {report.overall_verdict}')
print(f'Critical: {len(report.critical_findings)}')
print(f'Hard recs: {len(report.hard_recommendations)}')
print(f'Soft recs: {len(report.soft_recommendations)}')
print(f'Report: {val_dir / bid / \"results\" / \"report.md\"}')
"
```

---

## Part 5: Output File Map

After validation, each bundle contains:

```
validation_data/validations/<bundle_id>/
├── raw/
│   ├── model_code/            ← extracted from your model ZIP
│   │   ├── train.py
│   │   └── model.ipynb
│   └── data_samples/          ← extracted from your data ZIP
│       └── train.csv
├── inputs/
│   ├── task.txt               ← your task description
│   └── data_description.txt   ← your data description
├── inferred/
│   ├── model_profile.json     ← LLM-inferred model understanding
│   └── dependency_report.json ← AST-extracted dependencies
├── methodology/
│   ├── methodology.md         ← human-readable validation plan
│   ├── methodology_plan.json  ← structured plan
│   └── custom_checks/         ← checks created for this model
├── results/
│   ├── stage_S0.json          ← per-stage results
│   ├── stage_S1.json
│   ├── ...
│   ├── report.json            ← full structured report
│   └── report.md              ← human-readable report
├── improvement/
│   ├── plan.json              ← hard + soft recommendations
│   ├── implementation/        ← modified code (after improvement cycle)
│   └── revalidation/          ← revalidation results
├── validation.log             ← timestamped execution log
└── .sandbox_venv/             ← isolated Python environment
```

---

## Part 6: Configuration Reference

All settings can be set via environment variables or `ValidationConfig`:

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `comprehension_model` | `anthropic/claude-opus-4.6` | LLM for understanding model artifacts (S0) |
| `synthesis_model` | `anthropic/claude-opus-4.6` | LLM for generating recommendations (S9) |
| `report_model` | `anthropic/claude-opus-4.6` | LLM for executive summary in report |
| `improvement_model` | `anthropic/claude-opus-4.6` | LLM for implementing code changes |
| `sandbox_mem_mb` | `4096` | Memory limit for sandbox (MB) |
| `sandbox_cpu_sec` | `120` | CPU time limit for sandbox (seconds) |
| `stage_timeout_sec` | `600` | Max time per pipeline stage |
| `timeout_sec` | `3600` | Max total pipeline time |
| `auto_self_assess` | `True` | Run Tier 0 self-assessment after validation |
| `auto_improve` | `True` | Auto-run improvement cycle |
| `maturity_threshold` | `20` | Bundles needed to transition early → mature |
| `max_hard_recommendations` | `10` | Cap on implementable recommendations |
| `max_soft_recommendations` | `10` | Cap on informational recommendations |
| `inbox_dir` | `ml-models-to-validate` | Folder to watch for new model ZIPs |

**Cost-saving tip:** Use `anthropic/claude-sonnet-4` instead of `opus` for all
models during testing. Sonnet is ~10x cheaper and works well for validation tasks.
