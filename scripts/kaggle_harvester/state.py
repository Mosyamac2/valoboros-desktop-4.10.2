"""Resumable manifest for the harvester.

State lives at ``~/.kaggle_harvester/state.json``. A run can be interrupted
and resumed with ``--resume``; the manifest tells the harvester which
competitions are already harvested or permanently blocked.
"""

from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_STATE_DIR = pathlib.Path.home() / ".kaggle_harvester"
_STATE_FILE = _STATE_DIR / "state.json"
_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class HarvestRecord:
    slug: str
    bundle_path: str
    kernel: str
    ts: str = field(default_factory=_utc_now_iso)


@dataclass
class SkipRecord:
    slug: str
    reason: str
    ts: str = field(default_factory=_utc_now_iso)


@dataclass
class HarvesterState:
    version: int = _VERSION
    harvested: list[HarvestRecord] = field(default_factory=list)
    skipped: list[SkipRecord] = field(default_factory=list)
    blocked_competitions: list[str] = field(default_factory=list)
    tier: int = 1

    def harvested_slugs(self) -> set[str]:
        return {r.slug for r in self.harvested}

    def skipped_slugs(self) -> set[str]:
        return {r.slug for r in self.skipped}

    def blocked_set(self) -> set[str]:
        return set(self.blocked_competitions)

    def seen(self) -> set[str]:
        return self.harvested_slugs() | self.skipped_slugs() | self.blocked_set()

    def record_harvest(self, slug: str, bundle_path: pathlib.Path, kernel: str) -> None:
        self.harvested.append(HarvestRecord(
            slug=slug, bundle_path=str(bundle_path), kernel=kernel,
        ))

    def record_skip(self, slug: str, reason: str) -> None:
        self.skipped.append(SkipRecord(slug=slug, reason=reason))

    def block(self, slug: str) -> None:
        if slug not in self.blocked_competitions:
            self.blocked_competitions.append(slug)


def _dict_to_state(data: dict[str, Any]) -> HarvesterState:
    state = HarvesterState(
        version=int(data.get("version", _VERSION)),
        tier=int(data.get("tier", 1)),
        blocked_competitions=list(data.get("blocked_competitions", [])),
    )
    for raw in data.get("harvested", []) or []:
        state.harvested.append(HarvestRecord(
            slug=str(raw.get("slug", "")),
            bundle_path=str(raw.get("bundle_path", "")),
            kernel=str(raw.get("kernel", "")),
            ts=str(raw.get("ts", _utc_now_iso())),
        ))
    for raw in data.get("skipped", []) or []:
        state.skipped.append(SkipRecord(
            slug=str(raw.get("slug", "")),
            reason=str(raw.get("reason", "")),
            ts=str(raw.get("ts", _utc_now_iso())),
        ))
    return state


def load(path: pathlib.Path = _STATE_FILE) -> HarvesterState:
    if not path.exists():
        return HarvesterState()
    try:
        return _dict_to_state(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not load state from %s: %s. Starting fresh.", path, e)
        return HarvesterState()


def save(state: HarvesterState, path: pathlib.Path = _STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": state.version,
        "tier": state.tier,
        "harvested": [asdict(r) for r in state.harvested],
        "skipped": [asdict(r) for r in state.skipped],
        "blocked_competitions": list(state.blocked_competitions),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
