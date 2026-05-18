Prettify the validation results into a human-readable report for the model's
owner.

## Inputs (read these in this order)

1. `./results/results.json` — the structured output of `run_all.py`. Every
   test has an `id`, `name`, `block`, `verdict`, `metric`, `evidence`,
   `error`. The `summary` block holds the per-verdict counts.
2. `./results/interpretation.md` — your earlier interpretation pass. Carries
   the overall verdict, the "real issues" list, ambiguities, and Phase-B
   regression notes.
3. `./methodology/methodology.md` — for the original `why_it_matters` /
   `pass_criterion` text that gives each result its context.

## Output

Write `./results/report.md` with this structure:

```
# Validation report — <bundle id>

## Overall verdict
<one paragraph anchored to the metric counts from results.json>

## Hard findings (recommendations the side agent should implement)
<for each finding from the methodology that came back with a `fail` verdict
 AND has an implementable fix: a short paragraph + a code-level
 recommendation that a Python developer could act on.>

## Soft findings (cannot be auto-implemented)
<findings that surfaced real issues but whose fixes are infeasible inside
 the bundle (e.g. "collect more training data", "consult domain SME"). Be
 honest — these don't count toward improvement lift but they're still real
 signal to the model owner.>

## Quantitative results table
| Test ID | Name | Verdict | Metric | Pass threshold |
|---------|------|---------|--------|----------------|
| quantN  | ...  | pass    | AUC=…  | AUC ≥ 0.75     |
| ...     |      |         |        |                |

## Qualitative findings
<bullet list — one bullet per qualitative test from results.json. Cite
 the methodology's `why_it_matters` so the reader knows why each item is
 here, then state the verdict and the evidence in one sentence.>

## Errored / deferred tests
<table or bullet list of any test whose verdict is `error` or `deferred`,
 with the reason. Be explicit about whether the gap is a Phase-B
 regression (re-run with a fixed validation_project) or a fundamental
 limitation of the bundle's data.>

## Ambiguous cases (flagged for human review)
<from interpretation.md — pull through any borderline results so a human
 can make the final call.>
```

## Constraints

- **No new information.** If a claim isn't supported by `results.json` or
  by a verbatim quote from `interpretation.md` / `methodology.md`, don't
  write it. This is the no-hallucination commitment from BIBLE v5.1.
- **Use markdown tables** for anything with more than two rows of
  structured data. Not bullet salad.
- **Recommendations must be feasible**: every "Hard finding" must point at
  a concrete code change someone could implement in the bundle's source.
  Vague advice ("be careful with overfitting") goes in "Soft findings"
  or doesn't go at all.
- **Qualitative findings without numeric evidence get a label**: append
  `(qualitative — awaiting quantitative confirmation)` to any item whose
  metric is null.
- Keep it readable: aim for ≤ 2000 words. A model owner shouldn't have to
  scroll past 5 pages of prose to learn whether to ship.

## Output verification

After writing, re-read `./results/report.md` and verify:

- Every hard-finding bullet references a test id from `results.json`.
- The verdict counts in your prose match `summary` from `results.json`.
- No section is empty — if a category has zero items (e.g. no errored
  tests), write a single line saying so explicitly. Empty sections look
  like bugs.

Phase D ends when `./results/report.md` exists and passes the
self-verification above.
