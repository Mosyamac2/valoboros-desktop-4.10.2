# Post-Live-Deployment Fixes: Lessons from First Real Validation

**Date:** 2026-04-06
**Based on:** First live deployment on 178.154.198.151, validation of EAR CL model
**Status:** PLAN — do not implement yet

---

## Problems Discovered

### Problem 1: asyncio event loop bug (CRITICAL)

**What happened:** `run_validation` tool called `asyncio.get_event_loop().run_until_complete(pipeline.run())`
from a ThreadPoolExecutor worker thread. Python 3.10+ raises `RuntimeError: There is no current event loop`
in non-main threads. The pipeline never ran via the tool — Ouroboros had to improvise.

**Root cause:** Three tool handlers in `ouroboros/tools/validation.py` use the deprecated
`asyncio.get_event_loop()` pattern:
- `_run_validation()` line 28
- `_run_validation_stage()` line 41
- `_run_improvement_cycle()` line ~165 (same pattern)

Also: `server_validation_api.py` → `api_validation_run()` calls `await pipeline.run()` directly
in an async endpoint — this is correct. But `_run_self_assessment` in `validation_feedback.py`
also uses `asyncio.get_event_loop()`.

**Fix:** Replace all `asyncio.get_event_loop().run_until_complete(...)` with `asyncio.run(...)`.

**Files to change:**

| File | Lines | Change |
|------|-------|--------|
| `ouroboros/tools/validation.py` | ~28, ~41, ~165 | `asyncio.get_event_loop().run_until_complete(x)` → `asyncio.run(x)` |
| `ouroboros/tools/validation_feedback.py` | `_run_self_assessment` | Same pattern fix |

**Estimated effort:** ~10 lines changed, zero risk.

### Problem 2: Agent skipped quantitative analysis (HIGH)

**What happened:** The pipeline failed (Problem 1), so Ouroboros improvised by doing
code review only (LLM-assisted). It produced 10 findings entirely from reading the
source code — zero quantitative analysis on the actual 100K-row CSV. When explicitly
asked to run quantitative analysis, it found 6 NEW findings that were arguably more
important than the code review (e.g., Siamese network `coef` has R²=0.0000000).

**Root cause:** Two factors:
1. Pipeline failure meant no sandbox checks (S2-S7) ran
2. The agent defaulted to LLM code review (what it CAN do without sandbox) and
   declared victory without attempting data analysis

**The deeper issue:** Nothing in the current prompts or constitution REQUIRES the agent
to touch the data. The BIBLE says "Qualitative before quantitative" — the agent
interpreted this as "qualitative is sufficient." The principle needs to be balanced:
qualitative analysis informs where to look, but **quantitative evidence is what proves
findings are real**.

**Fix:** Add a constitutional principle that data analysis is mandatory, not optional.
Qualitative analysis INFORMS, quantitative analysis CONFIRMS. Code review alone is
insufficient for a validation — it's only half the job.

### Problem 3: No methodology.md generated (MEDIUM)

**What happened:** The methodology planning step didn't produce the file because the
pipeline failed before reaching that step (Problem 1). When the agent improvised, it
skipped methodology planning entirely.

**Root cause:** The methodology planner runs inside the pipeline. When the pipeline
fails, there's no fallback that creates a methodology document for ad-hoc validation.

**Fix:** The agent should create methodology.md even when doing ad-hoc validation
(outside the pipeline). This is more of a prompt/behavioral fix than a code fix —
the agent should know that methodology documentation is mandatory for any validation,
whether pipeline-driven or manual.

---

## Detailed Fix Plan

### Fix 1: asyncio.run() replacement (CRITICAL, do first)

**File: `ouroboros/tools/validation.py`**

Change 3 functions:

```python
# _run_validation (line ~28)
# Before:
report = asyncio.get_event_loop().run_until_complete(pipeline.run())
# After:
report = asyncio.run(pipeline.run())

# _run_validation_stage (line ~41)
# Before:
result = asyncio.get_event_loop().run_until_complete(pipeline.run_single_stage(stage))
# After:
result = asyncio.run(pipeline.run_single_stage(stage))

# _run_improvement_cycle (line ~165)
# Before:
impl_result = asyncio.get_event_loop().run_until_complete(improver.implement())
# After:
impl_result = asyncio.run(improver.implement())
# AND:
reval_result = asyncio.get_event_loop().run_until_complete(reval.run(...))
# After:
reval_result = asyncio.run(reval.run(...))
```

**File: `ouroboros/tools/validation_feedback.py`**

```python
# _run_self_assessment
# Before:
assessments = asyncio.get_event_loop().run_until_complete(run_self_assessment(...))
# After:
assessments = asyncio.run(run_self_assessment(...))
```

**Test:** After fix, upload a model via web UI → "Upload & Validate" → pipeline should
run automatically without `RuntimeError`.

### Fix 2: BIBLE.md — Data-driven validation principle (HIGH)

**File: `BIBLE.md` → Validation Quality Standards**

Add a new bullet after "Qualitative before quantitative":

```markdown
- **Data is mandatory, not optional.** Every validation MUST include quantitative
  analysis of the actual data — descriptive statistics, correlations, distribution
  checks, metric computations. Code review alone is not validation — it is preliminary
  assessment. Qualitative analysis identifies WHERE to look; quantitative analysis
  proves whether the issue is REAL. A validation report without a single number
  computed from the data is incomplete. If the data cannot be loaded or processed,
  that itself is a critical finding — but the attempt must be made.
```

### Fix 3: SYSTEM.md — Reinforce data analysis requirement (HIGH)

**File: `prompts/SYSTEM.md` → Validation Domain Context → Methodology discipline**

Add:

```markdown
- **Touch the data.** Every validation must load and analyze the actual dataset.
  Compute descriptive statistics, correlations, target distribution, feature
  distributions, train/test overlap. Code review is not a substitute for data
  analysis. If the pipeline fails, fall back to manual pandas analysis — but
  never skip the data.
```

### Fix 4: SYSTEM.md — Drift detector pattern for "code review only" (MEDIUM)

**File: `prompts/SYSTEM.md` → Drift Detector patterns**

Add a new pattern:

```markdown
**"Code review theater"** — Producing findings entirely from reading code without
running any computation on the actual data. If all checks are type "llm_assisted"
and zero are "deterministic" or "sandbox" — the validation is incomplete.
```

### Fix 5: Synthesis prompt — require data evidence (MEDIUM)

**File: `ouroboros/validation/synthesis.py` → `_SYNTHESIS_PROMPT`**

Add to the IMPORTANT section:

```markdown
- If NO quantitative checks ran (all findings are from code review only),
  explicitly flag this: "WARNING: This validation is based on code review only.
  No quantitative analysis was performed on the data. Findings should be
  considered preliminary until confirmed with data analysis."
```

### Fix 6: Pipeline resilience — fallback on async failure (LOW)

**File: `ouroboros/validation/pipeline.py`**

This is not urgent after Fix 1, but for defense-in-depth: if any stage throws an
unexpected exception, the pipeline should log it and continue with remaining stages
rather than aborting entirely. Currently, an exception in `_run_stage_module()` is
caught and returns an error `ValidationStageResult` — this is correct. But the asyncio
error happened BEFORE the pipeline even started, in the tool handler. Fix 1 addresses
this at the source.

---

## Implementation Order

| # | Fix | Files | Effort | Priority |
|---|-----|-------|--------|----------|
| 1 | asyncio.run() | `tools/validation.py`, `tools/validation_feedback.py` | ~10 LOC | **Critical — do first** |
| 2 | BIBLE.md data-driven principle | `BIBLE.md` | ~5 lines | High |
| 3 | SYSTEM.md data analysis requirement | `prompts/SYSTEM.md` | ~5 lines | High |
| 4 | SYSTEM.md drift detector pattern | `prompts/SYSTEM.md` | ~3 lines | Medium |
| 5 | Synthesis prompt data warning | `ouroboros/validation/synthesis.py` | ~4 lines | Medium |
| 6 | Pipeline resilience | `ouroboros/validation/pipeline.py` | Optional | Low |

**Total: ~30 lines of changes across 5 files. Can be done in a single commit.**

---

## What These Fixes Prevent

| Scenario | Before | After |
|----------|--------|-------|
| User clicks "Upload & Validate" | Pipeline fails silently, agent improvises | Pipeline runs correctly via asyncio.run() |
| Agent does code review only | Declares victory, misses data-level issues | BIBLE requires data analysis; drift detector catches "code review theater" |
| Pipeline partially fails | Agent skips remaining stages | Synthesis warns "code review only — preliminary" |
| Findings without data evidence | Accepted as valid | BIBLE: "Data is mandatory, not optional" |
