"""
Valoboros — per-model targeted literature research.

Searches arxiv with queries generated from the model profile, scores relevance
against THIS specific model, and synthesizes insights via LLM.  Runs during the
pipeline after S0 comprehension, before methodology planning.

This is separate from the background LiteratureScanner which uses static queries
between validations.  Both coexist and complement each other.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ouroboros.validation.types import (
    ModelProfile,
    ModelResearchResult,
    PaperSummary,
    ValidationConfig,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stopwords: English + generic ML terms that match everything on arxiv
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "it", "its", "this", "that", "these", "those", "i", "we", "you", "he",
    "she", "they", "me", "him", "her", "us", "them", "my", "our", "your",
    "his", "their", "what", "which", "who", "whom", "how", "when", "where",
    "why", "not", "no", "nor", "so", "if", "then", "than", "too", "very",
    "just", "about", "above", "after", "again", "all", "also", "am", "any",
    "because", "before", "between", "both", "each", "few", "more", "most",
    "other", "over", "same", "some", "such", "only", "own", "into", "up",
})

_ML_STOPWORDS = frozenset({
    "predict", "prediction", "model", "models", "rate", "data", "dataset",
    "analysis", "method", "approach", "system", "based", "using", "learning",
    "machine", "algorithm", "training", "train", "test", "feature", "features",
    "performance", "accuracy", "result", "results", "study", "paper",
    "proposed", "novel", "new", "improved", "framework", "detection",
    "classification", "regression", "deep", "neural", "network",
})

# ---------------------------------------------------------------------------
# Domain → arxiv category mapping
# ---------------------------------------------------------------------------

_DOMAIN_TO_CATEGORIES: dict[str, str] = {
    "credit": "cat:q-fin.RM OR cat:q-fin.ST OR cat:cs.CE",
    "loan": "cat:q-fin.RM OR cat:q-fin.ST OR cat:cs.CE",
    "mortgage": "cat:q-fin.RM OR cat:q-fin.ST",
    "prepayment": "cat:q-fin.RM OR cat:q-fin.ST",
    "repayment": "cat:q-fin.RM OR cat:q-fin.ST",
    "default": "cat:q-fin.RM OR cat:q-fin.ST",
    "scoring": "cat:q-fin.RM OR cat:q-fin.ST OR cat:cs.LG",
    "fraud": "cat:q-fin.RM OR cat:cs.CR OR cat:cs.LG",
    "insurance": "cat:q-fin.RM OR cat:stat.AP",
    "risk": "cat:q-fin.RM OR cat:stat.ML",
    "churn": "cat:cs.LG OR cat:stat.ML",
    "medical": "cat:cs.LG OR cat:q-bio.QM",
    "clinical": "cat:cs.LG OR cat:q-bio.QM",
    "health": "cat:cs.LG OR cat:q-bio.QM",
    "image": "cat:cs.CV",
    "vision": "cat:cs.CV",
    "text": "cat:cs.CL",
    "nlp": "cat:cs.CL",
    "language": "cat:cs.CL",
    "speech": "cat:cs.SD OR cat:cs.CL",
    "timeseries": "cat:stat.ML OR cat:cs.LG",
    "forecast": "cat:stat.ML OR cat:cs.LG",
    "recommender": "cat:cs.IR OR cat:cs.LG",
}
_DEFAULT_CATEGORIES = "cat:cs.LG OR cat:stat.ML"

_MIN_RELEVANCE = 0.3

_SYNTHESIS_PROMPT = """\
You are preparing to validate a {model_type} model ({algorithm}, {framework})
that {task_description}.

I found these recent papers relevant to this model type:
{paper_summaries}

I also know this from my knowledge base about {model_type} models:
{existing_knowledge}

Based on this, what specific validation risks should I prioritize for THIS model?
What techniques from these papers could I apply as validation checks?

Return JSON:
{{
  "risk_insights": ["ordered list of model-specific risks"],
  "applicable_techniques": ["techniques from papers to try"],
  "suggested_checks": [{{"check_id": "S{{N}}.NAME", "description": "...", "rationale": "..."}}]
}}

Return ONLY JSON. No explanation.
"""


class ModelResearcher:
    """Targeted literature research for a specific model before validation."""

    def __init__(
        self,
        profile: ModelProfile,
        knowledge_dir: Path,
        config: ValidationConfig,
        bundle_dir: Optional[Path] = None,
    ) -> None:
        self._profile = profile
        self._knowledge_dir = Path(knowledge_dir)
        self._config = config
        self._bundle_dir = Path(bundle_dir) if bundle_dir else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def research(self) -> ModelResearchResult:
        """Search arxiv, score relevance, synthesize insights."""
        return await asyncio.to_thread(self._do_research)

    def research_sync(self) -> ModelResearchResult:
        """Synchronous wrapper for testing."""
        return self._do_research()

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _do_research(self) -> ModelResearchResult:
        queries = self._generate_queries(self._profile)
        log.info("Per-model research: %d queries for %s/%s",
                 len(queries), self._profile.framework, self._profile.algorithm)
        for i, q in enumerate(queries):
            log.info("  Query %d: %s", i + 1, q)

        # Search arxiv
        all_papers: dict[str, dict] = {}  # deduplicate by id
        for query in queries:
            try:
                papers = self._search_arxiv(query, max_results=10)
                for p in papers:
                    if p["id"] not in all_papers:
                        all_papers[p["id"]] = p
            except Exception as exc:
                log.warning("Arxiv query failed (%s): %s", query[:80], exc)

        if not all_papers:
            return ModelResearchResult(queries_used=queries, papers_found=0)

        # Score and rank
        scored: list[tuple[float, dict]] = []
        for paper in all_papers.values():
            score = self._score_relevance(paper, self._profile)
            scored.append((score, paper))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Keep top N relevant papers (minimum relevance: 0.3)
        top_papers: list[PaperSummary] = []
        for score, paper in scored[:self._config.research_max_papers]:
            if score < _MIN_RELEVANCE:
                break
            top_papers.append(PaperSummary(
                arxiv_id=paper["id"],
                title=paper["title"],
                abstract=paper.get("abstract", "")[:500],
                url=paper.get("url", paper["id"]),
                relevance_score=score,
                applicable_technique=paper["title"][:100],
                proposed_check_idea=None,
            ))

        # Read existing knowledge
        existing_knowledge = self._read_knowledge()

        # LLM synthesis (if relevant papers found)
        risk_insights: list[str] = []
        applicable_techniques: list[str] = []
        suggested_checks: list[dict] = []

        if top_papers:
            try:
                synthesis = self._synthesize(top_papers, existing_knowledge, self._profile)
                risk_insights = synthesis.get("risk_insights", [])
                applicable_techniques = synthesis.get("applicable_techniques", [])
                suggested_checks = synthesis.get("suggested_checks", [])
            except Exception as exc:
                log.warning("LLM synthesis failed, using heuristic: %s", exc)
                risk_insights = self._heuristic_risks()
        else:
            # No relevant papers — still provide heuristic risks
            risk_insights = self._heuristic_risks()

        result = ModelResearchResult(
            queries_used=queries,
            papers_found=len(all_papers),
            relevant_papers=top_papers,
            risk_insights=risk_insights,
            applicable_techniques=applicable_techniques,
            suggested_checks=suggested_checks,
            knowledge_written=[],
        )

        # Write to knowledge base
        if top_papers or risk_insights:
            result.knowledge_written = self._write_knowledge(result, self._profile)

        return result

    # ------------------------------------------------------------------
    # Query generation
    # ------------------------------------------------------------------

    def _generate_queries(self, profile: ModelProfile) -> list[str]:
        """Build 2-3 arxiv queries specific to this model."""
        queries: list[str] = []
        categories = self._detect_categories(profile)

        # Gather all available text for keyword extraction
        rich_text = profile.task_description
        if self._bundle_dir:
            for fname in ["inputs/task.txt", "inputs/data_description.txt"]:
                p = self._bundle_dir / fname
                if p.exists():
                    try:
                        rich_text += " " + p.read_text(encoding="utf-8")[:2000]
                    except Exception:
                        pass

        domain_keywords = self._extract_domain_keywords(rich_text)
        bigrams = self._extract_bigrams(rich_text)

        # Query 1: Algorithm + domain bigrams (most specific)
        if bigrams:
            queries.append(
                f"({categories}) AND ({profile.algorithm} OR {profile.framework}) "
                f"AND ({' OR '.join(bigrams[:2])})"
            )
        else:
            queries.append(
                f"({categories}) AND ({profile.algorithm} OR {profile.framework}) "
                f"AND (validation OR evaluation)"
            )

        # Query 2: Domain keywords + risk/validation
        if domain_keywords:
            kw_str = " OR ".join(domain_keywords[:3])
            queries.append(
                f"({categories}) AND ({kw_str}) AND (validation OR risk OR evaluation)"
            )

        # Query 3: Risk-specific
        if profile.temporal_column:
            queries.append(f"({categories}) AND (temporal leakage OR time series split OR temporal validation)")
        elif profile.protected_attributes_candidates:
            queries.append(f"({categories}) AND (fairness OR bias detection OR disparate impact)")
        else:
            queries.append(
                f"({categories}) AND ({profile.framework}) AND (model validation OR model risk)"
            )

        return queries[:self._config.research_max_queries]

    def _detect_categories(self, profile: ModelProfile) -> str:
        """Map model domain to arxiv categories based on task description."""
        text = (profile.task_description + " " + profile.model_type).lower()
        for keyword, categories in _DOMAIN_TO_CATEGORIES.items():
            if keyword in text:
                return categories
        return _DEFAULT_CATEGORIES

    def _extract_domain_keywords(self, text: str) -> list[str]:
        """Extract domain-specific keywords, removing both English and ML stopwords."""
        words = text.lower().split()
        keywords = [
            w for w in words
            if w not in _STOPWORDS and w not in _ML_STOPWORDS
            and len(w) >= 3 and w.isalpha()
        ]
        return sorted(set(keywords), key=len, reverse=True)[:5]

    def _extract_bigrams(self, text: str) -> list[str]:
        """Extract meaningful 2-word phrases for more specific arxiv queries."""
        words = text.lower().split()
        bigrams: list[str] = []
        for i in range(len(words) - 1):
            a, b = words[i], words[i + 1]
            if (a not in _STOPWORDS and b not in _STOPWORDS
                    and a not in _ML_STOPWORDS and b not in _ML_STOPWORDS
                    and len(a) >= 3 and len(b) >= 3
                    and a.isalpha() and b.isalpha()):
                bigrams.append(f'"{a} {b}"')  # quoted for exact phrase match
        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for bg in bigrams:
            if bg not in seen:
                seen.add(bg)
                unique.append(bg)
        return unique[:5]

    # ------------------------------------------------------------------
    # Arxiv search
    # ------------------------------------------------------------------

    def _search_arxiv(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Search arxiv using the arxiv Python library."""
        import arxiv

        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.Relevance,  # sort by relevance, not date
        )
        results = []
        for paper in arxiv.Client().results(search):
            results.append({
                "id": paper.entry_id,
                "title": paper.title,
                "abstract": paper.summary,
                "url": paper.entry_id,
                "published": paper.published.isoformat() if paper.published else "",
            })
        return results

    # ------------------------------------------------------------------
    # Relevance scoring
    # ------------------------------------------------------------------

    def _score_relevance(self, paper: dict[str, Any], profile: ModelProfile) -> float:
        """Score how relevant a paper is to THIS specific model."""
        text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
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

        # Task domain keywords (extracted with ML stopwords removed)
        rich_text = profile.task_description
        if self._bundle_dir:
            for fname in ["inputs/task.txt"]:
                p = self._bundle_dir / fname
                if p.exists():
                    try:
                        rich_text += " " + p.read_text(encoding="utf-8")[:500]
                    except Exception:
                        pass
        for kw in self._extract_domain_keywords(rich_text):
            if kw in text:
                score += 0.2

        return min(score, 1.0)

    # ------------------------------------------------------------------
    # Knowledge reading/writing
    # ------------------------------------------------------------------

    def _read_knowledge(self) -> str:
        """Read existing knowledge base entries for this model type."""
        parts: list[str] = []
        for filename in [
            f"model_type_{self._profile.model_type}.md",
            "validation_patterns.md",
        ]:
            path = self._knowledge_dir / filename
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8")[:3000]
                    parts.append(f"### {filename}\n{content}")
                except Exception:
                    pass
        return "\n\n".join(parts)

    def _write_knowledge(self, result: ModelResearchResult, profile: ModelProfile) -> list[str]:
        """Append research findings to knowledge base. Does NOT overwrite."""
        self._knowledge_dir.mkdir(parents=True, exist_ok=True)
        filename = f"model_type_{profile.model_type}.md"
        path = self._knowledge_dir / filename

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines: list[str] = [
            f"\n## Research for {profile.algorithm} ({date_str})\n",
        ]
        if result.risk_insights:
            lines.append("**Risk insights:**")
            for r in result.risk_insights:
                lines.append(f"- {r}")
        if result.applicable_techniques:
            lines.append("\n**Applicable techniques:**")
            for t in result.applicable_techniques:
                lines.append(f"- {t}")
        if result.relevant_papers:
            lines.append("\n**Relevant papers:**")
            for p in result.relevant_papers:
                lines.append(f"- [{p.relevance_score:.1f}] {p.title}")
                lines.append(f"  {p.url}")
        if not result.relevant_papers:
            lines.append("\n*No sufficiently relevant papers found (min score: 0.3).*")
        lines.append(f"\nQueries used: {result.queries_used}")
        lines.append("")

        new_section = "\n".join(lines)

        if path.exists():
            existing = path.read_text(encoding="utf-8")
            path.write_text(existing + new_section, encoding="utf-8")
        else:
            header = f"# {profile.model_type.title()} Model Knowledge\n"
            path.write_text(header + new_section, encoding="utf-8")

        return [filename]

    # ------------------------------------------------------------------
    # LLM synthesis
    # ------------------------------------------------------------------

    def _synthesize(
        self,
        papers: list[PaperSummary],
        existing_knowledge: str,
        profile: ModelProfile,
    ) -> dict[str, Any]:
        """Call LLM to synthesize risk insights from papers + knowledge."""
        from ouroboros.llm import LLMClient

        paper_text = "\n".join(
            f"- {p.title} (relevance: {p.relevance_score:.1f})\n  {p.abstract[:200]}"
            for p in papers
        )
        prompt = _SYNTHESIS_PROMPT.format(
            model_type=profile.model_type,
            algorithm=profile.algorithm,
            framework=profile.framework,
            task_description=profile.task_description,
            paper_summaries=paper_text,
            existing_knowledge=existing_knowledge or "(no existing knowledge)",
        )

        client = LLMClient()
        response, _usage = client.chat(
            messages=[
                {"role": "system", "content": "You synthesize ML validation research. Return only JSON."},
                {"role": "user", "content": prompt},
            ],
            model=self._config.comprehension_model,
            reasoning_effort="low",
            max_tokens=2048,
        )

        text = response.get("content", "")
        if isinstance(text, list):
            text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]

        return json.loads(text.strip())

    def _heuristic_risks(self) -> list[str]:
        """Fallback risk insights when LLM is unavailable."""
        risks = []
        if self._profile.temporal_column:
            risks.append("Temporal leakage — model uses time-series data")
        risks.append(f"Overfitting — validate train/test gap for {self._profile.algorithm}")
        if self._profile.model_type == "classification":
            risks.append("Class imbalance — check if target is balanced")
        if self._profile.protected_attributes_candidates:
            risks.append("Fairness — protected attributes detected")
        risks.append("Data leakage — check feature-target correlations")
        return risks
