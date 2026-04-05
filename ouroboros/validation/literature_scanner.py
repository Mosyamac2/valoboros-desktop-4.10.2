"""
Valoboros — arxiv literature scanner.

Searches arxiv for recent ML model validation papers, scores relevance,
and writes findings to the knowledge base.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from ouroboros.validation.types import PaperSummary, ValidationConfig

log = logging.getLogger(__name__)

_ARXIV_QUERIES = [
    "cat:cs.LG AND (model validation OR model testing)",
    "cat:cs.LG AND (data leakage OR train test contamination)",
    "cat:cs.LG AND (fairness testing OR bias detection)",
    "cat:cs.LG AND (model robustness OR adversarial testing)",
    "cat:stat.ML AND (overfitting detection OR cross-validation)",
    "cat:cs.SE AND (automated testing machine learning)",
    "cat:cs.LG AND (model risk management OR model governance)",
]

_RELEVANCE_KEYWORDS = [
    "validation", "testing", "leakage", "fairness",
    "robustness", "overfitting", "bias", "drift",
    "reproducibility", "model risk", "audit",
]

_MAX_PAPERS_PER_SCAN = 5


class LiteratureScanner:
    """Searches arxiv for recent ML model validation papers."""

    def __init__(self, knowledge_dir: Path, config: ValidationConfig) -> None:
        self._knowledge_dir = Path(knowledge_dir)
        self._config = config
        self._history_file = self._knowledge_dir / "arxiv_scan_history.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan(self) -> list[PaperSummary]:
        """Search arxiv, filter, assess relevance, write to knowledge base."""
        return self._do_scan()

    def scan_sync(self) -> list[PaperSummary]:
        """Synchronous wrapper for testing."""
        return self._do_scan()

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _do_scan(self) -> list[PaperSummary]:
        query = self._get_current_query()
        log.info("Literature scan: query=%s", query)

        try:
            raw_papers = self._search_arxiv(query, max_results=10)
        except Exception as exc:
            log.warning("Arxiv search failed: %s", exc)
            return []

        # Filter out already-scanned
        history = self._load_scan_history()
        scanned_ids = set(history.get("scanned_ids", []))
        new_papers = [p for p in raw_papers if p["id"] not in scanned_ids]

        if not new_papers:
            self._record_scanned([])  # increment scan count
            return []

        # Score relevance (heuristic, no LLM cost)
        summaries: list[PaperSummary] = []
        for paper in new_papers[:_MAX_PAPERS_PER_SCAN]:
            score = self._heuristic_relevance(paper["title"], paper["abstract"])
            summaries.append(PaperSummary(
                arxiv_id=paper["id"],
                title=paper["title"],
                abstract=paper["abstract"][:500],
                url=paper["url"],
                relevance_score=score,
                applicable_technique=self._extract_technique(paper["title"], paper["abstract"]),
                proposed_check_idea=None,
            ))

        # Record scanned IDs
        new_ids = [p.arxiv_id for p in summaries]
        self._record_scanned(new_ids)

        # Write relevant papers to knowledge base
        relevant = [p for p in summaries if p.relevance_score >= 0.3]
        if relevant:
            self._write_to_knowledge(relevant)

        return summaries

    # ------------------------------------------------------------------
    # Arxiv search
    # ------------------------------------------------------------------

    def _search_arxiv(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Search arxiv using the arxiv Python library."""
        import arxiv

        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
        )
        results = []
        for paper in arxiv.Client().results(search):
            results.append({
                "id": paper.entry_id,
                "title": paper.title,
                "abstract": paper.summary,
                "url": paper.entry_id,
                "published": paper.published.isoformat() if paper.published else "",
                "categories": list(paper.categories),
            })
        return results

    # ------------------------------------------------------------------
    # Relevance scoring
    # ------------------------------------------------------------------

    def _heuristic_relevance(self, title: str, abstract: str) -> float:
        """Keyword-based relevance scoring (no LLM needed)."""
        text = (title + " " + abstract).lower()
        score = 0.0
        for keyword in _RELEVANCE_KEYWORDS:
            if keyword in text:
                score += 0.2
        return min(score, 1.0)

    @staticmethod
    def _extract_technique(title: str, abstract: str) -> str:
        """Extract a brief technique description from title."""
        # Simple: just use the title as the technique summary
        return title[:100]

    # ------------------------------------------------------------------
    # Query rotation
    # ------------------------------------------------------------------

    def _get_current_query(self) -> str:
        """Rotate through _ARXIV_QUERIES based on scan count."""
        history = self._load_scan_history()
        scan_count = history.get("scan_count", 0)
        idx = scan_count % len(_ARXIV_QUERIES)
        return _ARXIV_QUERIES[idx]

    # ------------------------------------------------------------------
    # History persistence
    # ------------------------------------------------------------------

    def _load_scan_history(self) -> dict[str, Any]:
        if not self._history_file.exists():
            return {"scanned_ids": [], "scan_count": 0}
        try:
            return json.loads(self._history_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"scanned_ids": [], "scan_count": 0}

    def _record_scanned(self, new_ids: list[str]) -> None:
        """Record scanned paper IDs and increment scan count."""
        history = self._load_scan_history()
        existing_ids = set(history.get("scanned_ids", []))
        existing_ids.update(new_ids)
        history["scanned_ids"] = sorted(existing_ids)
        history["scan_count"] = history.get("scan_count", 0) + 1
        history["last_scan"] = datetime.now(timezone.utc).isoformat()
        self._knowledge_dir.mkdir(parents=True, exist_ok=True)
        self._history_file.write_text(
            json.dumps(history, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Knowledge writing
    # ------------------------------------------------------------------

    def _write_to_knowledge(self, papers: list[PaperSummary]) -> None:
        """Append relevant papers to knowledge/arxiv_recent.md."""
        self._knowledge_dir.mkdir(parents=True, exist_ok=True)
        path = self._knowledge_dir / "arxiv_recent.md"

        lines: list[str] = []
        if path.exists():
            lines.append(path.read_text(encoding="utf-8").rstrip())
            lines.append("")

        if not lines or "# Recent Arxiv Papers" not in lines[0]:
            lines.insert(0, "# Recent Arxiv Papers on ML Validation\n")

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines.append(f"\n## Scan: {date_str}\n")
        for p in papers:
            lines.append(f"- **{p.title}** (relevance: {p.relevance_score:.1f})")
            lines.append(f"  {p.url}")
            if p.applicable_technique:
                lines.append(f"  Technique: {p.applicable_technique}")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
