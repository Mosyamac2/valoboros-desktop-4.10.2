# Arxiv Search Architecture Review

**Date:** 2026-04-06
**Status:** Analysis — identifying gaps in existing implementation

---

## Current State: Two Mechanisms Already Exist

### Mechanism 1: Background Scanner (`literature_scanner.py`)

- **When:** Between validations, during consciousness idle wakeups
- **Queries:** 7 STATIC queries, rotating
- **Problem you identified:** These queries are generic, NOT related to any specific model
- **Purpose:** Broad knowledge accumulation over time
- **This is working as designed** — it's meant to be generic

### Mechanism 2: Per-Model Researcher (`model_researcher.py`)

- **When:** During the pipeline, AFTER S0 comprehension, BEFORE methodology planning
- **Queries:** DYNAMIC, generated from the model's `ModelProfile`:
  - Query 1: `"{algorithm} OR {framework}"` + validation
  - Query 2: Domain keywords extracted from `task_description`
  - Query 3: Risk-specific (temporal leakage if temporal column, fairness if protected attributes)
- **This already does what you want** — model-specific arxiv search

### Pipeline flow (as implemented):

```
S0 Comprehension → extracts ModelProfile (algorithm, framework, task, domain)
    ↓
Per-Model Researcher → generates queries from ModelProfile → searches arxiv
    ↓
Methodology Planner → reads knowledge base (enriched by researcher)
    ↓
S1-S9 checks
```

---

## Why It Didn't Work During Your Deployment

The per-model researcher **never ran** because:

1. **asyncio bug** (now fixed in commit `9bcb2fd`) — the `run_validation` tool
   crashed before the pipeline started, so no stage executed at all
2. When the agent improvised (manual validation without the pipeline), it skipped
   the researcher entirely — it went straight to code review
3. The background scanner DID run (consciousness wakeups), so you saw the static
   queries — which gave the impression that ALL arxiv search is static

**After the asyncio fix + Docker rebuild**, the pipeline should now execute
correctly: S0 → researcher (dynamic queries) → methodology → S1-S9.

---

## Gaps Found in the Current Implementation

### Gap 1: Researcher uses `comprehension_model` (Opus by default)

The per-model researcher calls the LLM for synthesis using
`config.comprehension_model` — which defaults to Opus. This is expensive
for a pre-validation research step. Should use a lighter model.

**Fix:** Add a dedicated `OUROBOROS_VALIDATION_RESEARCH_MODEL` config key
defaulting to Sonnet, or reuse `comprehension_model` but with `reasoning_effort="low"`
(already set to "low" — good).

**Status:** Low priority. The call already uses `reasoning_effort="low"`.

### Gap 2: No verification that researcher actually ran

When the pipeline runs, `validation.log` logs "Searching for literature..." and
"Found N papers" or "No relevant papers found." But if the researcher silently
fails (network error, arxiv timeout), there's no explicit finding in the report
saying "per-model research was attempted and failed."

**Fix:** Add a note to the report or methodology.md indicating whether research
ran and what it found. Currently, `methodology/research.md` is written if papers
are found, but nothing is written if the search fails.

**Recommendation:** Write a `methodology/research.md` even on failure:
```markdown
# Per-Model Literature Research
Status: FAILED (arxiv timeout)
Queries attempted: [list]
The methodology plan was created without arxiv-informed insights.
```

### Gap 3: Keyword extraction is basic

`_extract_domain_keywords()` in `model_researcher.py` splits the task description
into words, removes stopwords, and takes the longest 5 words. For the EAR CL model
with task "Predict early repayment rate for consumer loans", it would extract:
`["repayment", "consumer", "predict", "early", "loans"]`.

This is decent but could miss domain-specific compound terms like "early repayment"
or "prepayment risk" or "consumer credit". A smarter extraction could:
- Keep bigrams (2-word phrases) in addition to single words
- Recognize common financial/ML domain compound terms

**Fix:** Enhance `_extract_domain_keywords()` to also extract bigrams:

```python
def _extract_domain_keywords(self, text: str) -> list[str]:
    words = text.lower().split()
    # Single keywords (existing)
    singles = [w for w in words if w not in _STOPWORDS and len(w) >= 3 and w.isalpha()]
    # Bigrams
    bigrams = []
    for i in range(len(words) - 1):
        a, b = words[i], words[i+1]
        if a not in _STOPWORDS and b not in _STOPWORDS and len(a) >= 3 and len(b) >= 3:
            bigrams.append(f"{a} {b}")
    # Combine: bigrams first (more specific), then singles
    combined = bigrams[:3] + sorted(set(singles), key=len, reverse=True)[:3]
    return combined[:5]
```

This would extract: `["early repayment", "repayment rate", "consumer loans", "repayment", "consumer"]` — much better queries.

### Gap 4: The model description (.txt) is not used for query generation

The `model_researcher.py` only uses `profile.task_description` (the short task string
from intake). But the model often comes with a detailed description file
(`inputs/data_description.txt` or even a model_description.txt inside the ZIP).
This rich text could provide much better domain keywords.

**Fix:** In `_do_research()`, also read `inputs/data_description.txt` and
`inputs/task.txt` from the bundle directory, and extract keywords from all
available text.

**Implementation:**
```python
# In _do_research(), before generating queries:
bundle_dir = self._knowledge_dir.parent.parent  # or pass bundle_dir as param
extra_text = ""
for fname in ["inputs/task.txt", "inputs/data_description.txt"]:
    path = bundle_dir / fname
    if path.exists():
        extra_text += " " + path.read_text(encoding="utf-8")[:2000]
if extra_text:
    task_keywords = self._extract_domain_keywords(
        self._profile.task_description + " " + extra_text
    )
```

**Problem:** `ModelResearcher.__init__` takes `profile` and `knowledge_dir` but
NOT `bundle_dir`. The bundle_dir is needed to read the input files.

**Fix:** Add `bundle_dir` parameter to `ModelResearcher.__init__` and pass it
from `pipeline.py → _research_model()`.

---

## Summary of Recommended Changes

| # | Change | Impact | Effort | Priority |
|---|--------|--------|--------|----------|
| 1 | Verify researcher runs after asyncio fix | Confirms existing code works | Just test | **High — verify first** |
| 2 | Write research.md even on failure | Transparency | ~10 LOC | Medium |
| 3 | Bigram keyword extraction | Better queries → more relevant papers | ~15 LOC | Medium |
| 4 | Read data_description.txt for keywords | Richer domain context | ~20 LOC + add bundle_dir param | Medium |
| 5 | Dedicated research_model config (Sonnet) | Cost savings | ~5 LOC | Low (already uses low effort) |

**Total if implementing 2+3+4: ~45 LOC across 2 files.**

---

## What to Do First

**Step 0:** Rebuild Docker with the asyncio fix, upload a model, and check
`validation.log` for "Searching for literature relevant to this model..." and
"Found N papers." If these lines appear, the researcher IS running with
model-specific queries. The architecture is already correct — it just wasn't
executing due to the asyncio bug.

If the log shows the research step, the only improvements needed are Gaps 2-4
(better keywords, richer context, transparency on failure).
