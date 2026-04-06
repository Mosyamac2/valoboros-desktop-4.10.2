# Post-Live-Deployment Fixes — Implementation Prompts

**How to use:** Execute these 2 prompts sequentially.

**Start each session by saying:**
> Read `aux_notes/post_live_deployment_fixes.md` — this is the fix plan.
> Then execute the prompt below.

---

## Prompt 1 of 2: asyncio.run() Fix + Synthesis Warning

```
Read the fix plan in aux_notes/post_live_deployment_fixes.md, Fixes 1 and 5.

This is the CRITICAL fix. The validation pipeline never executes via tools
because asyncio.get_event_loop() raises RuntimeError in Python 3.10+ when
called from a ThreadPoolExecutor worker thread.

### Files to modify:

1. ouroboros/tools/validation.py — Replace ALL occurrences of
   asyncio.get_event_loop().run_until_complete(...) with asyncio.run(...)

   Three functions affected:
   - _run_validation() (~line 28)
   - _run_validation_stage() (~line 41)
   - _run_improvement_cycle() (~line 165 — there may be TWO calls:
     one for improver.implement() and one for reval.run())

   Read the file first to find all occurrences.

2. ouroboros/tools/validation_feedback.py — Same fix in _run_self_assessment()
   Replace asyncio.get_event_loop().run_until_complete(...) with asyncio.run(...)

3. ouroboros/validation/synthesis.py — Add data-analysis warning to _SYNTHESIS_PROMPT.
   Find the IMPORTANT section (near the end of the prompt string) and add:

   - If NO quantitative checks ran (all findings are from code review only),
     explicitly state: "WARNING: This validation is based on code review only.
     No quantitative analysis was performed on the data. Findings should be
     considered preliminary until confirmed with data analysis."

### Verify

```bash
# 1. Verify no get_event_loop remains in validation tools
grep -rn "get_event_loop" ouroboros/tools/validation.py ouroboros/tools/validation_feedback.py
# Should return NOTHING

# 2. Verify asyncio.run is used instead
grep -cn "asyncio.run(" ouroboros/tools/validation.py
# Should return >= 3

# 3. Verify synthesis warning added
grep -q "code review only" ouroboros/validation/synthesis.py && echo "synthesis warning: OK" || echo "synthesis warning: MISSING"

# 4. Run tests
.venv/bin/python -m pytest tests/test_validation_types.py tests/test_intake.py tests/test_integration.py tests/test_validation_api.py tests/test_synthesis_report.py --tb=short -q
```

All checks must pass.
```

---

## Prompt 2 of 2: BIBLE.md + SYSTEM.md Principles

```
Read the fix plan in aux_notes/post_live_deployment_fixes.md, Fixes 2, 3, and 4.

Update the constitution and system prompt to enforce data-driven validation.

### Files to modify:

1. BIBLE.md — In the "Validation Quality Standards" section, add a new bullet
   AFTER "Qualitative before quantitative" and BEFORE "No false positive alarms":

   - **Data is mandatory, not optional.** Every validation MUST include quantitative
     analysis of the actual data — descriptive statistics, correlations, distribution
     checks, metric computations. Code review alone is not validation — it is preliminary
     assessment. Qualitative analysis identifies WHERE to look; quantitative analysis
     proves whether the issue is REAL. A validation report without a single number
     computed from the data is incomplete. If the data cannot be loaded or processed,
     that itself is a critical finding — but the attempt must be made.

2. prompts/SYSTEM.md — In the "Methodology discipline" section (inside
   "Validation Domain Context"), add a new bullet:

   - **Touch the data.** Every validation must load and analyze the actual dataset.
     Compute descriptive statistics, correlations, target distribution, feature
     distributions, train/test overlap. Code review is not a substitute for data
     analysis. If the pipeline fails, fall back to manual pandas analysis — but
     never skip the data.

3. prompts/SYSTEM.md — In the "Drift Detector" section (the 6 patterns list),
   add a 7th pattern:

   **"Code review theater"** — Producing findings entirely from reading code without
   running any computation on the actual data. If all checks are type "llm_assisted"
   and zero are "deterministic" or "sandbox" — the validation is incomplete.

### Verify

```bash
# 1. Verify BIBLE.md has the new principle
grep -q "Data is mandatory" BIBLE.md && echo "BIBLE: OK" || echo "BIBLE: MISSING"

# 2. Verify SYSTEM.md has "touch the data"
grep -q "Touch the data" prompts/SYSTEM.md && echo "SYSTEM touch: OK" || echo "SYSTEM touch: MISSING"

# 3. Verify drift detector has new pattern
grep -q "Code review theater" prompts/SYSTEM.md && echo "SYSTEM drift: OK" || echo "SYSTEM drift: MISSING"

# 4. Verify nothing was accidentally deleted
wc -l BIBLE.md prompts/SYSTEM.md
# BIBLE.md should be >= 463 lines, SYSTEM.md should be >= 815 lines

# 5. Run tests
.venv/bin/python -m pytest tests/test_validation_types.py tests/test_intake.py tests/test_integration.py --tb=short -q
```

All checks must pass.
```

---

## Summary

| Prompt | Fixes | Files | LOC |
|--------|-------|-------|-----|
| 1 | asyncio.run() (critical) + synthesis warning | `tools/validation.py`, `tools/validation_feedback.py`, `validation/synthesis.py` | ~15 |
| 2 | Data-driven principle + drift pattern | `BIBLE.md`, `prompts/SYSTEM.md` | ~15 |
| **Total** | **5 of 6 fixes** | **5 files** | **~30** |

Fix 6 (pipeline resilience) is optional and not included — the pipeline already
catches stage-level exceptions. The asyncio fix (prompt 1) eliminates the root cause.
