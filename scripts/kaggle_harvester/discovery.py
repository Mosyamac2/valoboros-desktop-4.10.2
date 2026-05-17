"""Competition discovery + filtering.

Lists candidate competitions from Kaggle and post-filters them for the
harvester's needs (closed, tabular/NLP domain, Python kernels exist,
accepts API data downloads).

The Kaggle API does not expose a clean filter for "data downloadable
without rules acceptance" — we discover that lazily by probing each
competition with ``competition_files()`` (tier-1 acceptance handling per
plan §3.4).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from .kaggle_http import KaggleClient, KaggleHttpError

log = logging.getLogger(__name__)

# Heuristic keyword sets for domain inference. Used because the Kaggle
# `category` filter is coarse (e.g., "featured" / "research") and doesn't
# map to ML problem type. We rank by overlap with the title + description.
_TABULAR_KEYWORDS = {
    "tabular", "regression", "classification", "credit", "loan", "default",
    "prediction", "forecast", "sales", "demand", "fraud", "churn", "price",
    "claim", "score", "risk", "rating", "house", "store", "structured",
}
_NLP_KEYWORDS = {
    "nlp", "text", "language", "sentence", "sentiment", "review", "tweet",
    "tweets", "comment", "complaint", "question", "answer", "classification",
    "intent", "topic", "named entity", "translation", "summari", "spam",
    "toxic", "hate", "stance",
}
_CV_KEYWORDS = {
    "image", "vision", "object detection", "segmentation", "satellite",
    "x-ray", "histology", "pathology", "video", "photo", "pixel", "pixels",
    "ocr", "facial", "face", "draw", "drawing", "scene",
}


@dataclass
class CompetitionCandidate:
    """Subset of competition metadata the harvester actually uses."""

    slug: str
    title: str
    description: str
    category: str
    evaluation_metric: str
    deadline_iso: str
    organization: str
    url: str
    inferred_domain: str  # 'tabular' | 'nlp' | 'cv' | 'other'

    @property
    def is_closed(self) -> bool:
        if not self.deadline_iso:
            return False
        try:
            dt = datetime.fromisoformat(self.deadline_iso.replace("Z", "+00:00"))
        except ValueError:
            return False
        return dt < datetime.now(timezone.utc)


def _coalesce(d: dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return str(v)
    return default


def _infer_domain(title: str, description: str) -> str:
    """Cheap keyword-overlap classifier. Returns 'tabular' / 'nlp' / 'cv' / 'other'.

    Uses substring matching against a lowercased, punctuation-stripped form
    of the title + description, so simple plurals and inflections still
    count (``images`` matches ``image``).
    """
    text = f"{title} {description}".lower()
    text = re.sub(r"[^a-z\s]", " ", text)
    text = " " + " ".join(text.split()) + " "  # canonical whitespace
    def _count(keywords: set[str]) -> int:
        return sum(1 for kw in keywords if kw in text)
    counts = {
        "tabular": _count(_TABULAR_KEYWORDS),
        "nlp": _count(_NLP_KEYWORDS),
        "cv": _count(_CV_KEYWORDS),
    }
    best = max(counts, key=lambda k: counts[k])
    if counts[best] == 0:
        return "other"
    return best


def _to_candidate(raw: dict[str, Any]) -> Optional[CompetitionCandidate]:
    url = _coalesce(raw, "urlNullable", "url")
    if not url:
        return None
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    if not slug:
        return None
    title = _coalesce(raw, "titleNullable", "title")
    description = _coalesce(raw, "descriptionNullable", "description")
    category = _coalesce(raw, "categoryNullable", "category")
    metric = _coalesce(raw, "evaluationMetricNullable", "evaluationMetric")
    deadline = _coalesce(raw, "deadlineNullable", "deadline")
    org = _coalesce(raw, "organizationNameNullable", "organizationName")
    return CompetitionCandidate(
        slug=slug,
        title=title,
        description=description,
        category=category,
        evaluation_metric=metric,
        deadline_iso=deadline,
        organization=org,
        url=url,
        inferred_domain=_infer_domain(title, description),
    )


def iter_candidates(
    client: KaggleClient,
    *,
    domains: frozenset[str],
    seen_slugs: frozenset[str],
    closed_only: bool = True,
    max_pages: int = 20,
    page_size: int = 50,
) -> Iterator[CompetitionCandidate]:
    """Yield competition candidates matching the domain + freshness filters.

    Walks Kaggle's listing across multiple categories AND sort orders to
    surface as many candidates as possible (Kaggle's public listing API
    returns a small, sort-dependent window per call). Deduplicates by slug.
    """
    yielded: set[str] = set()
    categories = [
        "",                # default — broad
        "featured",
        "playground",
        "gettingStarted",
        "research",
        "community",       # community-hosted; large pool
    ]
    # Different sort orders open different windows over the listing.
    sort_orders = [
        "latestDeadline",  # closest-to-deadline first
        "numberOfTeams",   # popular established competitions
        "recentlyCreated", # fresh new competitions
        "prize",           # highest-value first
    ]
    for category in categories:
        for sort_by in sort_orders:
            for page in range(1, max_pages + 1):
                try:
                    batch = client.list_competitions(
                        category=category, page=page, page_size=page_size,
                        sort_by=sort_by,
                    )
                except KaggleHttpError as e:
                    log.warning(
                        "discovery: list_competitions(category=%r, sort=%r, "
                        "page=%d) failed: %s", category, sort_by, page, e,
                    )
                    break
                if not batch:
                    break
                produced_this_page = False
                for raw in batch:
                    cand = _to_candidate(raw)
                    if cand is None:
                        continue
                    if cand.slug in yielded or cand.slug in seen_slugs:
                        continue
                    if cand.inferred_domain not in domains:
                        continue
                    if closed_only and not cand.is_closed:
                        continue
                    yielded.add(cand.slug)
                    produced_this_page = True
                    yield cand
                if len(batch) < page_size:
                    break
                if not produced_this_page:
                    # No new candidates on a full page — likely we've seen all
                    # of this (category, sort) window. Stop walking deeper.
                    break


def candidate_from_slug(slug: str) -> CompetitionCandidate:
    """Build a minimal candidate for an explicit user-named slug.

    Used by the allow-list path: when the user names a specific competition,
    we don't rely on Kaggle's ``search=`` index (which silently omits older
    competitions). The slug alone is enough to download data and pull
    kernels; the validator's S0 comprehension will read the kernel + data
    and figure out the rest.

    Title and description are stub placeholders. The harvester appendix in
    the assembled bundle still records full provenance (kernel + license +
    subsample decision).
    """
    return CompetitionCandidate(
        slug=slug,
        title=slug.replace("-", " ").title(),
        description=(
            "This bundle was harvested via the user-curated allow-list path. "
            "Kaggle's public listing search did not surface this competition's "
            "metadata, so the description is a stub. Read the notebook and the "
            "data files to understand the task; consult "
            f"https://www.kaggle.com/competitions/{slug} for the original "
            "overview, evaluation metric, and rules."
        ),
        category="(allow-list)",
        evaluation_metric="(inferred — see Kaggle competition page)",
        deadline_iso="",
        organization="",
        url=f"https://www.kaggle.com/competitions/{slug}",
        inferred_domain="tabular",  # default; agent decides for real
    )


def probe_data_access(client: KaggleClient, cand: CompetitionCandidate) -> tuple[bool, str]:
    """Probe whether the competition's data files are accessible.

    Returns ``(accessible, skip_reason)``. ``skip_reason`` is empty when
    accessible. A 401/403 here means rules acceptance is required.

    Kaggle's file-list endpoint sometimes returns 200 with an empty list
    for getting-started competitions whose data is nonetheless downloadable
    via ``/competitions/data/download-all/<slug>``. We treat empty file
    lists as tentatively accessible — the actual download in the run loop
    will reveal whether the data is truly reachable, and a download failure
    is recorded as its own skip reason there.
    """
    try:
        ok, files = client.competition_files(cand.slug)
    except KaggleHttpError as e:
        if e.status in (401, 403):
            return False, "rules_not_accepted"
        return False, f"http_error:{e.status}"
    if not ok:
        return False, "rules_not_accepted_or_no_files"
    # ok == True; files may legitimately be empty (see docstring).
    return True, ""
