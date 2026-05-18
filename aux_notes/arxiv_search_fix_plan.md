# Fix Plan: Arxiv Search Returning Irrelevant Papers

**Date:** 2026-04-06
**Status:** PLAN — do not implement yet
**Evidence:** Live deployment returned papers about muscle fatigue, microgravity,
and sparse autoencoders for a CatBoost consumer loan prepayment model.
Max relevance score: 0.2 out of 1.0.

---

## Root Cause Analysis

### Problem 1: Arxiv query category is too broad

All queries use `cat:cs.LG` (Machine Learning). This category has ~500 new
papers per week. The queries are drowned in noise.

**For a credit scoring model, better categories would be:**
- `cat:q-fin.RM` (Quantitative Finance → Risk Management)
- `cat:q-fin.ST` (Statistical Finance)
- `cat:stat.ML` (Statistics → Machine Learning)
- `cat:cs.CE` (Computational Engineering, Finance)

The query generator should map model domains to arxiv categories:

```python
_DOMAIN_TO_CATEGORIES = {
    "credit": "cat:q-fin.RM OR cat:q-fin.ST OR cat:cs.CE",
    "fraud": "cat:q-fin.RM OR cat:cs.CR",
    "churn": "cat:cs.LG",
    "medical": "cat:cs.LG OR cat:q-bio.QM",
    "nlp": "cat:cs.CL",
    "vision": "cat:cs.CV",
    "timeseries": "cat:stat.ML OR cat:cs.LG",
    "default": "cat:cs.LG OR cat:stat.ML",
}
```

### Problem 2: Keyword extraction misses domain terms

`_extract_domain_keywords("Predict early repayment rate for consumer loans")`
returns individual words: `["repayment", "consumer", "predict", "early", "loans"]`.

Arxiv search with `(repayment OR consumer OR predict)` matches ANYTHING with
"predict" in it — which is every ML paper. The word "predict" is too generic.

**Fixes needed:**
1. Add common generic ML words to the stopwords: `predict`, `model`, `rate`,
   `data`, `analysis`, `method`, `approach`, `system`, `based`, `using`, `learning`
2. Extract bigrams: "early repayment", "consumer loans", "repayment rate"
3. Keep compound domain terms intact

### Problem 3: Relevance scoring threshold is too low

Papers with score 0.1-0.2 are being included. These are noise — a score of 0.1
means only ONE generic keyword matched ("validation" or "testing"). The minimum
threshold should be 0.3 at least.

Current code:
```python
if score < 0.1:
    break  # below minimum relevance
```

Should be:
```python
if score < 0.3:
    break  # below minimum relevance
```

### Problem 4: Sorting by SubmittedDate returns latest papers, not most relevant

`sort_by=arxiv.SortCriterion.SubmittedDate` returns the most recent papers,
regardless of how well they match the query. For a niche domain query, the
most recent 10 papers in cs.LG are unlikely to be about credit scoring.

**Fix:** Use `sort_by=arxiv.SortCriterion.Relevance` — arxiv's own relevance
ranking, which considers query-term proximity in title and abstract.

### Problem 5: No financial/banking domain vocabulary

The query generator doesn't know financial terms. For a prepayment model, the
ideal queries would include terms like:
- "prepayment risk", "early repayment", "mortgage prepayment"
- "survival analysis credit", "hazard rate loan"
- "CatBoost financial", "gradient boosting credit risk"
- "CLTV", "customer lifetime value"

These come from the model description and data description, which are available
in `inputs/task.txt` and `inputs/data_description.txt` — but the researcher
currently only uses `profile.task_description` (the short task string).

---

## Detailed Fix Plan

### Fix A: Better query generation (~40 LOC)

**File:** `ouroboros/validation/model_researcher.py` → `_generate_queries()`

```python
def _generate_queries(self, profile: ModelProfile) -> list[str]:
    queries = []

    # 1. Determine arxiv categories from domain
    categories = self._detect_categories(profile)

    # 2. Extract rich keywords from ALL available text
    rich_text = profile.task_description
    # Also read data_description if available
    if hasattr(self, '_bundle_dir') and self._bundle_dir:
        for fname in ["inputs/task.txt", "inputs/data_description.txt"]:
            p = self._bundle_dir / fname
            if p.exists():
                rich_text += " " + p.read_text(encoding="utf-8")[:2000]

    domain_keywords = self._extract_domain_keywords(rich_text)
    bigrams = self._extract_bigrams(rich_text)

    # Query 1: Algorithm + domain bigrams (most specific)
    if bigrams:
        queries.append(
            f"{categories} AND ({profile.algorithm}) AND "
            f"({' OR '.join(bigrams[:2])})"
        )

    # Query 2: Domain keywords + validation/risk
    if domain_keywords:
        kw_str = " OR ".join(domain_keywords[:3])
        queries.append(
            f"{categories} AND ({kw_str}) AND (validation OR risk)"
        )

    # Query 3: Risk-specific (existing logic, but with better categories)
    if profile.temporal_column:
        queries.append(f"{categories} AND (temporal leakage OR time series split)")
    else:
        queries.append(
            f"{categories} AND ({profile.framework}) AND (model validation)"
        )

    return queries[:self._config.research_max_queries]
```

### Fix B: Domain-to-category mapping (~20 LOC)

**File:** `ouroboros/validation/model_researcher.py`

```python
_DOMAIN_KEYWORDS_TO_CATEGORIES = {
    "credit": "cat:q-fin.RM OR cat:q-fin.ST OR cat:cs.CE",
    "loan": "cat:q-fin.RM OR cat:q-fin.ST",
    "mortgage": "cat:q-fin.RM OR cat:q-fin.ST",
    "fraud": "cat:q-fin.RM OR cat:cs.CR",
    "insurance": "cat:q-fin.RM OR cat:stat.AP",
    "churn": "cat:cs.LG OR cat:stat.ML",
    "medical": "cat:cs.LG OR cat:q-bio.QM",
    "clinical": "cat:cs.LG OR cat:q-bio.QM",
    "image": "cat:cs.CV",
    "text": "cat:cs.CL",
    "nlp": "cat:cs.CL",
    "speech": "cat:cs.SD OR cat:cs.CL",
    "timeseries": "cat:stat.ML OR cat:cs.LG",
    "forecast": "cat:stat.ML OR cat:cs.LG",
}
_DEFAULT_CATEGORIES = "cat:cs.LG OR cat:stat.ML"

def _detect_categories(self, profile: ModelProfile) -> str:
    """Map model domain to arxiv categories."""
    text = (profile.task_description + " " + profile.model_type).lower()
    for keyword, categories in _DOMAIN_KEYWORDS_TO_CATEGORIES.items():
        if keyword in text:
            return categories
    return _DEFAULT_CATEGORIES
```

### Fix C: Better keyword extraction with bigrams + ML stopwords (~25 LOC)

**File:** `ouroboros/validation/model_researcher.py`

```python
# Add to module-level stopwords:
_ML_STOPWORDS = frozenset({
    "predict", "prediction", "model", "models", "rate", "data", "dataset",
    "analysis", "method", "approach", "system", "based", "using", "learning",
    "machine", "algorithm", "training", "train", "test", "feature", "features",
    "performance", "accuracy", "result", "results", "study", "paper",
    "proposed", "novel", "new", "improved", "framework",
})

def _extract_domain_keywords(self, text: str) -> list[str]:
    words = text.lower().split()
    keywords = [
        w for w in words
        if w not in _STOPWORDS and w not in _ML_STOPWORDS
        and len(w) >= 3 and w.isalpha()
    ]
    return sorted(set(keywords), key=len, reverse=True)[:5]

def _extract_bigrams(self, text: str) -> list[str]:
    """Extract meaningful 2-word phrases."""
    words = text.lower().split()
    bigrams = []
    for i in range(len(words) - 1):
        a, b = words[i], words[i+1]
        if (a not in _STOPWORDS and b not in _STOPWORDS
            and a not in _ML_STOPWORDS and b not in _ML_STOPWORDS
            and len(a) >= 3 and len(b) >= 3
            and a.isalpha() and b.isalpha()):
            bigrams.append(f'"{a} {b}"')  # quoted for exact phrase match
    return bigrams[:5]
```

### Fix D: Raise minimum relevance + sort by relevance (~5 LOC)

**File:** `ouroboros/validation/model_researcher.py`

In `_do_research()`:
```python
# Change threshold from 0.1 to 0.3:
if score < 0.3:
    break

# In _search_arxiv(), change sort:
sort_by=arxiv.SortCriterion.Relevance  # was: SubmittedDate
```

### Fix E: Pass bundle_dir to researcher (~10 LOC)

**File:** `ouroboros/validation/model_researcher.py` — add `bundle_dir` to `__init__`
**File:** `ouroboros/validation/pipeline.py` — pass `self._bundle_dir` when creating `ModelResearcher`

---

## Expected Result After Fixes

For the EAR CL model (CatBoost consumer loan prepayment), the queries would be:

```
Query 1: "cat:q-fin.RM OR cat:q-fin.ST OR cat:cs.CE" AND (CatBoost) AND ("early repayment" OR "consumer loans")
Query 2: "cat:q-fin.RM OR cat:q-fin.ST OR cat:cs.CE" AND (prepayment OR repayment OR consumer) AND (validation OR risk)
Query 3: "cat:q-fin.RM OR cat:q-fin.ST OR cat:cs.CE" AND (temporal leakage OR time series split)
```

Instead of current:
```
Query 1: cat:cs.LG AND (CatBoostRegressor OR catboost) AND (validation OR testing OR evaluation)
Query 2: cat:cs.LG AND (repayment OR consumer OR predict) AND (model risk OR validation)
Query 3: cat:cs.LG AND (temporal leakage OR time series validation)
```

The first set targets quantitative finance papers about CatBoost credit models.
The second set targets any ML paper that mentions "predict" — which is all of them.

---

## Implementation Summary

| Fix | What | LOC | File |
|-----|------|-----|------|
| A | Better query generation (bigrams, rich text) | ~40 | `model_researcher.py` |
| B | Domain-to-arxiv-category mapping | ~20 | `model_researcher.py` |
| C | ML stopwords + bigram extraction | ~25 | `model_researcher.py` |
| D | Relevance threshold 0.3 + sort by Relevance | ~5 | `model_researcher.py` |
| E | Pass bundle_dir for data_description access | ~10 | `model_researcher.py` + `pipeline.py` |
| **Total** | | **~100 LOC** | **2 files** |

Can be done in a single prompt to Claude Code.
