# Plan: Per-Model Literature Research Before Validation

**Date:** 2026-04-05  
**Status:** PLAN ONLY — do not implement yet

---

## Concept

There are **two independent literature search mechanisms**, each serving a different
purpose. Neither replaces the other.

### Mechanism 1: Background Literature Scanning (EXISTING, unchanged)

- **When:** Between validations, during consciousness idle wakeups (task #5)
- **Queries:** 7 static queries, rotating across wakeups
- **Purpose:** Cast a wide net, accumulate general ML validation knowledge over time
- **Scope:** Generic — not tied to any specific model
- **LLM cost:** Zero (heuristic relevance scoring)
- **Implemented in:** `ouroboros/validation/literature_scanner.py`
- **Status:** Already implemented, no changes needed

### Mechanism 2: Per-Model Targeted Research (NEW)

- **When:** During the pipeline, after S0 comprehension, before methodology planning
- **Queries:** 2-3 queries dynamically generated from the model profile
- **Purpose:** Find papers specifically relevant to THIS model's type, framework,
  task domain, and detected risks — to inform the methodology plan
- **Scope:** Narrow and targeted — tied to the model being validated
- **LLM cost:** One call for synthesis (~$0.01-0.03)
- **To be implemented in:** `ouroboros/validation/model_researcher.py` (new file)

### How they complement each other

```
Between validations (consciousness):
  Background Scanner → generic papers → knowledge/arxiv_recent.md
                                         ↓
                              (accumulates over time)
                                         ↓
During validation (pipeline):
  S0 comprehension → model profile known
                         ↓
  Per-Model Researcher → targeted papers → knowledge/model_type_{type}.md
                         ↓                  + bundle methodology/research.md
  Methodology Planner reads BOTH:
    - arxiv_recent.md (from background scans)
    - model_type_{type}.md (from per-model research + reflection)
    - validation_patterns.md (from reflection engine)
                         ↓
  Better validation plan for THIS model
```

The background scanner provides a baseline of general knowledge that accumulates
passively. The per-model researcher adds a focused, model-specific research burst
right before the methodology planner needs it. The planner reads everything from
the knowledge base — it doesn't care which mechanism produced the knowledge.

---

## Current Pipeline Flow

```
S0 comprehension → dep install → methodology planning → S1-S9
```

## Proposed Pipeline Flow

```
S0 comprehension → dep install → PER-MODEL RESEARCH (NEW) → methodology planning → S1-S9
```

---

## New Module: `ouroboros/validation/model_researcher.py`

```python
class ModelResearcher:
    """Targeted literature research for a specific model before validation."""

    def __init__(self, profile: ModelProfile, knowledge_dir: Path, config: ValidationConfig):
        ...

    async def research(self) -> ModelResearchResult:
        """
        1. Generate 2-3 arxiv queries from the model profile
        2. Search arxiv for recent papers (last 90 days)
        3. Score relevance against THIS model (not generic keywords)
        4. Read existing knowledge base entries for this model type
        5. Call LLM: "Given this model and these papers + existing knowledge,
           what validation risks should I prioritize?"
        6. Write findings to:
           - knowledge/model_type_{type}.md (persists for future models)
           - bundle methodology/research.md (part of this validation project)
        7. Return structured result
        """
```

### Query generation — dynamic, model-specific

```python
def _generate_queries(self, profile: ModelProfile) -> list[str]:
    """Build 2-3 arxiv queries specific to this model."""
    queries = []

    # Query 1: Algorithm/framework + validation
    # e.g., "cat:cs.LG AND (CatBoost OR catboost) AND (validation OR testing)"
    queries.append(
        f"cat:cs.LG AND ({profile.algorithm} OR {profile.framework}) "
        f"AND (validation OR testing OR evaluation)"
    )

    # Query 2: Task domain + model risk
    # e.g., "cat:cs.LG AND (credit scoring OR early repayment) AND (model risk OR validation)"
    task_keywords = _extract_domain_keywords(profile.task_description)
    if task_keywords:
        queries.append(
            f"cat:cs.LG AND ({' OR '.join(task_keywords)}) "
            f"AND (model risk OR validation)"
        )

    # Query 3: Specific risks detected by S0 comprehension
    if profile.temporal_column:
        queries.append("cat:cs.LG AND (temporal leakage OR time series validation)")
    elif profile.protected_attributes_candidates:
        queries.append("cat:cs.LG AND (fairness ML OR bias detection)")
    elif profile.comprehension_gaps:
        # If there are gaps, search for the model type + common risks
        queries.append(
            f"cat:cs.LG AND {profile.model_type} AND (overfitting OR data leakage)"
        )

    return queries[:3]  # max 3 queries to limit latency
```

**`_extract_domain_keywords(task_description)`** — simple keyword extraction:
- Split task description into words
- Remove stopwords
- Keep nouns and domain terms
- Take top 3-5 keywords
- Optionally: one cheap LLM call to extract keywords (more accurate)

### Relevance scoring — model-specific

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
        score += 0.3
    if profile.algorithm.lower() in text:
        score += 0.3
    if profile.model_type.lower() in text:
        score += 0.2

    # Task domain keywords (high weight)
    for kw in _extract_domain_keywords(profile.task_description):
        if kw.lower() in text:
            score += 0.2

    return min(score, 1.0)
```

### LLM synthesis — "What should I watch for with THIS model?"

After finding relevant papers, one LLM call:

```
You are preparing to validate a {model_type} model ({algorithm}, {framework})
that {task_description}.

I found these recent papers relevant to this model type:
{paper_summaries}

I also know this from my knowledge base about {model_type} models:
{existing_knowledge}

Based on this, what specific validation risks should I prioritize for THIS model?
What techniques from these papers could I apply as validation checks?

Return JSON:
{
  "risk_insights": ["ordered list of model-specific risks"],
  "applicable_techniques": ["techniques from papers to try"],
  "suggested_checks": [{"check_id": "...", "description": "...", "rationale": "..."}]
}
```

---

## Result Dataclass

Add to `ouroboros/validation/types.py`:

```python
@dataclass
class ModelResearchResult:
    queries_used: list[str] = field(default_factory=list)
    papers_found: int = 0
    relevant_papers: list[PaperSummary] = field(default_factory=list)
    risk_insights: list[str] = field(default_factory=list)
    applicable_techniques: list[str] = field(default_factory=list)
    suggested_checks: list[dict] = field(default_factory=list)
    knowledge_written: list[str] = field(default_factory=list)
```

---

## Pipeline Integration

### File: `ouroboros/validation/pipeline.py`

Insert between dependency install and methodology planning:

```python
# --- Auto-install detected dependencies before S1 ---
await self._install_dependencies(profile)

# --- Per-model literature research (NEW) ---
if self._config.pre_research:
    self._log("Searching for literature relevant to this model...")
    research = await self._research_model(profile)
    if research and research.relevant_papers:
        self._log(f"Found {len(research.relevant_papers)} relevant papers, "
                  f"{len(research.risk_insights)} risk insights")
    else:
        self._log("No relevant papers found (non-blocking, continuing).")

# --- Methodology planning (now enriched by research) ---
methodology = await self._plan_methodology(profile)
```

New method:

```python
async def _research_model(self, profile: ModelProfile) -> Optional[ModelResearchResult]:
    try:
        from ouroboros.validation.model_researcher import ModelResearcher
        knowledge_dir = self._bundle_dir.parent.parent / "memory" / "knowledge"
        researcher = ModelResearcher(profile, knowledge_dir, self._config)
        result = await researcher.research()
        # Also save to bundle's methodology/ dir
        research_md = self._bundle_dir / "methodology" / "research.md"
        research_md.write_text(
            _format_research_md(result), encoding="utf-8"
        )
        return result
    except Exception as exc:
        self._log(f"Model research failed (non-blocking): {exc}")
        return None
```

**Key:** Research failure is non-blocking. If arxiv is down, the pipeline
continues with whatever knowledge already exists.

---

## Methodology Planner — No Code Changes Needed

The planner already reads the knowledge base via `_gather_knowledge()`:

```python
def _gather_knowledge(self) -> str:
    mt_file = self._knowledge_dir / f"model_type_{self._profile.model_type}.md"
    patterns_file = self._knowledge_dir / "validation_patterns.md"
    ...
```

The per-model researcher writes to `model_type_{type}.md` before the planner
runs. The planner picks up the enriched knowledge automatically.

**Optional enhancement:** Add a minor prompt tweak to the planner to mention
that recent research was conducted:

```python
# In _METHODOLOGY_PROMPT, add to the Knowledge Base section:
"Note: Targeted literature research was conducted for this specific model.
Recent arxiv findings are included in the knowledge base below."
```

This nudges the LLM to pay attention to the research findings.

---

## Config Changes

### File: `ouroboros/config.py` — add to SETTINGS_DEFAULTS:

| Key | Default | Description |
|-----|---------|-------------|
| `OUROBOROS_VALIDATION_PRE_RESEARCH` | `True` | Enable per-model arxiv research before validation |
| `OUROBOROS_VALIDATION_RESEARCH_MAX_QUERIES` | `3` | Max arxiv queries per model |
| `OUROBOROS_VALIDATION_RESEARCH_MAX_PAPERS` | `5` | Max papers to assess per model |

### File: `ouroboros/validation/types.py` — add to ValidationConfig:

```python
pre_research: bool = True
research_max_queries: int = 3
research_max_papers: int = 5
```

---

## Prompt Changes

### File: `prompts/SYSTEM.md` — add to "Validation Domain Context":

```
**Pre-validation research:** Before validating each model, I search for recent
academic papers relevant to that specific model type, framework, and domain.
I use these to inform my methodology plan — not as generic background reading,
but as targeted preparation for THIS model. A CatBoost credit scoring model
gets different research than a PyTorch NLP model. This is separate from and
in addition to my background literature scanning between validations.
```

### File: `BIBLE.md` — no changes needed

Already says "Eagerly search for new techniques." Both mechanisms implement this.

### File: `prompts/CONSCIOUSNESS.md` — no changes needed

Task #5 (background literature scan) remains unchanged. The per-model research
is a pipeline-level activity, not a consciousness task.

---

## Two Mechanisms Side by Side

| Aspect | Background Scanner (existing) | Per-Model Researcher (new) |
|--------|------------------------------|---------------------------|
| **Trigger** | Consciousness idle wakeup (every 3rd) | New model arrives for validation |
| **When in lifecycle** | Between validations | During pipeline, after S0 |
| **Queries** | 7 static, rotating | 2-3 generated from model profile |
| **Relevance scoring** | Generic keywords (+0.2 each) | Model-specific (+0.3 for framework/algorithm match) |
| **LLM usage** | None (pure heuristic) | One synthesis call |
| **Writes to** | `knowledge/arxiv_recent.md` | `knowledge/model_type_{type}.md` + `methodology/research.md` |
| **Benefits** | All future models (broad) | THIS model's methodology plan (targeted) |
| **Cost** | Free | ~$0.01-0.03 per model |
| **Latency** | N/A (background) | ~10-20s added to pipeline |
| **If fails** | Next wakeup retries | Non-blocking, pipeline continues |
| **Disableable** | `/bg stop` | `config.pre_research = False` |

**Both coexist. Neither replaces the other.** The background scanner builds a
broad foundation of knowledge over time at zero cost. The per-model researcher
adds a focused, timely research burst for the specific model about to be validated.

---

## Estimated Effort

| Component | LOC | LLM calls |
|-----------|-----|-----------|
| `model_researcher.py` (new) | ~200 | 1 per model |
| `pipeline.py` changes | ~20 | 0 |
| `types.py` changes | ~15 | 0 |
| `config.py` + `config_loader.py` | ~10 | 0 |
| `SYSTEM.md` prompt addition | ~5 lines | 0 |
| `methodology_planner.py` prompt tweak (optional) | ~3 lines | 0 |
| Tests | ~80 | 0 |
| **Total** | **~330** | **1 per model** |
