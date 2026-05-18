"""
Ouroboros validation platform — report generation.

Produces JSON and Markdown reports from ValidationReport objects.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ouroboros.validation.types import ValidationConfig, ValidationReport

log = logging.getLogger(__name__)


class ReportGenerator:
    """Generate and save validation reports in JSON and Markdown formats."""

    def generate_json(self, report: ValidationReport) -> str:
        """Serialize ValidationReport to a JSON string."""
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)

    def generate_markdown(self, report: ValidationReport, config: ValidationConfig) -> str:
        """Generate a human-readable Markdown report.

        Attempts LLM-generated narrative; falls back to structured template.
        """
        try:
            return self._llm_narrative(report, config)
        except Exception as exc:
            log.info("LLM narrative unavailable (%s), using template.", exc)
            return self._template_markdown(report)

    def save(self, report: ValidationReport, bundle_dir: Path, config: ValidationConfig) -> None:
        """Write report.json and report.md to results/."""
        results_dir = Path(bundle_dir) / "results"
        results_dir.mkdir(parents=True, exist_ok=True)

        (results_dir / "report.json").write_text(
            self.generate_json(report), encoding="utf-8",
        )
        (results_dir / "report.md").write_text(
            self.generate_markdown(report, config), encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _template_markdown(self, report: ValidationReport) -> str:
        """Structured Markdown template (no LLM needed)."""
        lines: list[str] = []
        lines.append(f"# Validation Report: {report.bundle_id}")
        lines.append("")
        lines.append(f"**Verdict:** {report.overall_verdict}")
        lines.append(f"**Generated:** {report.generated_at}")
        lines.append(f"**Methodology:** {report.methodology_snapshot or 'N/A'}")
        lines.append("")

        # Model info
        mt = report.model_profile.get("model_type", "?")
        fw = report.model_profile.get("framework", "?")
        algo = report.model_profile.get("algorithm", "?")
        lines.append(f"## Model: {algo} ({fw}, {mt})")
        task = report.model_profile.get("task_description", "")
        if task:
            lines.append(f"\n{task}\n")

        # Stages summary
        lines.append("## Stages")
        lines.append("")
        lines.append("| Stage | Name | Status | Checks | Findings |")
        lines.append("|-------|------|--------|--------|----------|")
        for s in report.stages:
            total = len(s.checks)
            failed = sum(1 for c in s.checks if not c.passed)
            lines.append(f"| {s.stage} | {s.stage_name} | {s.status} | {total} | {failed} |")
        lines.append("")

        # Separate qualitative (S0, S4 code-level, S8) and quantitative (S2, S3, S5, S6, S7) findings
        qualitative_stages = {"S0", "S4", "S8", "S1"}
        quantitative_stages = {"S2", "S3", "S5", "S6", "S7"}

        qual_findings = []
        quant_findings = []
        for s in report.stages:
            for c in s.checks:
                if not c.passed:
                    if s.stage in qualitative_stages:
                        qual_findings.append(c)
                    elif s.stage in quantitative_stages:
                        quant_findings.append(c)
                    else:
                        qual_findings.append(c)  # default to qualitative

        # Qualitative findings
        if qual_findings:
            lines.append("## Qualitative Analysis Findings")
            lines.append("")
            lines.append("Architecture, target formulation, data pipeline, code quality:")
            lines.append("")
            for f in qual_findings:
                sev = f"[{f.severity}]" if f.severity != "pass" else ""
                lines.append(f"- **{f.check_id}** {sev}: {f.details}")
            lines.append("")

        # Quantitative findings
        if quant_findings:
            lines.append("## Quantitative Analysis Findings")
            lines.append("")
            lines.append("Metrics, sensitivity, stability, drill-downs:")
            lines.append("")
            for f in quant_findings:
                score_str = f" (score: {f.score})" if f.score is not None else ""
                lines.append(f"- **{f.check_id}**{score_str}: {f.details}")
            lines.append("")

        # Critical findings (across both blocks)
        if report.critical_findings:
            lines.append("## Critical Findings")
            lines.append("")
            for f in report.critical_findings:
                lines.append(f"- **{f.check_id}**: {f.details}")
            lines.append("")

        # Hard recommendations
        if report.hard_recommendations:
            lines.append("## Hard Recommendations (implementable)")
            lines.append("")
            for i, r in enumerate(report.hard_recommendations, 1):
                lines.append(f"### {i}. {r.problem}")
                lines.append(f"**Fix:** {r.recommendation}")
                if r.implementation_sketch:
                    lines.append(f"```\n{r.implementation_sketch}\n```")
                if r.estimated_metric_impact:
                    impact = ", ".join(f"{k}: +{v}" for k, v in r.estimated_metric_impact.items())
                    lines.append(f"**Expected impact:** {impact} (confidence: {r.confidence})")
                lines.append("")

        # Soft recommendations
        if report.soft_recommendations:
            lines.append("## Soft Recommendations (requires human action)")
            lines.append("")
            for i, r in enumerate(report.soft_recommendations, 1):
                lines.append(f"{i}. **{r.problem}** — {r.recommendation}")
            lines.append("")

        # Meta scores
        if report.meta_scores:
            lines.append("## Confidence Scores")
            lines.append("")
            for k, v in report.meta_scores.items():
                lines.append(f"- {k}: {v:.2f}")
            lines.append("")

        return "\n".join(lines)

    def _llm_narrative(self, report: ValidationReport, config: ValidationConfig) -> str:
        """Generate a narrative report using LLM."""
        import asyncio
        from ouroboros.llm import LLMClient

        summary_data = {
            "verdict": report.overall_verdict,
            "model_type": report.model_profile.get("model_type", "?"),
            "algorithm": report.model_profile.get("algorithm", "?"),
            "stages_summary": [
                {"stage": s.stage, "status": s.status,
                 "findings": sum(1 for c in s.checks if not c.passed)}
                for s in report.stages
            ],
            "critical_count": len(report.critical_findings),
            "hard_rec_count": len(report.hard_recommendations),
            "soft_rec_count": len(report.soft_recommendations),
        }

        prompt = (
            "Write a concise executive summary (200-400 words) for this ML model "
            "validation report. Include: overall verdict, key findings, top recommendations.\n\n"
            f"Data:\n```json\n{json.dumps(summary_data, indent=2)}\n```"
        )

        client = LLMClient()
        response, _usage = client.chat(
            messages=[
                {"role": "system", "content": "You write clear, concise ML validation reports."},
                {"role": "user", "content": prompt},
            ],
            model=config.report_model,
            reasoning_effort="low",
            max_tokens=2048,
        )

        text = response.get("content", "")
        if isinstance(text, list):
            text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))

        # Prepend with template, append LLM narrative
        template = self._template_markdown(report)
        return template + "\n---\n\n## Executive Summary (AI-generated)\n\n" + text.strip() + "\n"
