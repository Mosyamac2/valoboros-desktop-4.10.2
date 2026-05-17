"""Agentic reflection — Plan v2 Phase 8.

Cross-bundle reflection over the agentic artifacts (``methodology.md``,
``interpretation.md``, ``results.json``) for two kinds of signal that
the legacy reflection engine cannot see:

1. **Methodological motifs**: which qualitative / quantitative tests keep
   showing up across many bundles? Their names + metric types are signal
   for "make this a reusable helper in :mod:`agentic_helpers`" or "append
   to :mod:`agentic_prompts.phase_a_methodology` so future methodologies
   always include it".

2. **Recurring false positives**: tests that fail in many bundles AND have
   no corresponding ``true_positive`` records in the EffectivenessTracker.
   Likely structural artifacts (e.g. ``S8.CODE_SMELLS`` triggering on
   every Kaggle kernel because of hardcoded ``/kaggle/input/`` paths).

The output is a :class:`ReflectionResult` (same type as the legacy
engine produces) plus a markdown digest written to
``knowledge/agentic_reflection.md`` for the next session's system prompt
to read. We deliberately KEEP the legacy engine running in parallel —
the legacy patterns from re-running pre-checks still hold useful signal
for v1 callers.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from ouroboros.validation.types import ReflectionResult, ValidationConfig

log = logging.getLogger(__name__)

_MIN_BUNDLES_FOR_MOTIF = 3       # a "motif" needs to show in ≥ 3 bundles
_MIN_BUNDLES_FOR_FALSE_POSITIVE = 3
_MIN_BUNDLES_FOR_PATTERN = 2


def _normalise_test_name(name: str) -> str:
    """Project a free-form test name (e.g. "OOS AUC on holdout split") down
    to a stable key for cross-bundle counting. We keep alphanumerics and
    drop everything else, lower-cased.
    """
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _extract_test_keys(test: dict[str, Any]) -> tuple[str, str]:
    """Return ``(name_key, metric_key)`` for cross-bundle counting.

    ``metric_key`` is the FIRST key in the test's ``metric`` dict; if no
    metric is present (qualitative test), returns ``""``.
    """
    name_key = _normalise_test_name(test.get("name") or "")
    metric = test.get("metric") or {}
    metric_key = ""
    if isinstance(metric, dict) and metric:
        metric_key = next(iter(metric.keys()))
    return name_key, metric_key


def _load_agentic_results(bundle_dir: Path) -> Optional[dict[str, Any]]:
    """Read ``results/results.json`` for a single bundle. Returns ``None``
    if absent or unparseable (legacy bundles, mid-run crashes)."""
    path = bundle_dir / "results" / "results.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("Could not load agentic results from %s: %s", path, exc)
        return None


def _load_methodology_md(bundle_dir: Path) -> str:
    path = bundle_dir / "methodology" / "methodology.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _load_finding_feedback_index(
    validations_root: Path,
) -> dict[tuple[str, str], Counter[str]]:
    """Read ``validation_findings.jsonl`` (written by EffectivenessTracker)
    and produce ``{(check_id, bundle_id): Counter(verdict)}``.

    Used by the false-positive scan: a check_id is a candidate FP when it
    appears as ``fail`` in N bundles but has no inferred
    ``true_positive`` records.
    """
    findings_file = validations_root / "validation_findings.jsonl"
    out: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    if not findings_file.exists():
        return out
    try:
        for line in findings_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = row.get("check_id") or ""
            bid = row.get("bundle_id") or ""
            verdict = row.get("verdict") or ""
            if cid and bid and verdict:
                out[(cid, bid)][verdict] += 1
    except OSError as exc:
        log.debug("Could not read findings jsonl: %s", exc)
    return out


class AgenticReflectionEngine:
    """Cross-bundle reflection over agentic-flow artifacts."""

    def __init__(
        self,
        validations_dir: Path | str,
        knowledge_dir: Path | str,
        config: Optional[ValidationConfig] = None,
    ) -> None:
        self.validations_dir = Path(validations_dir).resolve()
        self.knowledge_dir = Path(knowledge_dir).resolve()
        self.config = config or ValidationConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reflect(self) -> ReflectionResult:
        """Scan every bundle's agentic ``results.json``. Returns a
        :class:`ReflectionResult` with ``patterns_found`` carrying the
        motifs / false-positive candidates, and writes
        ``knowledge/agentic_reflection.md`` for the next session."""
        results = self._load_all_results()

        out = ReflectionResult(total_validations_analyzed=len(results))
        if len(results) < _MIN_BUNDLES_FOR_PATTERN:
            return out

        # 1. Cross-bundle check_id failures (similar to legacy engine but
        #    over the agentic schema — fail / warn count separately).
        fail_counts: dict[str, set[str]] = defaultdict(set)
        warn_counts: dict[str, set[str]] = defaultdict(set)
        all_check_ids: set[str] = set()
        for r in results:
            bundle_id = r["bundle_id"]
            for test in r["results"].get("tests", []) or []:
                check_id = self._test_check_id(test)
                if not check_id:
                    continue
                all_check_ids.add(check_id)
                verdict = (test.get("verdict") or "").lower()
                if verdict == "fail":
                    fail_counts[check_id].add(bundle_id)
                elif verdict == "warn":
                    warn_counts[check_id].add(bundle_id)

        for check_id, bundles in fail_counts.items():
            if len(bundles) >= _MIN_BUNDLES_FOR_PATTERN:
                out.patterns_found.append({
                    "kind": "recurring_failure",
                    "check_id": check_id,
                    "frequency": len(bundles),
                    "model_types": [],
                    "description": (
                        f"{check_id} failed in {len(bundles)}/{len(results)} "
                        "agentic validations"
                    ),
                })

        # 2. Methodological motifs: test names that show up in ≥ N bundles
        #    independent of pass/fail. Signal for "everyone's methodology
        #    converges on this test — promote to default scaffolding".
        name_to_bundles: dict[str, set[str]] = defaultdict(set)
        name_metric_pairs: dict[str, set[str]] = defaultdict(set)
        for r in results:
            bundle_id = r["bundle_id"]
            for test in r["results"].get("tests", []) or []:
                name_key, metric_key = _extract_test_keys(test)
                if not name_key:
                    continue
                name_to_bundles[name_key].add(bundle_id)
                if metric_key:
                    name_metric_pairs[name_key].add(metric_key)

        for name_key, bundles in name_to_bundles.items():
            if len(bundles) >= _MIN_BUNDLES_FOR_MOTIF:
                metrics = sorted(name_metric_pairs.get(name_key, set()))
                out.patterns_found.append({
                    "kind": "methodological_motif",
                    "name_key": name_key,
                    "frequency": len(bundles),
                    "model_types": [],
                    "metrics": metrics,
                    "description": (
                        f"Methodologies asked for {name_key!r} in "
                        f"{len(bundles)}/{len(results)} bundles; metrics: "
                        f"{', '.join(metrics) if metrics else 'qualitative'}"
                    ),
                })

        # 3. Recurring false positives: a check_id that failed in many
        #    bundles AND has no inferred TP from the tracker. We treat the
        #    absence of TP as suggestive — Phase 9 may use this to propose
        #    retiring or rephrasing the corresponding methodology directive.
        findings_index = _load_finding_feedback_index(self.validations_dir)
        for check_id, bundles in fail_counts.items():
            if len(bundles) < _MIN_BUNDLES_FOR_FALSE_POSITIVE:
                continue
            tp_count = sum(
                ctr.get("true_positive", 0)
                for (cid, _bid), ctr in findings_index.items() if cid == check_id
            )
            if tp_count == 0:
                out.patterns_found.append({
                    "kind": "candidate_false_positive",
                    "check_id": check_id,
                    "frequency": len(bundles),
                    "model_types": [],
                    "description": (
                        f"{check_id} failed in {len(bundles)} bundles but "
                        "produced zero inferred true-positive records from "
                        "improvement cycles. Candidate structural artifact."
                    ),
                })

        # Sort: structural artifacts first (most urgent), then motifs by
        # frequency, then recurring failures.
        kind_order = {
            "candidate_false_positive": 0,
            "methodological_motif": 1,
            "recurring_failure": 2,
        }
        out.patterns_found.sort(key=lambda p: (kind_order.get(p["kind"], 9), -p["frequency"]))

        # Dead checks: known check_ids that show up in NO failure of any kind
        # — they're either always-pass (good!) or never used. Distinguishing
        # the two requires the registered_check_ids list which we don't
        # have here; leave the list empty and let callers populate.
        out.dead_checks = []
        out.hot_checks = sorted(
            cid for cid, bundles in fail_counts.items() if len(bundles) >= len(results)
        )

        # 4. Persist a markdown digest for the next session's system prompt
        written = self._write_knowledge(out)
        out.knowledge_entries_written = written

        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_all_results(self) -> list[dict[str, Any]]:
        """Iterate over every bundle dir under ``validations_dir`` and
        return ``[{bundle_id, results}, ...]`` for those that have an
        agentic ``results.json``."""
        out: list[dict[str, Any]] = []
        if not self.validations_dir.exists():
            return out
        for bundle_dir in sorted(self.validations_dir.iterdir()):
            if not bundle_dir.is_dir():
                continue
            results = _load_agentic_results(bundle_dir)
            if results is None:
                continue
            out.append({
                "bundle_id": results.get("bundle_id") or bundle_dir.name,
                "results": results,
                "methodology_md": _load_methodology_md(bundle_dir),
            })
        return out

    @staticmethod
    def _test_check_id(test: dict[str, Any]) -> str:
        block = (test.get("block") or "").upper() or "UNK"
        tid = test.get("id") or ""
        if not tid:
            return ""
        return f"{block}.{tid}"

    def _write_knowledge(self, result: ReflectionResult) -> list[str]:
        if not result.patterns_found:
            return []
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        lines: list[str] = [
            "# Agentic validation reflection",
            "",
            f"Analyzed {result.total_validations_analyzed} agentic bundles.",
            "",
        ]

        def _emit_section(title: str, kind: str) -> None:
            items = [p for p in result.patterns_found if p["kind"] == kind]
            if not items:
                return
            lines.append(f"## {title}")
            lines.append("")
            for p in items:
                lines.append(f"- {p.get('description', '')}")
            lines.append("")

        _emit_section("Candidate structural artifacts (suspected false positives)",
                      "candidate_false_positive")
        _emit_section("Methodological motifs (promote to helpers / prompt defaults)",
                      "methodological_motif")
        _emit_section("Recurring failures (signal to address in source code)",
                      "recurring_failure")
        if result.hot_checks:
            lines.append("## Hot checks (failed in every bundle)")
            lines.append("")
            for cid in result.hot_checks:
                lines.append(f"- {cid}")
            lines.append("")

        (self.knowledge_dir / "agentic_reflection.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        return ["agentic_reflection.md"]
