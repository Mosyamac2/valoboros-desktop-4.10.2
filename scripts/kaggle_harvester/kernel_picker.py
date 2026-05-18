"""Moderate-tier kernel selection.

Per plan §0 / locked-decision #2: pick a kernel that is solid but not
state-of-the-art so Valoboros has real room to find improvements.

Algorithm: take Python notebooks for the competition sorted by votes,
drop the top 20% by vote count, then uniform-random pick one from the
next 30% band. If the resulting band is empty (rare on small competitions),
fall back to a uniform pick across all moderate-or-below kernels.
"""

from __future__ import annotations

import logging
import math
import pathlib
import random
from dataclasses import dataclass
from typing import Any, Optional

from .kaggle_http import KaggleClient, KaggleHttpError

log = logging.getLogger(__name__)


@dataclass
class PickedKernel:
    ref: str             # "<user>/<kernel-slug>"
    title: str
    author: str
    votes: int
    enable_gpu: bool
    enable_internet: bool
    language: str
    url: str
    source_path: pathlib.Path
    metadata: dict[str, Any]


def _is_python_notebook(entry: dict[str, Any]) -> bool:
    language = (entry.get("language") or "").lower()
    kernel_type = (entry.get("kernelType") or "").lower()
    if language and language != "python":
        return False
    if kernel_type and kernel_type not in {"notebook", ""}:
        return False
    return True


def _list_python_kernels(client: KaggleClient, competition: str, *, max_pages: int = 5) -> list[dict[str, Any]]:
    """All Python notebooks for the competition, sorted by votes (desc)."""
    all_kernels: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        batch = client.list_kernels(
            competition=competition,
            language="python",
            sort_by="voteCount",
            page=page,
            page_size=100,
        )
        if not batch:
            break
        # Defensive client-side filter — Kaggle's competition= filter is
        # imperfect and sometimes leaks unrelated kernels.
        same_comp = [
            k for k in batch
            if competition in {
                ds.get("sourceSlug", "") if isinstance(ds, dict) else ""
                for ds in (k.get("competitionDataSources") or [])
            }
        ]
        if same_comp:
            all_kernels.extend(same_comp)
        else:
            # Fall back to relying on Kaggle's filter if our defensive
            # version yielded nothing (some endpoints don't populate the
            # competitionDataSources field).
            all_kernels.extend(batch)
        if len(batch) < 100:
            break
    # Deduplicate by ref
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for k in all_kernels:
        ref = str(k.get("ref", ""))
        if ref and ref not in seen and _is_python_notebook(k):
            seen.add(ref)
            unique.append(k)
    unique.sort(key=lambda k: int(k.get("totalVotes", 0) or 0), reverse=True)
    return unique


def _moderate_band(kernels: list[dict[str, Any]], *, drop_top_frac: float = 0.20,
                   band_frac: float = 0.30) -> list[dict[str, Any]]:
    """Return the moderate-quality band: skip top ``drop_top_frac`` by votes,
    take the next ``band_frac`` slice. Falls back to "anything below the top"
    when the candidate pool is small.
    """
    n = len(kernels)
    if n == 0:
        return []
    if n < 5:
        # Tiny pool — just exclude the absolute top and return the rest.
        return kernels[1:] or kernels
    skip = int(math.ceil(n * drop_top_frac))
    take = max(1, int(math.ceil(n * band_frac)))
    band = kernels[skip : skip + take]
    return band or kernels[skip:]


def pick_moderate_kernel(
    client: KaggleClient,
    competition: str,
    dest_dir: pathlib.Path,
    *,
    rng: Optional[random.Random] = None,
    max_attempts: int = 3,
) -> tuple[Optional[PickedKernel], str]:
    """Try up to ``max_attempts`` moderate-tier kernels until one downloads.

    Returns ``(picked, reason)``. ``picked`` is None on failure and ``reason``
    explains why (recorded in state.json by the caller).
    """
    rng = rng or random.Random()
    kernels = _list_python_kernels(client, competition)
    if not kernels:
        return None, "no_python_kernels_for_competition"

    band = _moderate_band(kernels)
    if not band:
        return None, "moderate_band_empty"

    candidates = list(band)
    rng.shuffle(candidates)
    last_reason = "unknown"

    for entry in candidates[:max_attempts]:
        ref = str(entry.get("ref", ""))
        if not ref or "/" not in ref:
            last_reason = "malformed_kernel_ref"
            continue
        try:
            source_path, meta = client.pull_kernel(ref, dest_dir)
        except KaggleHttpError as e:
            log.warning("kernel pull failed for %s: %s", ref, e)
            last_reason = f"pull_http_{e.status}"
            continue
        if source_path is None:
            last_reason = "pull_returned_no_source"
            continue
        if not source_path.exists() or source_path.stat().st_size < 100:
            last_reason = "pulled_source_too_small"
            continue
        return PickedKernel(
            ref=ref,
            title=str(entry.get("title", "")),
            author=str(entry.get("author", "")),
            votes=int(entry.get("totalVotes", 0) or 0),
            enable_gpu=bool(entry.get("enableGpu", False)),
            enable_internet=bool(entry.get("enableInternet", False)),
            language=str(entry.get("language", "python")),
            url=f"https://www.kaggle.com/code/{ref}",
            source_path=source_path,
            metadata=meta or entry,
        ), ""

    return None, last_reason
