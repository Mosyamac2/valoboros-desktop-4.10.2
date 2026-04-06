"""Tests for per-model targeted literature research."""
import json
import pytest
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
    """Extracts meaningful keywords, removes stopwords and ML stopwords."""
    researcher = ModelResearcher.__new__(ModelResearcher)
    researcher._bundle_dir = None
    keywords = researcher._extract_domain_keywords(
        "Predict early repayment rate for consumer loans in banking"
    )
    assert len(keywords) >= 2
    assert "for" not in keywords
    assert "in" not in keywords
    assert "predict" not in keywords  # ML stopword
    assert "rate" not in keywords     # ML stopword
    assert any(kw in keywords for kw in ["repayment", "consumer", "banking", "loans", "early"])


def test_extract_bigrams():
    """Extracts meaningful bigrams from text."""
    researcher = ModelResearcher.__new__(ModelResearcher)
    researcher._bundle_dir = None
    bigrams = researcher._extract_bigrams(
        "Predict early repayment rate for consumer loans in banking sector"
    )
    assert len(bigrams) >= 1
    # Should contain quoted phrases like '"early repayment"' or '"consumer loans"'
    combined = " ".join(bigrams)
    assert "early repayment" in combined or "consumer loans" in combined


def test_detect_categories_credit(knowledge_dir, profile):
    """Credit/loan model maps to q-fin categories."""
    researcher = ModelResearcher(profile, knowledge_dir, ValidationConfig())
    cats = researcher._detect_categories(profile)
    assert "q-fin" in cats  # should be quantitative finance, not just cs.LG


def test_detect_categories_default(knowledge_dir):
    """Unknown domain falls back to cs.LG."""
    profile = ModelProfile(
        bundle_id="test", task_description="Some generic task",
        model_type="other", model_type_confidence=0.5,
        framework="sklearn", framework_confidence=0.9,
        algorithm="SomeAlgo", data_format="tabular",
    )
    researcher = ModelResearcher(profile, knowledge_dir, ValidationConfig())
    cats = researcher._detect_categories(profile)
    assert "cs.LG" in cats


def test_score_relevance_high_for_matching_paper(knowledge_dir, profile):
    """Paper about CatBoost regression validation scores high."""
    researcher = ModelResearcher(profile, knowledge_dir, ValidationConfig())
    paper = {
        "title": "Validation of CatBoost Models for Financial Risk Assessment",
        "abstract": "We evaluate CatBoost regression models for credit risk, "
                    "focusing on temporal leakage and overfitting detection...",
    }
    score = researcher._score_relevance(paper, profile)
    assert score >= 0.5


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
    assert "Existing knowledge" in content
    assert "temporal leakage" in content
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
    assert json.dumps(d)


def test_config_has_research_fields():
    """ValidationConfig includes pre_research fields."""
    cfg = ValidationConfig()
    assert cfg.pre_research is True
    assert cfg.research_max_queries == 3
    assert cfg.research_max_papers == 5
    cfg2 = ValidationConfig(pre_research=False, research_max_queries=1)
    assert cfg2.pre_research is False
    assert cfg2.research_max_queries == 1


def test_heuristic_risks_temporal(knowledge_dir, profile):
    """Heuristic fallback includes temporal leakage when temporal_column is set."""
    researcher = ModelResearcher(profile, knowledge_dir, ValidationConfig())
    risks = researcher._heuristic_risks()
    assert any("temporal" in r.lower() for r in risks)


def test_heuristic_risks_fairness(knowledge_dir):
    """Heuristic fallback includes fairness when protected attributes detected."""
    profile = ModelProfile(
        bundle_id="test", task_description="Credit scoring",
        model_type="classification", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="LogisticRegression", data_format="tabular",
        protected_attributes_candidates=["gender", "age"],
    )
    researcher = ModelResearcher(profile, knowledge_dir, ValidationConfig())
    risks = researcher._heuristic_risks()
    assert any("fairness" in r.lower() for r in risks)
