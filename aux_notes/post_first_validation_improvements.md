# Valoboros: Post-First-Validation Improvement Plan

**Date:** 2026-04-05
**Based on:** First real validation of EAR Consumer Loans model (bundle d6ac58b2-44a)
**Status:** PLAN ONLY — do not implement yet

---

## 1. Problem Summary

The first real validation exposed three structural weaknesses:

| Problem | What happened | Impact |
|---------|-------------|--------|
| **S1 hard-fails on missing deps** | Notebook imported `lightgbm` which wasn't in sandbox. S1 returned `FAIL`, and S2-S7 all skipped. | 60% of checks never ran. The pipeline gave up too early. |
| **No self-recovery on failure** | Pipeline saw S1 fail and just skipped everything downstream. No attempt to diagnose, install deps, or retry. | One missing pip package killed the whole validation. |
| **S8 too rigid** | "No visible train/test split" flagged as failure — but model uses temporal split at 2024-08-31 (detected by S0 comprehension). S8 didn't use the profile to adjust. | False positive finding. |

---

## 2. Improvement 0 (NEW): Deterministic Dependency Extraction

### Problem

S0 comprehension uses the LLM to infer `dependencies_detected`, but this is unreliable:
- LLM may miss imports buried in notebook cells
- LLM may hallucinate package names
- LLM confuses import names with pip names (e.g., reports `sklearn` not `scikit-learn`)
- LLM doesn't see conditional imports or imports inside functions

### What to create

**New file:** `ouroboros/validation/dependency_extractor.py`

A **deterministic, AST-based** dependency scanner that runs BEFORE the LLM call and
provides ground truth on what the code actually imports.

```python
class DependencyExtractor:
    """Extract all third-party imports from .py and .ipynb files using AST parsing."""

    def __init__(self, code_dir: Path):
        self._code_dir = code_dir

    def extract(self) -> DependencyReport:
        """
        1. For each .py file: ast.parse → walk tree → collect Import/ImportFrom nodes
        2. For each .ipynb file: extract code cells → ast.parse each cell → same
        3. Separate stdlib imports from third-party using sys.stdlib_module_names
        4. Map import names to pip package names using _IMPORT_TO_PIP
        5. Return structured report
        """
        ...
```

**`DependencyReport` dataclass** (add to `types.py` or keep in same file):

```python
@dataclass
class DependencyReport:
    imports_found: list[str]           # raw import names from code (e.g., ["lightgbm", "catboost", "sklearn"])
    pip_packages: list[str]            # mapped to pip names (e.g., ["lightgbm", "catboost", "scikit-learn"])
    unmapped: list[str]                # imports we couldn't map to pip (may be local modules)
    stdlib: list[str]                  # stdlib imports (filtered out)
    source_files: dict[str, list[str]] # which file imported what
```

**`_IMPORT_TO_PIP` mapping** — common mismatches between import and pip names:

```python
_IMPORT_TO_PIP = {
    # ML/Data
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "lightgbm": "lightgbm",
    "catboost": "catboost",
    "xgboost": "xgboost",
    "tf": "tensorflow",
    "tensorflow": "tensorflow",
    "torch": "torch",
    "torchvision": "torchvision",
    # Data
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "attr": "attrs",
    "dotenv": "python-dotenv",
    "sqlalchemy": "sqlalchemy",
    "psycopg2": "psycopg2-binary",
    # Viz
    "mpl_toolkits": "matplotlib",
    "plotly": "plotly",
    "seaborn": "seaborn",
    # Utils
    "tqdm": "tqdm",
    "joblib": "joblib",
    "polars": "polars",
    "pyarrow": "pyarrow",
}
```

**Stdlib detection:** Python 3.10+ has `sys.stdlib_module_names`. For earlier versions,
maintain a frozen set of stdlib module names.

### How it fits in the pipeline

```
S0 comprehension:
  1. DependencyExtractor.extract()          ← NEW: deterministic, fast, no LLM
  2. LLM artifact comprehension             ← existing: infers profile, also guesses deps
  3. Merge: union(extractor.pip_packages, profile.dependencies_detected)
  4. Deduplicate, sort
  5. Store merged list in profile.dependencies_detected
```

The extractor runs first because:
- It's fast (pure AST parsing, no LLM cost)
- It's reliable (doesn't hallucinate packages)
- It catches things the LLM misses (conditional imports, nested imports)
- The LLM result is used to SUPPLEMENT, not replace — it may catch packages referenced in comments or descriptions

### AST walking logic

```python
def _extract_imports_from_source(source: str) -> set[str]:
    """Parse Python source and return top-level import names."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports
```

For notebooks: extract each code cell's source, parse individually (cells may have
syntax errors in isolation — skip those, don't fail the whole file).

### Edge cases

| Case | Handling |
|------|----------|
| `import lightgbm as lgb` | AST captures `lightgbm` correctly |
| `from sklearn.ensemble import ...` | AST captures `sklearn`, mapped to `scikit-learn` |
| `try: import optuna except: pass` | AST captures `optuna` — it's in a Try but still imported |
| `exec("import X")` | Not caught by AST — LLM may catch from context |
| Local modules (`from utils import ...`) | Filtered: check if `utils.py` exists in code_dir |
| `%pip install X` (notebook magic) | Not AST — add regex scan for `%pip` / `!pip` lines |
| `requirements.txt` exists | Parse it and merge — highest priority source |

### Estimated effort

~120 lines for `dependency_extractor.py` + ~10 lines in `artifact_comprehension.py` to call it + ~10 lines in `pipeline.py` to wire the merged deps into install.

---

## 3. Improvement A: Auto-Install Dependencies Before S1

### What to change

**File:** `ouroboros/validation/pipeline.py` → `run()` method

Insert a new step between S0 (comprehension) and S1 (reproducibility):

```
S0 comprehension (includes dep extraction) → NEW: sandbox.install_dependencies() → S1 reproducibility → ...
```

**Logic:**
1. After S0 produces `ModelProfile` (which now includes merged deterministic + LLM deps),
   read `profile.dependencies_detected`
2. Call `self._sandbox.install_dependencies(profile.dependencies_detected)`
3. If installation fails for some packages, log warnings but proceed — S1 will catch import errors
4. Record install results in `results/dependency_install.json`

**File:** `ouroboros/validation/sandbox.py` → `install_dependencies()`

Already implemented. No changes needed — just needs to be called.

**Estimated effort:** ~30 lines added to `pipeline.py`

### Edge cases
- Some packages need system deps (e.g., `lightgbm` needs `libgomp`). Sandbox can't install those. Record as a finding, not a hard failure.
- Version conflicts. Try `--no-deps` flag as fallback if first install fails.
- Very large packages (tensorflow ~500MB). Set a reasonable timeout and size warning.

---

## 3. Improvement B: S1 Retry Loop with Reflection

### What to change

**File:** `ouroboros/validation/reproducibility.py` → `run_stage()`

Current behavior: try once → fail → done.

New behavior: **try → if fail, reflect → fix → retry (up to 3 attempts)**

```python
async def run_stage(...) -> ValidationStageResult:
    max_attempts = 3
    for attempt in range(max_attempts):
        result = _try_execute(sandbox, main_file, config)
        if result.returncode == 0:
            break  # success — proceed to determinism check
        
        # Reflect on the failure
        diagnosis = _diagnose_failure(result.stderr, profile)
        
        if diagnosis.type == "missing_import":
            # Try installing the missing package
            sandbox.install_dependencies(diagnosis.packages)
            continue  # retry
        
        if diagnosis.type == "missing_data":
            # Data file path mismatch — try to symlink/copy data
            _fix_data_paths(bundle_dir, diagnosis)
            continue  # retry
        
        if diagnosis.type == "gpu_required":
            # Can't fix — record as finding, don't retry
            break
        
        # Unknown error — don't retry blindly
        break
```

**`_diagnose_failure()` function** — parses stderr to identify:

| Error Pattern | Diagnosis | Auto-fix |
|--------------|-----------|----------|
| `ModuleNotFoundError: No module named 'X'` | `missing_import` | `sandbox.install_dependencies(["X"])` |
| `FileNotFoundError: ... .csv` | `missing_data` | Symlink data files into working directory |
| `CUDA not available` / `GPU` | `gpu_required` | None — record as soft finding |
| `MemoryError` | `oom` | None — record, suggest reducing data |
| `SyntaxError` | `code_error` | None — record as-is |
| Anything else | `unknown` | None — record as-is |

**Common import-to-pip mapping** (new constant):

```python
_IMPORT_TO_PIP = {
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "yaml": "pyyaml",
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
    "attr": "attrs",
    "lightgbm": "lightgbm",
    "catboost": "catboost",
    "xgboost": "xgboost",
    "tf": "tensorflow",
    "torch": "torch",
}
```

**Estimated effort:** ~100 lines in `reproducibility.py`, ~20 lines mapping dict

---

## 4. Improvement C: S8 Uses Model Profile for Context-Aware Checks

### What to change

**File:** `ouroboros/validation/checks/s8_code_smells.py`

Current: checks for `train_test_split` regex in code. If not found, flags as failure.

Problem: The EAR model uses temporal split (detected by S0 as `train_test_split_method: "temporal split at 2024-08-31"`). The code filters by date, which is a valid split — but S8's regex doesn't recognize it.

**New logic:**

```python
def run(bundle_dir, model_profile, sandbox=None):
    # ... existing checks ...
    
    # Check for missing train/test split — BUT consult model_profile first
    has_split_in_code = _TRAIN_TEST_SPLIT.search(all_code)
    has_split_in_profile = bool(model_profile.get("train_test_split_method"))
    
    if not has_split_in_code and not has_split_in_profile:
        findings.append("No visible train/test split or cross-validation")
    elif not has_split_in_code and has_split_in_profile:
        # Profile says there's a split method, but code doesn't show it explicitly
        # This is a SOFT finding, not a hard failure
        findings.append(
            f"Train/test split method detected by comprehension "
            f"('{model_profile['train_test_split_method']}') but not visible "
            f"as explicit code pattern. Verify the split is implemented correctly."
        )
        # Downgrade severity to "info" instead of "warning"
```

Also add temporal-split-aware patterns:

```python
_TEMPORAL_SPLIT_RE = re.compile(
    r'report_date|date_col|time_split|cutoff_date|train_date|test_date|'
    r'\d{4}-\d{2}-\d{2}.*split|filter.*date|loc\[.*date'
)
```

**Estimated effort:** ~30 lines modified in `s8_code_smells.py`

---

## 5. Improvement D: Valoboros Self-Validates When Checks Don't Pass

### Concept: "Compensatory Validation"

When a stage fails for infrastructure reasons (not model quality reasons), the pipeline should try to compensate rather than skip.

**File:** `ouroboros/validation/pipeline.py` → `run()` method

| Scenario | Current behavior | New behavior |
|----------|-----------------|-------------|
| S1 fails (can't execute code) | Skip S2-S7 entirely | Try auto-fix (Improvement B). If still fails, run S2-S7 in **data-only mode** — use the raw data to train a simple model ourselves and validate that. |
| No explicit train/test split | Flag as failure | Use `temporal_column` from profile (if available) to create temporal cross-validation splits automatically |
| S2 check finds no metrics | Report as "couldn't compute" | If we have data + target column, train a baseline model (e.g., `GradientBoosting` with defaults) to establish baseline metrics |

### Data-Only Fallback for S2-S7

When S1 fails and auto-fix doesn't help, the pipeline can still do useful validation using just the data:

```python
# In pipeline.py, after S1 fails and retry exhausted:
if not s1_passed and profile.target_column and profile.data_format == "tabular":
    # Run S2-S7 in data-only mode: train our own baseline model
    for module_name, stage_id in [("performance", "S2"), ...]:
        result = await self._run_stage_module(
            module_name, stage_id, profile,
            mode="data_only"  # new parameter
        )
```

Each sandbox check would need a `data_only` path that:
1. Loads data from `raw/data_samples/`
2. Identifies target from `profile.target_column`
3. Trains a simple model (GradientBoosting with defaults)
4. Runs the check against this baseline model
5. Notes in the result: "Validated against baseline model, not original code"

**Estimated effort:** ~50 lines in `pipeline.py`, ~30 lines per sandbox check (s2, s3, s6, s7)

---

## 6. Improvement E: Temporal Cross-Validation When No Explicit Split

### What to change

**New file:** `ouroboros/validation/checks/s3_temporal_cv.py`

When `model_profile.temporal_column` is set but no explicit train/test split is found, this check automatically:

1. Loads data, sorts by temporal column
2. Creates 3 temporal splits (e.g., 60/20/20 rolling forward)
3. Trains a simple model on each split
4. Reports train/test gap across splits
5. If gap > threshold, flags overfitting with temporal context

```python
def run(bundle_dir, model_profile, sandbox=None):
    temporal_col = model_profile.get("temporal_column")
    if not temporal_col:
        return _skip("No temporal column detected")
    
    target = model_profile.get("target_column")
    if not target:
        return _skip("No target column detected")
    
    # Generate sandbox script for temporal CV
    script = _TEMPORAL_CV_SCRIPT.format(
        data_dir=str(bundle_dir / "raw" / "data_samples"),
        temporal_col=temporal_col,
        target=target,
    )
    
    if sandbox is None:
        return _skip("No sandbox available")
    
    result = sandbox.run(script, timeout=120)
    # Parse results...
```

**Register as:** `S3.TEMPORAL_CV`, tags: `["tabular"]`, check_type: `"sandbox"`

**Estimated effort:** ~80 lines for the new check

---

## 7. Improvement F: Pipeline Reflection After Completion

### Concept

After the pipeline finishes, add a **reflection step** where the LLM reviews what went wrong in this specific validation and suggests methodology improvements.

**File:** `ouroboros/validation/pipeline.py` → `run()` method (after report generation)

```python
# After report is saved, before return:
if any(s.status in ("failed", "error") for s in stages):
    reflection = await self._reflect_on_failures(stages, profile)
    # Write reflection to results/pipeline_reflection.md
    # If reflection suggests a concrete check improvement,
    # write it to scratchpad for the evolution loop to pick up
```

**Reflection prompt:**

```
You just validated an ML model and some stages failed.
Review the failures and answer:
1. Was the failure a MODEL problem or a PIPELINE problem?
2. If pipeline problem: what specific change would prevent this failure next time?
3. If model problem: is the finding correctly communicated to the user?
```

This connects back to the Ouroboros self-improvement loop — the pipeline learns from each validation, not just from human feedback.

**Estimated effort:** ~60 lines in `pipeline.py`, ~20 lines for reflection prompt

---

## 8. Implementation Priority

| # | Improvement | Impact | Effort | Priority |
|---|-----------|--------|--------|----------|
| **0** | Deterministic dependency extraction | **High** — reliable dep list, no LLM guessing | Medium (~120 LOC) | **1st** |
| **A** | Auto-install deps before S1 | **High** — unblocks S2-S7 for most models | Small (~30 LOC) | **1st (with 0)** |
| **B** | S1 retry with diagnosis | **High** — auto-fixes most import/path errors | Medium (~120 LOC) | **2nd** |
| **C** | S8 uses model profile | **Medium** — eliminates false positive for temporal splits | Small (~30 LOC) | **3rd** |
| **E** | Temporal cross-validation check | **Medium** — new capability for time-series models | Medium (~80 LOC) | **4th** |
| **D** | Data-only fallback for S2-S7 | **High** — validation always produces metrics | Large (~200 LOC) | **5th** |
| **F** | Pipeline reflection | **Medium** — drives self-improvement | Medium (~80 LOC) | **6th** |

**Suggested implementation order:** 0+A (batch) → B → C → E → D → F

Improvements 0+A+B+C together (~300 LOC) would have prevented all three failures
in the EAR CL validation. The dependency extractor (0) feeds into auto-install (A),
so they must ship together. The retry loop (B) and profile-aware S8 (C) are
independent and can follow immediately.

---

## 9. Prompt Changes Needed

| File | Change |
|------|--------|
| `prompts/SYSTEM.md` | Add to Drift Detector: "Pipeline surrender" — giving up after one failure without attempting diagnosis or retry |
| `prompts/CONSCIOUSNESS.md` | Task #7 (pipeline health) should include: "Review recent failed validations. Were failures due to model quality or pipeline limitations? If pipeline — propose improvement." |

---

## 10. Metrics to Track

After implementing these improvements, measure:

| Metric | Before | Target |
|--------|--------|--------|
| S1 pass rate (first attempt) | ~40% (estimated) | — |
| S1 pass rate (after retry) | N/A | > 70% |
| S2-S7 skip rate | ~60% (from S1 failures) | < 20% |
| S8 false positive rate | High (temporal splits flagged) | < 10% |
| Validations with at least one quantitative metric | ~30% | > 80% |
