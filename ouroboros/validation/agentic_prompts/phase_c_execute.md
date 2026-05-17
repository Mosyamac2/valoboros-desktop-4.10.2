Phase B just produced a validation project at
`./methodology/validation_project/`. Your job in this phase is to **run it**
and **interpret what came out**.

## Step 1: Run the project

Use `Bash`. Steps:

```bash
cd methodology/validation_project
pip install -r requirements.txt 2>&1 | tail -10
mkdir -p ../../results
timeout 1200 python run_all.py --tests all --output ../../results/results.json
echo "EXIT=$?"
cd ../..
```

Constraints:

- 1200 seconds (20 minutes) is the hard wall-clock cap for the entire test
  suite. If `timeout` fires, that's fine — the runner script is expected to
  catch per-test exceptions and continue, so partial results still land in
  `results.json`.
- If a single test inside `run_all.py` hangs the whole runner, kill it with
  a follow-up `pkill -f run_all.py`, then mark the suite as partially
  executed in your interpretation.
- If `pip install` fails for a heavy/incompatible dep, edit
  `validation_project/run_all.py` to skip the dependent tests (mark them
  `"verdict": "deferred", "evidence": "<dep> install failed: <reason>"`)
  and re-run. The methodology is the contract; the package is yours to
  adjust to make it run.

## Step 2: Read the results

After the runner exits, read `./results/results.json`. Confirm:

- Every test from `./methodology/methodology.md` has a corresponding entry.
  If any are missing, look in `run_all.py` for why — typo, import error,
  silent skip — and fix it, then re-run.
- The summary counts (`n_pass / n_warn / n_fail / n_deferred / n_error`)
  add up to the total number of tests.
- No verdict is `null`. A test that genuinely could not run should be
  `"deferred"` or `"error"`, not blank.

## Step 3: Write the interpretation

Write `./results/interpretation.md` covering:

1. **Verdict summary**: one sentence on the model's overall validation
   verdict — would you trust it for the stated task? Anchor this to the
   numeric pass/fail counts, not vibes.
2. **Real issues found** (which tests revealed actual problems): list them
   with their `id`, the metric value if numeric, and one sentence on what
   the finding means for the model's deployment risk.
3. **Clean passes**: short bullet list of tests that passed cleanly — be
   specific, "OOS AUC = 0.87 vs train AUC 0.89 (gap 0.02)" beats "no
   overfitting detected".
4. **Errored / deferred tests**: list them with the reason. If a test
   errored because of a setup issue you couldn't fix, say so honestly —
   that's a Phase B improvement target for next time, not a model defect.
5. **Ambiguities**: any test whose verdict is technically PASS but whose
   metric is borderline (e.g. PASS threshold 0.80, actual 0.81). Flag
   these for human review in the final report.

## Critical constraint

Do NOT write **new findings** that aren't backed by `results.json`. Phase C
is interpretation, not invention. If the methodology asked a qualitative
question that the implemented test doesn't actually answer, say so — and
flag it as a Phase B regression for the next iteration. Don't paper over
gaps with prose.

## Outputs

- `./results/results.json` — produced by `run_all.py`.
- `./results/interpretation.md` — your interpretation.

Both must exist for the phase to count as successful.
