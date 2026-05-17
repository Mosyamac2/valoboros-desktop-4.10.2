"""Tier-2 + tier-3 graceful degradation.

Tier-1: walk Kaggle's competition listing and try each. Skip competitions
whose data download returns 401/403 (rules acceptance required).

Tier-2: user supplies a text file of pre-accepted competition slugs (one
per line, comments with ``#``). Harvester only pulls from that list.

Tier-3: switch source from Competitions to Datasets. Datasets are
open-access and don't require rules acceptance, at the cost of weaker
leaderboard semantics. Bundle's description appendix records the downgrade.
"""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass
from typing import Iterator

log = logging.getLogger(__name__)


@dataclass
class AllowList:
    slugs: list[str]
    source_path: pathlib.Path

    @classmethod
    def load(cls, path: pathlib.Path) -> "AllowList":
        slugs: list[str] = []
        if not path.exists():
            return cls(slugs=[], source_path=path)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            slugs.append(line)
        return cls(slugs=slugs, source_path=path)

    def __bool__(self) -> bool:
        return bool(self.slugs)


def iter_dataset_candidates(client, *, page_size: int = 50, max_pages: int = 10) -> Iterator[dict]:
    """Tier-3: yield public dataset entries via Kaggle's datasets list.

    The shape differs from competitions — there's no evaluation metric and
    no leaderboard. The validator's S0 comprehension has to infer the
    target/task purely from the dataset description text.
    """
    for page in range(1, max_pages + 1):
        resp = client._request("GET", "/datasets/list",
                               params={"page": page, "pageSize": page_size,
                                       "sortBy": "votes"})
        data = resp.json()
        if not isinstance(data, list) or not data:
            return
        for entry in data:
            yield entry
        if len(data) < page_size:
            return
