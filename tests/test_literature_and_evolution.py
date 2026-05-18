"""Tests for literature scanner and methodology evolver."""
import json
import pytest
from pathlib import Path
from ouroboros.validation.types import ValidationConfig, PaperSummary, EvolutionAction


@pytest.fixture
def knowledge_dir(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    return d


# --- Literature Scanner Tests ---

def test_scanner_heuristic_relevance(knowledge_dir):
    """Heuristic relevance scoring works without LLM."""
    from ouroboros.validation.literature_scanner import LiteratureScanner
    scanner = LiteratureScanner(knowledge_dir, ValidationConfig())
    score = scanner._heuristic_relevance(
        "A New Method for Data Leakage Detection in ML Pipelines",
        "We propose a validation framework for detecting train-test leakage and overfitting..."
    )
    assert score >= 0.4  # "leakage", "validation", "overfitting" should hit


def test_scanner_heuristic_irrelevant(knowledge_dir):
    """Irrelevant papers get low scores."""
    from ouroboros.validation.literature_scanner import LiteratureScanner
    scanner = LiteratureScanner(knowledge_dir, ValidationConfig())
    score = scanner._heuristic_relevance(
        "Quantum Computing for Drug Discovery",
        "We apply quantum algorithms to molecular simulation..."
    )
    assert score < 0.2


def test_scan_history_persists(knowledge_dir):
    """Scanned paper IDs are recorded and survive re-instantiation."""
    from ouroboros.validation.literature_scanner import LiteratureScanner
    scanner1 = LiteratureScanner(knowledge_dir, ValidationConfig())
    scanner1._record_scanned(["arxiv:2401.00001", "arxiv:2401.00002"])
    scanner2 = LiteratureScanner(knowledge_dir, ValidationConfig())
    history = scanner2._load_scan_history()
    assert "arxiv:2401.00001" in history["scanned_ids"]
    assert "arxiv:2401.00002" in history["scanned_ids"]


def test_query_rotation(knowledge_dir):
    """Scanner rotates through queries on successive scans."""
    from ouroboros.validation.literature_scanner import LiteratureScanner, _ARXIV_QUERIES
    scanner = LiteratureScanner(knowledge_dir, ValidationConfig())
    q1 = scanner._get_current_query()
    scanner._record_scanned([])  # increments scan count
    q2 = scanner._get_current_query()
    assert q1 != q2 or len(_ARXIV_QUERIES) == 1


def test_scan_count_increments(knowledge_dir):
    """Each _record_scanned call increments the scan count."""
    from ouroboros.validation.literature_scanner import LiteratureScanner
    scanner = LiteratureScanner(knowledge_dir, ValidationConfig())
    scanner._record_scanned([])
    scanner._record_scanned([])
    scanner._record_scanned([])
    history = scanner._load_scan_history()
    assert history["scan_count"] == 3


# --- Methodology Evolver Tests ---

def test_evolver_no_targets_returns_none(tmp_path):
    """With no evolution targets, evolver returns None."""
    from ouroboros.validation.methodology_evolver import MethodologyEvolver
    from ouroboros.validation.check_registry import CheckRegistry
    from ouroboros.validation.effectiveness import EffectivenessTracker

    repo_dir = Path(__file__).parent.parent
    registry = CheckRegistry(repo_dir)
    tracker = EffectivenessTracker(tmp_path)
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    evolver = MethodologyEvolver(repo_dir, registry, tracker, knowledge_dir, ValidationConfig())
    action = evolver.evolve_sync()
    assert action is None


def test_evolver_detects_fix_target(tmp_path):
    """Evolver identifies a check with low precision as a fix target."""
    from ouroboros.validation.methodology_evolver import MethodologyEvolver
    from ouroboros.validation.check_registry import CheckRegistry
    from ouroboros.validation.effectiveness import EffectivenessTracker

    repo_dir = Path(__file__).parent.parent
    registry = CheckRegistry(repo_dir)
    tracker = EffectivenessTracker(tmp_path)
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    for i in range(5):
        tracker.record_finding_feedback("S8.CODE_SMELLS", f"b{i}", "false_positive", "human", 1.0)

    evolver = MethodologyEvolver(repo_dir, registry, tracker, knowledge_dir, ValidationConfig())
    targets = evolver._get_targets()
    assert len(targets) > 0
    assert any(t.target_type == "fix_check" for t in targets)


def test_evolver_fix_returns_action(tmp_path):
    """Evolver returns a fix_check action for a low-precision check."""
    from ouroboros.validation.methodology_evolver import MethodologyEvolver
    from ouroboros.validation.check_registry import CheckRegistry
    from ouroboros.validation.effectiveness import EffectivenessTracker

    repo_dir = Path(__file__).parent.parent
    registry = CheckRegistry(repo_dir)
    tracker = EffectivenessTracker(tmp_path)
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    for i in range(5):
        tracker.record_finding_feedback("S8.CODE_SMELLS", f"b{i}", "false_positive", "human", 1.0)

    evolver = MethodologyEvolver(repo_dir, registry, tracker, knowledge_dir, ValidationConfig())
    action = evolver.evolve_sync()
    assert action is not None
    assert action.action_type == "fix_check"
    assert "S8.CODE_SMELLS" in action.check_id or "S8.CODE_SMELLS" in action.description


def test_paper_summary_dataclass():
    """PaperSummary serialization."""
    ps = PaperSummary(
        arxiv_id="2401.00001", title="Test Paper",
        abstract="Testing...", url="https://arxiv.org/abs/2401.00001",
        relevance_score=0.8, applicable_technique="New leakage detection",
        proposed_check_idea="S4.ADVANCED_LEAKAGE",
    )
    assert ps.relevance_score == 0.8
    assert ps.proposed_check_idea is not None


def test_evolution_action_dataclass():
    """EvolutionAction serialization."""
    ea = EvolutionAction(
        action_type="create_check", check_id="S4.NEW",
        description="Created new check", success=True, error_message=None,
    )
    assert ea.success is True
