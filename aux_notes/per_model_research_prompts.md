# Per-Model Research Implementation Prompts

**How to use:** Execute these 3 prompts sequentially in separate Claude Code sessions.
Each prompt contains implementation AND tests.

**Start each session by saying:**
> Read `aux_notes/per_model_research_plan.md` — this is the detailed plan.
> Then execute the prompt below.

**Dependency:** Prompt 1 → Prompt 2 → Prompt 3 (strictly sequential).

---

## Prompt 1 of 3: ModelResearcher Core + Config + Types

```
Read the per-model research plan in aux_notes/per_model_research_plan.md.

This prompt creates the ModelResearcher module and supporting types/config.
Do NOT modify pipeline.py or prompts yet — that's prompts 2 and 3.

### Files to create:

1. ouroboros/validation/model_researcher.py — ModelResearcher class with:

   - __init__(self, profile: ModelProfile, knowledge_dir: Path, config: ValidationConfig)

   - async research() -> ModelResearchResult:
     a. Call _generate_queries(profile) to build 2-3 arxiv queries
     b. For each query, call _search_arxiv(query) — reuse the arxiv
        library the same way literature_scanner.py does
     c. Deduplicate papers across queries (by arxiv_id)
     d. Score each paper via _score_relevance(paper, profile)
     e. Keep top N by relevance (config.research_max_papers)
     f. Read existing knowledge via _read_knowledge()
     g. If relevant papers found, call LLM for synthesis via _synthesize()
     h. Write results to knowledge_dir/model_type_{type}.md (append, not overwrite)
     i. Return ModelResearchResult

   - research_sync() -> ModelResearchResult: sync wrapper for testing

   - _generate_queries(profile) -> list[str]:
     Query 1: f"cat:cs.LG AND ({profile.algorithm} OR {profile.framework}) AND (validation OR testing OR evaluation)"
     Query 2: extract domain keywords from task_description, build query
     Query 3: risk-specific based on profile (temporal_column → temporal leakage,
              protected_attributes → fairness, else model_type + overfitting/leakage)
     Return up to config.research_max_queries queries.

   - _extract_domain_keywords(text) -> list[str]:
     Split text into words, remove English stopwords (hardcode a small set of
     ~50 common stopwords), remove words shorter than 3 chars, return top 5
     remaining words sorted by length (longer = more specific).

   - _search_arxiv(query, max_results=10) -> list[dict]:
     Same pattern as literature_scanner.py._search_arxiv() — use the arxiv
     library. Return list of {"id", "title", "abstract", "url", "published"}.

   - _score_relevance(paper: dict, profile: ModelProfile) -> float:
     Generic keywords ("validation", "testing", "evaluation") → +0.1 each
     Framework match (profile.framework in text) → +0.3
     Algorithm match (profile.algorithm in text) → +0.3
     Model type match (profile.model_type in text) → +0.2
     Task domain keywords match → +0.2 each
     Cap at 1.0.

   - _read_knowledge() -> str:
     Read model_type_{type}.md and validation_patterns.md from knowledge_dir.
     Return concatenated content (truncated to 3000 chars each).

   - _synthesize(papers, existing_knowledge, profile) -> dict:
     One LLM call asking: "Given this model and these papers + existing knowledge,
     what validation risks should I prioritize?"
     Parse JSON response with risk_insights, applicable_techniques, suggested_checks.
     Fallback if LLM fails: return risk_insights from profile (heuristic).

   - _write_knowledge(result, profile) -> list[str]:
     Append to knowledge_dir/model_type_{type}.md with a dated section.
     Do NOT overwrite existing content — append.
     Return list of filenames written.

### Files to modify:

2. ouroboros/validation/types.py — Add ModelResearchResult dataclass:
   - queries_used: list[str]
   - papers_found: int
   - relevant_papers: list[PaperSummary]  (reuse existing PaperSummary)
   - risk_insights: list[str]
   - applicable_techniques: list[str]
   - suggested_checks: list[dict]
   - knowledge_written: list[str]
   - to_dict() and from_dict()

3. ouroboros/validation/types.py — Add to ValidationConfig:
   - pre_research: bool = True
   - research_max_queries: int = 3
   - research_max_papers: int = 5

4. ouroboros/config.py — Add to SETTINGS_DEFAULTS:
   - "OUROBOROS_VALIDATION_PRE_RESEARCH": True
   - "OUROBOROS_VALIDATION_RESEARCH_MAX_QUERIES": 3
   - "OUROBOROS_VALIDATION_RESEARCH_MAX_PAPERS": 5

5. ouroboros/validation/config_loader.py — Add 3 new keys to _KEY_MAP:
   - "OUROBOROS_VALIDATION_PRE_RESEARCH": "pre_research"
   - "OUROBOROS_VALIDATION_RESEARCH_MAX_QUERIES": "research_max_queries"
   - "OUROBOROS_VALIDATION_RESEARCH_MAX_PAPERS": "research_max_papers"

### Verify

Write and run tests/test_model_researcher.py:

```python
"""Tests for per-model targeted literature research."""
import json, pytest
from pathlib import Path
from ouroboros.validation.model_researcher import ModelResearcher
from ouroboros.validation.types import ModelProfile, ValidationConfig, ModelResearchResult


@pytest.fixture
def knowledge_dir(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    return d


@pytest.fixture
def profile():
    return ModelProfile(
        bundle_id="test", task_description="Predict early repayment rate for consumer loans",
        model_type="regression", model_type_confidence=0.9,
        framework="catboost", framework_confidence=0.9,
        algorithm="CatBoost", data_format="tabular",
        target_column="EAR_y", temporal_column="report_date",
    )


def test_generate_queries_uses_model_profile(knowledge_dir, profile):
    """Queries include the model's algorithm and framework."""
    researcher = ModelResearcher(profile, knowledge_dir, ValidationConfig())
    queries = researcher._generate_queries(profile)
    assert len(queries) >= 2
    assert any("CatBoost" in q or "catboost" in q for q in queries)
    # Should have temporal leakage query since temporal_column is set
    assert any("temporal" in q.lower() for q in queries)


def test_generate_queries_uses_task_keywords(knowledge_dir):
    """Queries include domain keywords from the task description."""
    profile = ModelProfile(
        bundle_id="test", task_description="Credit scoring fraud detection model",
        model_type="classification", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="RandomForest", data_format="tabular",
    )
    researcher = ModelResearcher(profile, knowledge_dir, ValidationConfig())
    queries = researcher._generate_queries(profile)
    combined = " ".join(queries).lower()
    assert "credit" in combined or "fraud" in combined or "scoring" in combined


def test_extract_domain_keywords():
    """Extracts meaningful keywords, removes stopwords."""
    from ouroboros.validation.model_researcher import ModelResearcher
    researcher = ModelResearcher.__new__(ModelResearcher)
    keywords = researcher._extract_domain_keywords(
        "Predict early repayment rate for consumer loans in banking"
    )
    assert len(keywords) >= 2
    # Should keep domain words, remove stopwords
    assert "for" not in keywords
    assert "in" not in keywords
    assert any(kw in keywords for kw in ["repayment", "consumer", "banking", "loans", "predict"])


def test_score_relevance_high_for_matching_paper(knowledge_dir, profile):
    """Paper about CatBoost regression validation scores high."""
    researcher = ModelResearcher(profile, knowledge_dir, ValidationConfig())
    paper = {
        "title": "Validation of CatBoost Models for Financial Risk Assessment",
        "abstract": "We evaluate CatBoost regression models for credit risk, "
                    "focusing on temporal leakage and overfitting detection...",
    }
    score = researcher._score_relevance(paper, profile)
    assert score >= 0.5  # CatBoost + regression + validation + temporal


def test_score_relevance_low_for_unrelated_paper(knowledge_dir, profile):
    """Paper about quantum computing scores low."""
    researcher = ModelResearcher(profile, knowledge_dir, ValidationConfig())
    paper = {
        "title": "Quantum Advantage for Protein Folding",
        "abstract": "We demonstrate quantum speedup for molecular dynamics...",
    }
    score = researcher._score_relevance(paper, profile)
    assert score < 0.2


def test_read_knowledge_returns_existing(knowledge_dir, profile):
    """_read_knowledge reads existing model_type file."""
    (knowledge_dir / "model_type_regression.md").write_text(
        "# Regression\nWatch for overfitting in small datasets.\n"
    )
    researcher = ModelResearcher(profile, knowledge_dir, ValidationConfig())
    kb = researcher._read_knowledge()
    assert "overfitting" in kb.lower()


def test_write_knowledge_appends(knowledge_dir, profile):
    """Writing research results appends to existing file, doesn't overwrite."""
    existing = "# Existing knowledge\nSome old content.\n"
    (knowledge_dir / "model_type_regression.md").write_text(existing)
    researcher = ModelResearcher(profile, knowledge_dir, ValidationConfig())
    result = ModelResearchResult(
        queries_used=["test query"],
        papers_found=1,
        relevant_papers=[],
        risk_insights=["temporal leakage is critical"],
        applicable_techniques=["time-based cross-validation"],
        suggested_checks=[],
        knowledge_written=[],
    )
    written = researcher._write_knowledge(result, profile)
    content = (knowledge_dir / "model_type_regression.md").read_text()
    assert "Existing knowledge" in content  # old content preserved
    assert "temporal leakage" in content     # new content appended
    assert len(written) >= 1


def test_research_result_dataclass():
    """ModelResearchResult roundtrip."""
    r = ModelResearchResult(
        queries_used=["q1"], papers_found=5,
        relevant_papers=[], risk_insights=["risk1"],
        applicable_techniques=["tech1"],
        suggested_checks=[{"check_id": "S4.NEW"}],
        knowledge_written=["model_type_regression.md"],
    )
    d = r.to_dict()
    r2 = ModelResearchResult.from_dict(d)
    assert r2.papers_found == 5
    assert r2.risk_insights == ["risk1"]
    assert json.dumps(d)  # JSON-serializable


def test_config_has_research_fields():
    """ValidationConfig includes pre_research fields."""
    cfg = ValidationConfig()
    assert cfg.pre_research is True
    assert cfg.research_max_queries == 3
    assert cfg.research_max_papers == 5
    cfg2 = ValidationConfig(pre_research=False, research_max_queries=1)
    assert cfg2.pre_research is False
    assert cfg2.research_max_queries == 1
```

Run: `.venv/bin/python -m pytest tests/test_model_researcher.py -v`
All tests must pass.
```

---

## Prompt 2 of 3: Pipeline Integration

```
Read the per-model research plan in aux_notes/per_model_research_plan.md,
sections "Pipeline Integration" and "Methodology Planner — No Code Changes Needed".

Read the existing ouroboros/validation/pipeline.py — understand where the
methodology planning step is, and insert the research step before it.

Also read ouroboros/validation/methodology_planner.py — understand the
_METHODOLOGY_PROMPT to add the optional enhancement about research findings.

### Files to modify:

1. ouroboros/validation/pipeline.py:
   - Add _research_model(profile) method that:
     a. Checks config.pre_research — if False, skip
     b. Instantiates ModelResearcher with profile, knowledge_dir, config
     c. Calls researcher.research()
     d. Writes methodology/research.md to the bundle dir with formatted results
     e. Returns ModelResearchResult or None on failure
     f. Logs progress and results
     g. Non-blocking: catches all exceptions, logs, returns None

   - Insert the research step in run() between _install_dependencies() and
     _plan_methodology():

     ```python
     # --- Per-model literature research ---
     if self._config.pre_research:
         self._log("Searching for literature relevant to this model...")
         research = await self._research_model(profile)
         if research and research.relevant_papers:
             self._log(f"Found {len(research.relevant_papers)} relevant papers, "
                       f"{len(research.risk_insights)} risk insights")
         else:
             self._log("No relevant papers found (non-blocking, continuing).")
     ```

2. ouroboros/validation/methodology_planner.py:
   - Add a small note to _METHODOLOGY_PROMPT in the Knowledge Base section:

     After the "{knowledge}" placeholder, add:
     "Note: If recent arxiv findings appear above, they were gathered
     specifically for this model. Consider them when designing the plan."

   - In _gather_knowledge(), also try to read knowledge_dir / "arxiv_recent.md"
     (from background scanner) in addition to model_type and validation_patterns.

### Verify

Write and run tests/test_research_pipeline_integration.py:

```python
"""Tests for per-model research integration into the pipeline."""
import pytest, zipfile
from pathlib import Path
from ouroboros.validation.types import ValidationConfig
from ouroboros.validation.pipeline import ValidationPipeline


def _make_test_bundle(tmp_path):
    from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
    code_zip = tmp_path / "code.zip"
    with zipfile.ZipFile(code_zip, "w") as zf:
        zf.writestr("train.py", "import pandas\nprint('hello')\n")
    val_dir = tmp_path / "validations"
    val_dir.mkdir()
    bundle_id = _ingest_model_artifacts_impl(val_dir, str(code_zip), "Test credit scoring model")
    return val_dir / bundle_id


def test_research_disabled_skips(tmp_path):
    """When pre_research=False, _research_model returns None without error."""
    import asyncio
    bundle_dir = _make_test_bundle(tmp_path)
    config = ValidationConfig(pre_research=False, auto_self_assess=False, auto_improve=False)
    pipeline = ValidationPipeline(bundle_dir.name, bundle_dir, Path(__file__).parent.parent, config)
    from ouroboros.validation.types import ModelProfile
    profile = ModelProfile(
        bundle_id="test", task_description="test",
        model_type="classification", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="RF", data_format="tabular",
    )
    result = asyncio.run(pipeline._research_model(profile))
    assert result is None  # disabled, should return None


def test_research_failure_is_nonblocking(tmp_path):
    """If arxiv/LLM fails, _research_model returns None without crashing."""
    import asyncio
    from unittest.mock import patch
    bundle_dir = _make_test_bundle(tmp_path)
    config = ValidationConfig(pre_research=True, auto_self_assess=False, auto_improve=False)
    pipeline = ValidationPipeline(bundle_dir.name, bundle_dir, Path(__file__).parent.parent, config)
    from ouroboros.validation.types import ModelProfile
    profile = ModelProfile(
        bundle_id="test", task_description="test",
        model_type="regression", model_type_confidence=0.9,
        framework="catboost", framework_confidence=0.9,
        algorithm="CatBoost", data_format="tabular",
    )
    # Mock arxiv to raise an error
    with patch("ouroboros.validation.model_researcher.ModelResearcher.research",
               side_effect=ConnectionError("arxiv down")):
        result = asyncio.run(pipeline._research_model(profile))
    assert result is None  # failed gracefully


def test_methodology_planner_reads_arxiv_recent(tmp_path):
    """Planner's _gather_knowledge includes arxiv_recent.md if it exists."""
    from ouroboros.validation.methodology_planner import MethodologyPlanner
    from ouroboros.validation.check_registry import CheckRegistry
    from ouroboros.validation.types import ModelProfile
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "arxiv_recent.md").write_text(
        "# Recent Papers\n- CatBoost validation technique from 2026\n"
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "methodology").mkdir()
    profile = ModelProfile(
        bundle_id="test", task_description="test",
        model_type="regression", model_type_confidence=0.9,
        framework="catboost", framework_confidence=0.9,
        algorithm="CatBoost", data_format="tabular",
    )
    repo_dir = Path(__file__).parent.parent
    planner = MethodologyPlanner(bundle_dir, profile, CheckRegistry(repo_dir), ValidationConfig(), knowledge_dir)
    kb = planner._gather_knowledge()
    assert "CatBoost validation technique" in kb
```

Run: `.venv/bin/python -m pytest tests/test_research_pipeline_integration.py -v`
All tests must pass.
```

---

## Prompt 3 of 3: Prompt Update + Tutorial Update

```
Read the per-model research plan in aux_notes/per_model_research_plan.md,
section "Prompt Changes".

This prompt updates the system prompt and the tutorial documentation.
No new Python modules — only text files.

### Files to modify:

1. prompts/SYSTEM.md — In the "Validation Domain Context" section (search for
   "**Key processes:**"), add after the existing process flow:

   **Pre-validation research:** Before validating each model, I search for recent
   academic papers relevant to that specific model type, framework, and domain.
   I use these to inform my methodology plan — not as generic background reading,
   but as targeted preparation for THIS model. A CatBoost credit scoring model
   gets different research than a PyTorch NLP model. This is separate from and
   in addition to my background literature scanning between validations.

2. aux_notes/valoboros_tutorial.md — In Part 1, Phase 4 (Methodology Planning),
   add a note BEFORE the methodology planning description:

   #### Phase 3.5: Per-Model Literature Research (if enabled)

   ```
   Pipeline → ModelResearcher.research()
   ```

   Before designing the methodology plan, Valoboros searches arxiv for papers
   specifically relevant to THIS model. Queries are generated from the model
   profile — the algorithm, framework, task domain, and detected risks.

   This is different from the background literature scanning (Part 3b):
   - Background scanning: generic queries, between validations, benefits future models
   - Per-model research: targeted queries, during pipeline, benefits THIS model

   Example: For a CatBoost regression model predicting loan early repayment,
   the queries might be:
   - "cat:cs.LG AND (CatBoost OR catboost) AND (validation OR testing)"
   - "cat:cs.LG AND (repayment OR consumer OR loans) AND (model risk)"
   - "cat:cs.LG AND (temporal leakage OR time series validation)"

   Results are written to the knowledge base, which the methodology planner
   reads immediately after. If arxiv is down or no relevant papers are found,
   the pipeline continues without delay.

   Disable with: `ValidationConfig(pre_research=False)`

### Verify

```bash
# Verify SYSTEM.md has the new text
grep -q "Pre-validation research" prompts/SYSTEM.md && echo "SYSTEM.md: OK" || echo "SYSTEM.md: MISSING"

# Verify tutorial has the new section
grep -q "Per-Model Literature Research" aux_notes/valoboros_tutorial.md && echo "Tutorial: OK" || echo "Tutorial: MISSING"

# Run full validation test suite to confirm nothing broke
.venv/bin/python -m pytest tests/test_validation_types.py tests/test_sandbox.py tests/test_check_registry.py tests/test_seed_checks.py tests/test_stage_orchestrators.py tests/test_intake.py tests/test_synthesis_report.py tests/test_effectiveness.py tests/test_improvement_cycle.py tests/test_integration.py tests/test_dependency_extractor.py tests/test_watcher.py tests/test_reflection_engine.py tests/test_methodology_planner.py tests/test_literature_and_evolution.py tests/test_project_structure.py tests/test_model_researcher.py tests/test_research_pipeline_integration.py --tb=short -q
```

All greps should say "OK". All tests must pass.
```

---

## Summary

| Prompt | Creates | Modifies | Tests | LOC est. |
|--------|---------|----------|-------|----------|
| 1 | `model_researcher.py` | `types.py`, `config.py`, `config_loader.py` | 10 | ~250 |
| 2 | — | `pipeline.py`, `methodology_planner.py` | 3 | ~40 |
| 3 | — | `SYSTEM.md`, `valoboros_tutorial.md` | grep + full suite | ~30 lines text |
| **Total** | **1 new file** | **6 modified** | **13 new** | **~320** |
