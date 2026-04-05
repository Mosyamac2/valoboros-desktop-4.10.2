"""
Ouroboros validation platform — effectiveness tracker.

Tracks finding quality (is the risk real?) and recommendation quality
(does the fix work?) as independent dimensions.  Storage: JSONL file.
Supports graduated maturity phases (early → mature).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_LIFT_THRESHOLD = 0.01  # min lift to count as "improved"


# ---------------------------------------------------------------------------
# Stats dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FindingStats:
    check_id: str
    tp: float = 0.0
    fp: float = 0.0
    fn: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    self_assessed_tp: int = 0
    self_assessed_fp: int = 0
    human_tp: int = 0
    human_fp: int = 0
    human_fn: int = 0
    last_triggered: Optional[str] = None
    times_triggered: int = 0
    last_updated: str = ""


@dataclass
class RecommendationStats:
    check_id: str
    recommendations_produced: int = 0
    recommendations_implemented: int = 0
    recommendations_improved: int = 0
    recommendations_degraded: int = 0
    recommendations_unchanged: int = 0
    mean_improvement_lift: float = 0.0
    implementation_rate: float = 0.0
    usefulness_rate: float = 0.0


@dataclass
class PlatformMetrics:
    maturity_phase: str = "early"
    total_validations: int = 0
    total_bundles_with_feedback: int = 0
    mean_finding_precision: float = 0.0
    mean_finding_recall: float = 0.0
    mean_improvement_lift: float = 0.0
    useless_hard_rec_rate: float = 0.0
    recommendation_implementation_rate: float = 0.0
    methodology_evolution_frequency: float = 0.0
    check_churn: float = 0.0
    total_checks: int = 0
    total_improvement_cycles: int = 0


@dataclass
class EvolutionTarget:
    target_type: str
    priority: float
    description: str
    evidence: str
    suggested_action: str
    maturity_gate: str = "any"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal record types (stored in JSONL)
# ---------------------------------------------------------------------------

@dataclass
class _FindingRecord:
    check_id: str
    bundle_id: str
    verdict: str         # "true_positive" | "false_positive" | "false_negative"
    source: str          # "self_assessed" | "human" | "improvement_inferred" | "cross_check"
    weight: float
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> _FindingRecord:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class _RecommendationRecord:
    check_id: str
    bundle_id: str
    metric_before: dict[str, float]
    metric_after: dict[str, float]
    lift: float
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> _RecommendationRecord:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# EffectivenessTracker
# ---------------------------------------------------------------------------

class EffectivenessTracker:
    """Tracks finding quality and recommendation quality independently."""

    def __init__(self, data_root: Path, maturity_threshold: int = 20) -> None:
        self._data_root = Path(data_root)
        self._findings_path = self._data_root / "validation_findings.jsonl"
        self._recs_path = self._data_root / "validation_recommendations.jsonl"
        self._maturity_threshold = maturity_threshold

    # ------------------------------------------------------------------
    # Maturity
    # ------------------------------------------------------------------

    @property
    def maturity_phase(self) -> str:
        bundles = self._unique_bundles_with_feedback()
        return "mature" if len(bundles) >= self._maturity_threshold else "early"

    def _unique_bundles_with_feedback(self) -> set[str]:
        records = self._load_findings()
        return {r.bundle_id for r in records}

    # ------------------------------------------------------------------
    # Finding quality
    # ------------------------------------------------------------------

    def record_finding_feedback(
        self,
        check_id: str,
        bundle_id: str,
        verdict: str,
        source: str,
        weight: float,
    ) -> None:
        record = _FindingRecord(
            check_id=check_id, bundle_id=bundle_id, verdict=verdict,
            source=source, weight=weight, timestamp=_utc_now(),
        )
        self._append_finding(record)

    def record_self_assessment(
        self,
        check_id: str,
        bundle_id: str,
        likely_verdict: str,
        reasoning: str,
    ) -> None:
        verdict = "true_positive" if likely_verdict.startswith("likely_tp") or likely_verdict == "true_positive" else "false_positive"
        self.record_finding_feedback(check_id, bundle_id, verdict, "self_assessed", 0.3)

    def get_finding_stats(self, check_id: str, min_weight: float = 0.0) -> FindingStats:
        records = [r for r in self._load_findings() if r.check_id == check_id and r.weight >= min_weight]
        stats = FindingStats(check_id=check_id)

        for r in records:
            if r.source == "self_assessed":
                if r.verdict == "true_positive":
                    stats.self_assessed_tp += 1
                elif r.verdict == "false_positive":
                    stats.self_assessed_fp += 1
            elif r.source == "human":
                if r.verdict == "true_positive":
                    stats.human_tp += 1
                elif r.verdict == "false_positive":
                    stats.human_fp += 1
                elif r.verdict == "false_negative":
                    stats.human_fn += 1

            # Weighted aggregates
            if r.verdict == "true_positive":
                stats.tp += r.weight
            elif r.verdict == "false_positive":
                stats.fp += r.weight
            elif r.verdict == "false_negative":
                stats.fn += r.weight

        stats.times_triggered = len(records)
        if records:
            stats.last_triggered = records[-1].timestamp
            stats.last_updated = records[-1].timestamp

        total = stats.tp + stats.fp
        stats.precision = stats.tp / total if total > 0 else 0.0
        tp_fn = stats.tp + stats.fn
        stats.recall = stats.tp / tp_fn if tp_fn > 0 else 0.0
        pr_sum = stats.precision + stats.recall
        stats.f1 = 2 * stats.precision * stats.recall / pr_sum if pr_sum > 0 else 0.0

        return stats

    def get_finding_rankings(self, sort_by: str = "f1") -> list[tuple[str, FindingStats]]:
        all_ids = {r.check_id for r in self._load_findings()}
        rankings = [(cid, self.get_finding_stats(cid)) for cid in all_ids]
        rankings.sort(key=lambda x: getattr(x[1], sort_by, 0), reverse=True)
        return rankings

    def get_underperformers(self, min_samples: int = 1, max_precision: float = 0.5) -> list[str]:
        result = []
        all_ids = {r.check_id for r in self._load_findings()}
        for cid in all_ids:
            stats = self.get_finding_stats(cid)
            if stats.times_triggered >= min_samples and stats.precision < max_precision:
                result.append(cid)
        return result

    def get_never_triggered(self, min_validations: int = 10) -> list[str]:
        triggered = {r.check_id for r in self._load_findings()}
        # We'd need the check registry to know all checks — return empty for now
        return []

    # ------------------------------------------------------------------
    # Recommendation quality
    # ------------------------------------------------------------------

    def record_recommendation_result(
        self,
        check_id: str,
        bundle_id: str,
        metric_before: dict[str, float],
        metric_after: dict[str, float],
    ) -> None:
        deltas = {k: metric_after.get(k, 0) - metric_before.get(k, 0) for k in metric_before}
        avg_lift = sum(deltas.values()) / len(deltas) if deltas else 0.0
        record = _RecommendationRecord(
            check_id=check_id, bundle_id=bundle_id,
            metric_before=metric_before, metric_after=metric_after,
            lift=avg_lift, timestamp=_utc_now(),
        )
        self._append_recommendation(record)

    def get_recommendation_stats(self, check_id: str) -> RecommendationStats:
        records = [r for r in self._load_recommendations() if r.check_id == check_id]
        stats = RecommendationStats(check_id=check_id)
        stats.recommendations_produced = len(records)
        stats.recommendations_implemented = len(records)  # recorded means implemented

        lifts: list[float] = []
        for r in records:
            lifts.append(r.lift)
            if r.lift > _LIFT_THRESHOLD:
                stats.recommendations_improved += 1
            elif r.lift < -_LIFT_THRESHOLD:
                stats.recommendations_degraded += 1
            else:
                stats.recommendations_unchanged += 1

        stats.mean_improvement_lift = sum(lifts) / len(lifts) if lifts else 0.0
        stats.implementation_rate = 1.0 if records else 0.0
        stats.usefulness_rate = (
            stats.recommendations_improved / len(records) if records else 0.0
        )
        return stats

    # ------------------------------------------------------------------
    # Platform-level
    # ------------------------------------------------------------------

    def get_platform_metrics(self) -> PlatformMetrics:
        findings = self._load_findings()
        recs = self._load_recommendations()
        bundles = {r.bundle_id for r in findings}

        # Mean finding precision across checks
        check_ids = {r.check_id for r in findings}
        precisions = []
        for cid in check_ids:
            s = self.get_finding_stats(cid)
            if s.times_triggered >= 1:
                precisions.append(s.precision)

        # Mean improvement lift
        lifts = [r.lift for r in recs]

        return PlatformMetrics(
            maturity_phase=self.maturity_phase,
            total_validations=len(bundles),
            total_bundles_with_feedback=len(bundles),
            mean_finding_precision=sum(precisions) / len(precisions) if precisions else 0.0,
            mean_finding_recall=0.0,  # needs FN data
            mean_improvement_lift=sum(lifts) / len(lifts) if lifts else 0.0,
            useless_hard_rec_rate=0.0,
            recommendation_implementation_rate=0.0,
            methodology_evolution_frequency=0.0,
            check_churn=0.0,
            total_checks=0,
            total_improvement_cycles=len(recs),
        )

    def get_evolution_targets(self) -> list[EvolutionTarget]:
        targets: list[EvolutionTarget] = []
        phase = self.maturity_phase

        underperformers = self.get_underperformers(
            min_samples=1 if phase == "early" else 5,
            max_precision=0.3 if phase == "early" else 0.5,
        )
        for cid in underperformers:
            stats = self.get_finding_stats(cid)
            targets.append(EvolutionTarget(
                target_type="fix_check",
                priority=1.0 - stats.precision,
                description=f"Check {cid} has low precision ({stats.precision:.2f})",
                evidence=f"TP={stats.tp:.1f}, FP={stats.fp:.1f}, precision={stats.precision:.2f}",
                suggested_action=f"Edit or delete check {cid}",
                maturity_gate=phase,
            ))

        targets.sort(key=lambda t: t.priority, reverse=True)
        return targets

    # ------------------------------------------------------------------
    # JSONL persistence
    # ------------------------------------------------------------------

    def _load_findings(self) -> list[_FindingRecord]:
        return self._load_jsonl(self._findings_path, _FindingRecord.from_dict)

    def _load_recommendations(self) -> list[_RecommendationRecord]:
        return self._load_jsonl(self._recs_path, _RecommendationRecord.from_dict)

    def _append_finding(self, record: _FindingRecord) -> None:
        self._append_jsonl(self._findings_path, record.to_dict())

    def _append_recommendation(self, record: _RecommendationRecord) -> None:
        self._append_jsonl(self._recs_path, record.to_dict())

    @staticmethod
    def _load_jsonl(path: Path, parser) -> list:
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                try:
                    records.append(parser(json.loads(line)))
                except Exception:
                    pass
        return records

    @staticmethod
    def _append_jsonl(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
