# Plan: Per-Model Literature Research Before Validation

**Date:** 2026-04-05
**Status:** PLAN ONLY — do not implement yet

---

## Concept

After S0 comprehension produces a ModelProfile (model type, framework, algorithm,
task domain, known issues), and BEFORE the methodology planner runs, Valoboros
performs a **targeted literature search** relevant to THIS specific model. The
search queries are dynamically generated from the model's characteristics, not
from a static list.

This enriches the knowledge base with model-specific insights that the methodology
planner can immediately use when designing the validation plan.

---

## Current Pipeline Flow

```
S0 comprehension → dep install → methodology planning → S1-S9
```

## Proposed Pipeline Flow

```
S0 comprehension → dep install → MODEL-SPECIFIC RESEARCH (NEW) → methodology planning → S1-S9
```

---

## What Changes

### 1. New module: `ouroboros/validation/model_researcher.py`

```python
class ModelResearcher:
    """Performs targeted literature research relevant to a specific model."""

    def __init__(self, profile: ModelProfile, knowledge_dir: Path, config: ValidationConfig):
        ...

    async def research(self) -> ModelResearchResult:
        """
        1. Generate search queries from the model profile
        2. Search arxiv (2-3 targeted queries)
        3. Score relevance against THIS model (not generic keywords)
        4. Read knowledge base for existing knowledge about this model type
        5. Call LLM to synthesize: "Given this model and these papers,
           what validation risks should I prioritize?"
        6. Write findings to knowledge base + bundle's methodology/ dir
        7. Return structured result
        """
```

### 2. Query generation — the key difference from background scanning

The background `LiteratureScanner` uses 7 static queries and rotates them.
The `ModelResearcher` generates queries **from the model profile**:

```python
def _generate_queries(self, profile: ModelProfile) -> list[str]:
    """Build 2-3 arxiv queries specific to this model."""
    queries = []

    # Query 1: Model type + framework + validation
    # e.g., "CatBoost regression validation overfitting"
    queries.append(
        f"cat:cs.LG AND ({profile.algorithm} OR {profile.framework}) "
        f"AND (validation OR testing OR evaluation)"
    )

    # Query 2: Task domain + known risks
    # e.g., "credit scoring model risk early repayment prediction"
    task_keywords = _extract_domain_keywords(profile.task_description)
    if task_keywords:
        queries.append(
            f"cat:cs.LG AND ({' OR '.join(task_keywords)}) "
            f"AND (model risk OR validation)"
        )

    # Query 3: Specific risks from comprehension gaps
    # e.g., "temporal leakage time series" if temporal_column was detected
    if profile.temporal_column:
        queries.append("cat:cs.LG AND (temporal leakage OR time series validation)")
    if profile.protected_attributes_candidates:
        queries.append("cat:cs.LG AND (fairness ML OR bias detection)")

    return queries[:3]  # max 3 queries to limit latency
```

**`_extract_domain_keywords()`** uses simple NLP (no LLM):
- Split task description into words
- Remove stopwords
- Keep nouns and domain terms (credit, scoring, churn, fraud, etc.)
- Take top 3-5 keywords

Alternatively, could use the LLM (one cheap call) to extract domain keywords
from the task description. This would be more accurate but adds cost/latency.

### 3. Relevance scoring — model-specific, not generic

The background scanner scores relevance against generic keywords ("validation",
"testing", "leakage"). The model researcher scores against THIS model:

```python
def _score_relevance(self, paper: dict, profile: ModelProfile) -> float:
    """Score how relevant a paper is to THIS specific model."""
    text = (paper["title"] + " " + paper["abstract"]).lower()
    score = 0.0

    # Generic validation keywords (low weight)
    for kw in ["validation", "testing", "evaluation"]:
        if kw in text:
            score += 0.1

    # Model-specific keywords (high weight)
    if profile.framework.lower() in text:
        score += 0.3  # paper mentions the same framework
    if profile.algorithm.lower() in text:
        score += 0.3  # paper mentions the same algorithm
    if profile.model_type.lower() in text:
        score += 0.2  # paper mentions the same model type

    # Task domain keywords (high weight)
    for kw in _extract_domain_keywords(profile.task_description):
        if kw.lower() in text:
            score += 0.2

    return min(score, 1.0)
```

### 4. LLM synthesis — "What should I watch for?"

After finding relevant papers, call the LLM once:

```
You are preparing to validate a {model_type} model ({algorithm}, {framework})
that {task_description}.

I found these recent papers relevant to this model type:
{paper_summaries}

I also know this from my knowledge base:
{existing_knowledge}

Based on this, what specific validation risks should I prioritize for this model?
What techniques from these papers could I apply?

Return a JSON with:
- risk_priorities: ordered list of risks specific to this model
- applicable_techniques: techniques from the papers I should try
- suggested_checks: new check ideas inspired by the papers
```

This output feeds directly into the methodology planner, which already accepts
`knowledge_references` and `risk_priorities`.

### 5. Result dataclass

```python
@dataclass
class ModelResearchResult:
    queries_used: list[str]
    papers_found: int
    relevant_papers: list[PaperSummary]
    risk_insights: list[str]          # model-specific risk priorities from LLM
    applicable_techniques: list[str]  # techniques from papers
    suggested_checks: list[dict]      # new check ideas from papers
    knowledge_written: list[str]      # files written to knowledge dir
```

### 6. Where results are saved

Two places:
- **Bundle-level:** `<bundle_dir>/methodology/research.md` — papers and insights
  relevant to THIS model (part of the validation project)
- **Knowledge base:** `knowledge/model_type_{type}.md` — appended with new
  insights that persist across validations

---

## Pipeline Integration

### File: `ouroboros/validation/pipeline.py`

Insert between dependency install and methodology planning:

```python
# --- Auto-install detected dependencies before S1 ---
await self._install_dependencies(profile)

# --- Per-model literature research (NEW) ---
self._log("Searching for literature relevant to this model...")
research = await self._research_model(profile)
if research:
    self._log(f"Found {len(research.relevant_papers)} relevant papers, "
              f"{len(research.risk_insights)} risk insights")

# --- Methodology planning (now enriched by research) ---
methodology = await self._plan_methodology(profile)
```

The methodology planner already reads the knowledge base. By writing research
results to the knowledge base BEFORE calling the planner, the planner
automatically picks them up — no changes needed to the planner itself.

### New method in ValidationPipeline:

```python
async def _research_model(self, profile: ModelProfile) -> Optional[ModelResearchResult]:
    try:
        from ouroboros.validation.model_researcher import ModelResearcher
        knowledge_dir = self._bundle_dir.parent.parent / "memory" / "knowledge"
        researcher = ModelResearcher(profile, knowledge_dir, self._config)
        return await researcher.research()
    except Exception as exc:
        self._log(f"Model research failed (non-blocking): {exc}")
        return None
```

**Key design decision:** Research failure is non-blocking. If arxiv is down or
the LLM call fails, the pipeline continues with whatever knowledge already exists.

---

## Methodology Planner Changes

**No code changes needed.** The planner already:
1. Calls `_gather_knowledge()` which reads `model_type_{type}.md` and `validation_patterns.md`
2. Passes knowledge to the LLM prompt
3. Accepts `risk_priorities` and `checks_to_create` in its output

The research step enriches these knowledge files before the planner reads them.
The planner benefits automatically.

However, the LLM prompt for the planner could be improved to explicitly mention:
"Recent literature research has been conducted — see the Knowledge Base section
for arxiv-sourced insights." This makes the LLM more likely to use the research.

**File:** `ouroboros/validation/methodology_planner.py` — minor prompt update.

---

## Config Changes

### File: `ouroboros/config.py`

| Key | Default | Description |
|-----|---------|-------------|
| `OUROBOROS_VALIDATION_PRE_RESEARCH` | `True` | Enable per-model arxiv research before validation |
| `OUROBOROS_VALIDATION_RESEARCH_MAX_QUERIES` | `3` | Max arxiv queries per model |
| `OUROBOROS_VALIDATION_RESEARCH_MAX_PAPERS` | `5` | Max papers to assess per model |

### File: `ouroboros/validation/types.py`

Add `pre_research: bool = True` and `research_max_queries: int = 3` to `ValidationConfig`.

---

## Prompt/Principle Changes

### File: `prompts/SYSTEM.md`

Add to the "Validation Domain Context" section:

```
**Pre-validation research:** Before validating each model, I search for recent
academic papers relevant to that specific model type, framework, and domain.
I use these to inform my methodology plan — not as generic background reading,
but as targeted preparation for THIS model. A CatBoost credit scoring model
gets different research than a PyTorch NLP model.
```

### File: `BIBLE.md`

No changes needed. The existing Validation Quality Standards already say:
"Eagerly search for new techniques." This is just implementing that principle
at the per-model level instead of only in background consciousness.

### File: `prompts/CONSCIOUSNESS.md`

Task #5 (Literature scan) remains as-is for background scanning. The per-model
research is a separate, pipeline-level activity. No change needed.

---

## Interaction Between Background Scanning and Per-Model Research

| Aspect | Background Scanner | Per-Model Researcher |
|--------|-------------------|---------------------|
| **When** | Between validations (consciousness idle) | During pipeline, after S0 |
| **Queries** | 7 static, rotating | 2-3 generated from model profile |
| **Relevance** | Generic keywords | Model-specific scoring |
| **LLM usage** | None (heuristic only) | One call for synthesis |
| **Writes to** | `knowledge/arxiv_recent.md` | `knowledge/model_type_{type}.md` + bundle `methodology/research.md` |
| **Benefits** | All future models | THIS model's methodology plan |
| **Cost** | Free (no LLM) | ~$0.01-0.03 per model (one Sonnet call) |

They complement each other:
- Background scanning casts a wide net at zero cost
- Per-model research does a focused deep dive at low cost
- Both write to the knowledge base, which the methodology planner reads

---

## Estimated Effort

| Component | LOC | LLM calls | Latency added |
|-----------|-----|-----------|---------------|
| `model_researcher.py` | ~200 | 1 (synthesis) | ~10-20s (arxiv API + LLM) |
| `pipeline.py` changes | ~15 | 0 | 0 |
| `types.py` changes | ~10 | 0 | 0 |
| `config.py` changes | ~5 | 0 | 0 |
| `methodology_planner.py` prompt tweak | ~5 | 0 | 0 |
| `SYSTEM.md` prompt addition | ~5 lines | 0 | 0 |
| Tests | ~80 | 0 | 0 |
| **Total** | **~320** | **1 per model** | **~10-20s per model** |

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Arxiv API is slow or down | Non-blocking: research failure doesn't stop the pipeline |
| Queries return irrelevant papers | Model-specific scoring filters them; LLM synthesis adds another filter |
| Adds 10-20s latency per validation | Configurable: `pre_research=False` to disable |
| LLM synthesis hallucinates risks | Methodology planner has its own LLM call that cross-checks; fallback plan ignores research |
| Cost per model increases | One Sonnet call (~$0.01-0.03) — negligible vs. S0 comprehension cost |
