Now you are a senior developer with a strong statistics background. Phase A
just produced a validation methodology — read it first, then implement it.

## Inputs

- `./methodology/methodology.md` — the methodology you (or a prior Phase A
  session for this bundle) designed. Read every item.
- `./raw/model_code/` — the original model code (Python scripts or `.ipynb`
  notebooks). Read whatever you need to understand how to load and re-use
  preprocessing, splits, or feature definitions if applicable.
- `./raw/` — the rest of the bundle: data samples, model_report.md, etc.

## Output

Create a complete Python project at `./methodology/validation_project/`
with this exact layout:

```
validation_project/
  common/
    __init__.py
    helpers.py          # data loading + metric helpers + splitting helpers
    io.py               # result JSON schema + IO utils + write_results()
  qualitative/
    __init__.py
    q1_<slug>.py        # one module per qualitative item from methodology.md
    q2_<slug>.py
    ...
  quantitative/
    __init__.py
    quant1_<slug>.py    # one module per quantitative item from methodology.md
    quant2_<slug>.py
    ...
  run_all.py            # CLI entrypoint; see contract below
  requirements.txt      # pinned deps the project needs to run
  README.md             # 30–60 lines: how to run + what each test does
```

The `<slug>` in filenames is the short name from the methodology's `name`
field, lowercased and `_`-joined (e.g. `q1_target_column_check.py`,
`quant3_oos_auc.py`).

## run_all.py contract

`run_all.py` must be importable and runnable. Its CLI:

```
python run_all.py --tests {all|<id1>,<id2>,...} --output <results.json>
```

`--tests all` runs every test. Otherwise comma-separated test ids
(`q1,quant3,...`). On completion, write a JSON file with this shape:

```json
{
  "schema_version": "1",
  "bundle_id": "<bundle id>",
  "tests": [
    {
      "id": "q1",
      "name": "target column ambiguity",
      "block": "qualitative",
      "verdict": "pass|warn|fail|deferred|error",
      "metric": null,
      "evidence": "free-form text",
      "error": null
    },
    {
      "id": "quant1",
      "name": "OOS AUC",
      "block": "quantitative",
      "verdict": "pass",
      "metric": {"AUC": 0.87},
      "evidence": "computed on holdout split; n=300",
      "error": null
    }
  ],
  "summary": {
    "n_pass": 0,
    "n_warn": 0,
    "n_fail": 0,
    "n_deferred": 0,
    "n_error": 0
  }
}
```

Constraints on the runner:

- Each test module exposes `run(ctx)` returning a dict with at minimum
  `verdict` (str) and optionally `metric`, `evidence`, `error`.
- `ctx` is an object built once in `run_all.py` and reused; it carries
  paths to `./raw/`, the parsed data, the loaded model artifact if any,
  and metric helpers from `common/helpers.py`.
- On a test failure (Python exception, not a fail verdict), record it as
  `verdict: "error"` with the exception message in `error`. Do NOT crash
  the whole runner — other tests must still run.
- Use `timeout 300` semantics inside `run_all.py` for any test that loops
  or trains a model — wall-clock cap per test.

## Implementation constraints

- Use only data already present in `./raw/`. Do NOT download anything.
- If a test cannot be implemented (e.g. methodology asked for protected
  attributes but no candidate column exists in the data), set its module's
  `run()` to return `{"verdict": "deferred", "evidence": "<one-line reason>"}`.
  Do NOT invent metrics.
- Heavy deps: prefer what is already installed (numpy, pandas, scikit-learn
  are almost always available). If the methodology genuinely requires e.g.
  `lightgbm` or `xgboost`, add it to `requirements.txt` and install via
  `pip install -r requirements.txt` in the local venv with Bash. Stay under
  a 5-minute install budget. If a dep would exceed that, mark the dependent
  tests as deferred instead of bringing it in.
- The project must be **importable cold**: a fresh `python -c "import
  run_all"` must succeed without running any test logic.
- Write small, focused modules. ≤ ~150 lines per test file is healthy.
- No global side effects at import time.

## Verification before you signal done

After writing the project, run these checks via Bash from the cwd:

```bash
cd methodology/validation_project
pip install -r requirements.txt 2>&1 | tail -5
python -c "import sys; sys.path.insert(0, '.'); import run_all"
echo "IMPORT_OK"
```

If any of those fails, fix the project and re-verify. Do not finish the
phase until `IMPORT_OK` is printed. (Phase C will actually invoke
`run_all.py` and read its results — do not run it here.)

## What you should NOT do

- Do not modify anything under `./raw/`.
- Do not write outside `./methodology/validation_project/` and the local
  pip cache used by `pip install`.
- Do not invent metrics or hand-craft fake results.
- Do not produce a single monolithic file. The one-test-per-module layout
  is mandatory — it's how reflection across bundles spots recurring
  qualitative test motifs.
