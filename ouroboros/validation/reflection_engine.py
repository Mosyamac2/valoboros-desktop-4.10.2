"""
Valoboros — cross-validation reflection engine.

Analyzes past validation reports to find patterns, detect dead/hot checks,
and write accumulated knowledge to the knowledge base.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from ouroboros.validation.types import ReflectionResult, ValidationConfig

log = logging.getLogger(__name__)

_MIN_VALIDATIONS_FOR_PATTERNS = 2


class ValidationReflectionEngine:
    """Analyzes past validations to find patterns and improve methodology."""

    def __init__(
        self,
        validations_dir: Path,
        knowledge_dir: Path,
        config: ValidationConfig,
    ) -> None:
        self._validations_dir = Path(validations_dir)
        self._knowledge_dir = Path(knowledge_dir)
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def reflect(
        self,
        registered_check_ids: Optional[list[str]] = None,
    ) -> ReflectionResult:
        """Analyze all past reports and produce reflection result."""
        return self._do_reflect(registered_check_ids)

    def reflect_sync(
        self,
        registered_check_ids: Optional[list[str]] = None,
    ) -> ReflectionResult:
        """Synchronous wrapper for testing."""
        return self._do_reflect(registered_check_ids)

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _do_reflect(
        self,
        registered_check_ids: Optional[list[str]] = None,
    ) -> ReflectionResult:
        reports = self._load_all_reports()

        result = ReflectionResult(total_validations_analyzed=len(reports))

        if len(reports) < _MIN_VALIDATIONS_FOR_PATTERNS:
            return result

        # Collect all failed check occurrences
        check_failures: dict[str, list[dict]] = defaultdict(list)
        # {"check_id": [{"bundle_id": ..., "model_type": ..., "details": ...}]}

        all_triggered_checks: set[str] = set()

        for report in reports:
            model_type = report.get("model_profile", {}).get("model_type", "unknown")
            bundle_id = report.get("bundle_id", "?")

            for finding in report.get("critical_findings", []):
                check_id = finding.get("check_id", "")
                if check_id:
                    check_failures[check_id].append({
                        "bundle_id": bundle_id,
                        "model_type": model_type,
                        "details": finding.get("details", ""),
                    })
                    all_triggered_checks.add(check_id)

            # Also scan stage checks for triggered (passed=False) checks
            for stage in report.get("stages", []):
                for check in stage.get("checks", []):
                    if not check.get("passed", True):
                        cid = check.get("check_id", "")
                        if cid:
                            all_triggered_checks.add(cid)
                            if cid not in check_failures:
                                check_failures[cid].append({
                                    "bundle_id": bundle_id,
                                    "model_type": model_type,
                                    "details": check.get("details", ""),
                                })

        # Build patterns: checks that fail in >= 2 reports
        for check_id, occurrences in check_failures.items():
            if len(occurrences) >= 2:
                model_types = sorted(set(o["model_type"] for o in occurrences))
                result.patterns_found.append({
                    "check_id": check_id,
                    "frequency": len(occurrences),
                    "model_types": model_types,
                    "description": f"{check_id} triggered in {len(occurrences)}/{len(reports)} validations across {model_types}",
                })

        # Sort patterns by frequency (most common first)
        result.patterns_found.sort(key=lambda p: p["frequency"], reverse=True)

        # Dead checks: registered but never triggered in any report
        if registered_check_ids:
            result.dead_checks = sorted(
                set(registered_check_ids) - all_triggered_checks
            )

        # Hot checks: triggered in every single report
        for check_id, occurrences in check_failures.items():
            if len(occurrences) >= len(reports):
                result.hot_checks.append(check_id)
        result.hot_checks.sort()

        # Write to knowledge base
        written = self._write_knowledge(reports, result)
        result.knowledge_entries_written = written

        return result

    # ------------------------------------------------------------------
    # Report loading
    # ------------------------------------------------------------------

    def _load_all_reports(self) -> list[dict[str, Any]]:
        """Scan validations_dir for all completed report.json files."""
        if not self._validations_dir.exists():
            return []

        reports: list[dict] = []
        for bundle_dir in sorted(self._validations_dir.iterdir()):
            if not bundle_dir.is_dir():
                continue
            report_path = bundle_dir / "results" / "report.json"
            if report_path.exists():
                try:
                    data = json.loads(report_path.read_text(encoding="utf-8"))
                    reports.append(data)
                except (json.JSONDecodeError, OSError) as exc:
                    log.debug("Failed to load report %s: %s", report_path, exc)
        return reports

    # ------------------------------------------------------------------
    # Knowledge writing
    # ------------------------------------------------------------------

    def _write_knowledge(
        self,
        reports: list[dict],
        result: ReflectionResult,
    ) -> list[str]:
        """Write patterns to knowledge base as .md files."""
        if not result.patterns_found:
            return []

        self._knowledge_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []

        # 1. Cross-cutting validation_patterns.md
        patterns_md = self._render_patterns_md(result)
        patterns_file = self._knowledge_dir / "validation_patterns.md"
        patterns_file.write_text(patterns_md, encoding="utf-8")
        written.append("validation_patterns.md")

        # 2. Per-model-type files
        model_types_seen: set[str] = set()
        for report in reports:
            mt = report.get("model_profile", {}).get("model_type", "")
            if mt:
                model_types_seen.add(mt)

        for mt in sorted(model_types_seen):
            mt_patterns = [p for p in result.patterns_found if mt in p["model_types"]]
            if mt_patterns:
                filename = f"model_type_{mt}.md"
                content = self._render_model_type_md(mt, mt_patterns, reports)
                (self._knowledge_dir / filename).write_text(content, encoding="utf-8")
                written.append(filename)

        return written

    @staticmethod
    def _render_patterns_md(result: ReflectionResult) -> str:
        lines = [
            "# Validation Patterns",
            "",
            f"Based on {result.total_validations_analyzed} validations.",
            "",
            "## Common Failure Patterns",
            "",
        ]
        for p in result.patterns_found:
            lines.append(
                f"- **{p['check_id']}**: triggered {p['frequency']} times "
                f"across {', '.join(p['model_types'])}. {p.get('description', '')}"
            )
        if result.dead_checks:
            lines += ["", "## Dead Checks (never triggered)", ""]
            for cid in result.dead_checks:
                lines.append(f"- {cid}")
        if result.hot_checks:
            lines += ["", "## Hot Checks (always trigger)", ""]
            for cid in result.hot_checks:
                lines.append(f"- {cid}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_model_type_md(
        model_type: str,
        patterns: list[dict],
        reports: list[dict],
    ) -> str:
        type_reports = [r for r in reports if r.get("model_profile", {}).get("model_type") == model_type]
        lines = [
            f"# {model_type.title()} Model Validation Patterns",
            "",
            f"Based on {len(type_reports)} {model_type} model validations.",
            "",
            "## Common Issues",
            "",
        ]
        for p in patterns:
            lines.append(f"- **{p['check_id']}**: triggered {p['frequency']} times")
        lines.append("")
        return "\n".join(lines)
