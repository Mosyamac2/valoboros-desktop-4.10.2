"""Thin HTTP client for Kaggle's REST API.

Replaces the legacy ``kaggle`` Python SDK. Talks to ``https://www.kaggle.com/api/v1``
with Bearer-token auth (or legacy Basic Auth for older tokens). Adds:

- Retry with exponential backoff on transient errors.
- Per-call rate-limit pacing (default ~2 req/sec to stay polite).
- Streaming downloads for large files.
- Honest error surfaces — 401/403/404 propagate as ``KaggleHttpError``
  with the response body attached so callers can branch on them.
"""

from __future__ import annotations

import logging
import pathlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator, Optional

import requests

from .auth import KaggleAuth, load_credentials

log = logging.getLogger(__name__)

_API_BASE = "https://www.kaggle.com/api/v1"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_PACE = 0.5  # seconds between requests (~2 req/sec)
_RETRYABLE_STATUS = {500, 502, 503, 504}


class KaggleHttpError(RuntimeError):
    """A non-retryable HTTP error from the Kaggle API."""

    def __init__(self, status: int, message: str, body: str = ""):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.body = body


@dataclass
class _RatePacer:
    interval_sec: float
    _last_at: float = 0.0
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self):
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delta = now - self._last_at
            if delta < self.interval_sec:
                time.sleep(self.interval_sec - delta)
            self._last_at = time.monotonic()


class KaggleClient:
    """Minimal Kaggle API surface used by the harvester."""

    def __init__(
        self,
        auth: Optional[KaggleAuth] = None,
        *,
        pace_sec: float = _DEFAULT_PACE,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = 3,
    ):
        self._auth = auth or load_credentials()
        self._pacer = _RatePacer(interval_sec=pace_sec)
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update(self._auth.auth_header())

    # ------------------------------------------------------------------
    # Low-level request with retry + pacing
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        stream: bool = False,
        accept_404: bool = False,
    ) -> requests.Response:
        url = f"{_API_BASE}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries):
            self._pacer.wait()
            try:
                resp = self._session.request(
                    method, url,
                    params=params,
                    stream=stream,
                    timeout=self._timeout,
                )
            except requests.RequestException as e:
                last_exc = e
                log.warning("Kaggle %s %s failed (attempt %d/%d): %s",
                            method, path, attempt + 1, self._max_retries, e)
                if attempt < self._max_retries - 1:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                raise KaggleHttpError(0, str(e), body="")
            if resp.status_code == 200:
                return resp
            if resp.status_code == 404 and accept_404:
                return resp
            if resp.status_code in _RETRYABLE_STATUS:
                log.warning("Kaggle %s %s returned %d (attempt %d/%d), retrying",
                            method, path, resp.status_code, attempt + 1, self._max_retries)
                if attempt < self._max_retries - 1:
                    time.sleep(min(2 ** attempt, 30))
                    continue
            # Non-retryable
            body = resp.text[:1000]
            raise KaggleHttpError(resp.status_code, resp.reason or "", body=body)
        if last_exc is not None:
            raise KaggleHttpError(0, str(last_exc), body="")
        raise KaggleHttpError(0, "Exhausted retries", body="")

    # ------------------------------------------------------------------
    # Competitions
    # ------------------------------------------------------------------

    def list_competitions(
        self,
        *,
        search: str = "",
        category: str = "",
        sort_by: str = "latestDeadline",
        page: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """List competitions. ``category`` ∈ {'', 'featured', 'research',
        'recruitment', 'gettingStarted', 'masters', 'playground'}.

        The Kaggle public API does not expose a generic 'closed' filter; we
        filter post-hoc on the returned ``deadline`` field.
        """
        params = {"page": page, "pageSize": page_size, "sortBy": sort_by}
        if search:
            params["search"] = search
        if category:
            params["category"] = category
        resp = self._request("GET", "/competitions/list", params=params)
        data = resp.json()
        return data if isinstance(data, list) else []

    def competition_files(self, slug: str) -> tuple[bool, list[dict[str, Any]]]:
        """Return (accessible, files).

        ``accessible`` is False when the competition requires rules
        acceptance (HTTP 403 from this endpoint) — caller should skip.
        """
        try:
            resp = self._request("GET", f"/competitions/data/list/{slug}", accept_404=True)
        except KaggleHttpError as e:
            if e.status in (401, 403):
                return False, []
            if e.status == 404:
                return False, []
            raise
        if resp.status_code != 200:
            return False, []
        data = resp.json()
        files = data.get("datasetFiles") if isinstance(data, dict) else data
        return True, files or []

    def download_competition(
        self,
        slug: str,
        dest_dir: pathlib.Path,
        *,
        file_name: Optional[str] = None,
    ) -> pathlib.Path:
        """Download the competition's data archive (or a single file).

        Returns the path to the saved file. The Kaggle API returns either a
        ZIP (multi-file dataset) or the raw single file.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        if file_name:
            path = f"/competitions/data/download/{slug}/{file_name}"
            out = dest_dir / file_name
        else:
            path = f"/competitions/data/download-all/{slug}"
            out = dest_dir / f"{slug}.zip"
        resp = self._request("GET", path, stream=True, accept_404=True)
        if resp.status_code != 200:
            raise KaggleHttpError(resp.status_code, resp.reason or "",
                                  body=resp.text[:500])
        tmp = out.with_suffix(out.suffix + ".part")
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)
        tmp.replace(out)
        return out

    # ------------------------------------------------------------------
    # Kernels (notebooks/scripts)
    # ------------------------------------------------------------------

    def list_kernels(
        self,
        *,
        competition: str = "",
        search: str = "",
        language: str = "python",
        sort_by: str = "voteCount",
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """List kernels. ``competition`` filters by competition slug.

        ``language`` ∈ {'all', 'python', 'r', 'sqlite', 'julia'}.
        ``sort_by`` ∈ {'hotness', 'commentCount', 'dateCreated', 'dateRun',
                       'relevance', 'scoreAscending', 'scoreDescending',
                       'viewCount', 'voteCount'}.
        """
        params = {
            "page": page,
            "pageSize": page_size,
            "language": language,
            "sortBy": sort_by,
        }
        if competition:
            params["competition"] = competition
        if search:
            params["search"] = search
        resp = self._request("GET", "/kernels/list", params=params)
        data = resp.json()
        return data if isinstance(data, list) else []

    def pull_kernel(
        self,
        kernel_ref: str,
        dest_dir: pathlib.Path,
    ) -> tuple[Optional[pathlib.Path], dict[str, Any]]:
        """Download a kernel's source.

        ``kernel_ref`` is ``<user>/<kernel-slug>``. Returns the path to the
        saved source file (``.ipynb`` for notebook kernels, ``.py`` /
        ``.R`` / ``.jl`` for script kernels) and the metadata block returned
        alongside the source.

        The endpoint Kaggle exposes is ``/kernels/pull/<user>/<slug>`` (path
        positional) which returns ``{"metadata": {...}, "blob": {"source": ...}}``.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        if "/" not in kernel_ref:
            return None, {}
        user, slug = kernel_ref.split("/", 1)
        resp = self._request(
            "GET", f"/kernels/pull/{user}/{slug}",
            stream=True, accept_404=True,
        )
        if resp.status_code != 200:
            return None, {}
        try:
            payload = resp.json()
        except ValueError:
            return None, {}
        if not isinstance(payload, dict):
            return None, {}
        meta = payload.get("metadata") or {}
        blob = payload.get("blob") or {}
        source = blob.get("source", "")
        kernel_type = str(meta.get("kernelTypeNullable") or meta.get("kernelType") or "").lower()
        language = str(meta.get("languageNullable") or meta.get("language") or "").lower()
        if kernel_type == "notebook":
            suffix = ".ipynb"
        else:
            suffix = {
                "python": ".py",
                "r": ".R",
                "rmarkdown": ".Rmd",
                "julia": ".jl",
                "sqlite": ".sql",
            }.get(language, ".txt")
        out = dest_dir / f"{slug}{suffix}"
        out.write_text(source, encoding="utf-8")
        return out, meta

    # ------------------------------------------------------------------
    # Streaming iterator helpers
    # ------------------------------------------------------------------

    def paginate_competitions(
        self,
        *,
        category: str = "",
        sort_by: str = "latestDeadline",
        page_size: int = 50,
        max_pages: int = 10,
    ) -> Iterator[dict[str, Any]]:
        """Yield competitions from a category, paginated. Stops on empty page."""
        for page in range(1, max_pages + 1):
            batch = self.list_competitions(
                category=category, sort_by=sort_by,
                page=page, page_size=page_size,
            )
            if not batch:
                return
            for c in batch:
                yield c
            if len(batch) < page_size:
                return
