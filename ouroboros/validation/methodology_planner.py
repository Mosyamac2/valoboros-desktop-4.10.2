"""
Valoboros — per-model methodology planner.

Before running checks, the LLM designs a custom validation plan for each
specific model: which checks to run, which to skip, and what new checks
to create.  Falls back to a deterministic plan if LLM is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from ouroboros.validation.check_registry import CheckRegistry
from ouroboros.validation.types import (
    MethodologyPlan,
    ModelProfile,
    ValidationConfig,
)

log = logging.getLogger(__name__)

_METHODOLOGY_PROMPT = """\
You are a model validation methodology expert.

## Model Under Validation
{model_summary}

## Available Checks
{checks_summary}

## Knowledge Base — Relevant Patterns
{knowledge}

## Instructions
Design a validation methodology for this specific model. Return a JSON object:
{{
  "bundle_id": "{bundle_id}",
  "model_summary": "<one paragraph summary>",
  "risk_priorities": ["<ordered risk areas, most important first>"],
  "checks_to_run": ["<check_ids to run>"],
  "checks_to_skip": ["<check_ids to skip>"],
  "checks_to_create": [
    {{"check_id": "S{{}}.NEW_CHECK", "stage": "S{{}}", "description": "...", "rationale": "..."}}
  ],
  "knowledge_references": [],
  "similar_past_validations": [],
  "methodology_version": "0.1.0",
  "confidence": 0.0-1.0
}}

Return ONLY JSON. No markdown fences.
"""


class MethodologyPlanner:
    """Creates a custom validation methodology for each model."""

    def __init__(
        self,
        bundle_dir: Path,
        profile: ModelProfile,
        check_registry: CheckRegistry,
        config: ValidationConfig,
        knowledge_dir: Path,
    ) -> None:
        self._bundle_dir = Path(bundle_dir)
        self._profile = profile
        self._registry = check_registry
        self._config = config
        self._knowledge_dir = Path(knowledge_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def plan(self) -> MethodologyPlan:
        """Create a validation methodology plan using LLM, with fallback."""
        try:
            return await self._llm_plan()
        except Exception as exc:
            log.warning("LLM methodology planning failed (%s), using fallback.", exc)
            return self._fallback_plan()

    def plan_sync(self) -> MethodologyPlan:
        """Synchronous wrapper."""
        try:
            return asyncio.get_event_loop().run_until_complete(self.plan())
        except RuntimeError:
            return self._fallback_plan()

    # ------------------------------------------------------------------
    # LLM-based planning
    # ------------------------------------------------------------------

    async def _llm_plan(self) -> MethodologyPlan:
        from ouroboros.llm import LLMClient

        model_summary = self._build_model_summary()
        checks_summary = self._build_checks_summary()
        knowledge = self._gather_knowledge()

        prompt = _METHODOLOGY_PROMPT.format(
            model_summary=model_summary,
            checks_summary=checks_summary,
            knowledge=knowledge or "(no knowledge base entries found)",
            bundle_id=self._profile.bundle_id,
        )

        client = LLMClient()
        response, _usage = await asyncio.to_thread(
            client.chat,
            messages=[
                {"role": "system", "content": "You design ML model validation methodologies. Return only JSON."},
                {"role": "user", "content": prompt},
            ],
            model=self._config.comprehension_model,
            reasoning_effort="medium",
            max_tokens=4096,
        )

        text = response.get("content", "")
        if isinstance(text, list):
            text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]

        plan = MethodologyPlan.from_dict(json.loads(text.strip()))
        self._save_plan(plan)
        return plan

    # ------------------------------------------------------------------
    # Fallback (deterministic, no LLM)
    # ------------------------------------------------------------------

    def _fallback_plan(self) -> MethodologyPlan:
        """Select all applicable checks, skip none, propose nothing new."""
        profile_dict = self._profile.to_dict()
        all_checks = self._registry.list_checks(enabled_only=True)

        applicable = self._registry.get_checks_for_stage("S0", profile_dict)
        for stage in ["S2", "S3", "S4", "S5", "S6", "S7", "S8"]:
            applicable.extend(self._registry.get_checks_for_stage(stage, profile_dict))

        applicable_ids = [c.check_id for c in applicable]
        all_ids = [c.check_id for c in all_checks]
        skipped_ids = [cid for cid in all_ids if cid not in applicable_ids]

        plan = MethodologyPlan(
            bundle_id=self._profile.bundle_id,
            model_summary=f"{self._profile.algorithm} ({self._profile.framework}, {self._profile.model_type}): {self._profile.task_description}",
            risk_priorities=self._default_risk_priorities(),
            checks_to_run=sorted(set(applicable_ids)),
            checks_to_skip=sorted(set(skipped_ids)),
            checks_to_create=[],
            knowledge_references=[],
            similar_past_validations=[],
            methodology_version=self._config.methodology_version,
            confidence=0.3,  # low confidence for fallback
        )
        return plan

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_model_summary(self) -> str:
        p = self._profile
        parts = [
            f"Model type: {p.model_type} (confidence: {p.model_type_confidence})",
            f"Framework: {p.framework} (confidence: {p.framework_confidence})",
            f"Algorithm: {p.algorithm}",
            f"Data format: {p.data_format}",
            f"Task: {p.task_description}",
        ]
        if p.target_column:
            parts.append(f"Target: {p.target_column}")
        if p.feature_columns:
            parts.append(f"Features: {p.feature_columns[:10]}")
        if p.temporal_column:
            parts.append(f"Temporal column: {p.temporal_column}")
        if p.protected_attributes_candidates:
            parts.append(f"Protected attributes: {p.protected_attributes_candidates}")
        if p.comprehension_gaps:
            parts.append(f"Comprehension gaps: {p.comprehension_gaps}")
        return "\n".join(parts)

    def _build_checks_summary(self) -> str:
        checks = self._registry.list_checks(enabled_only=True)
        lines = []
        for c in checks:
            tags = ", ".join(c.tags) if c.tags else "(universal)"
            lines.append(f"- {c.check_id} [{c.stage}] ({c.check_type}): {c.name} — tags: {tags}")
        return "\n".join(lines) or "(no checks registered)"

    def _gather_knowledge(self) -> str:
        """Read relevant knowledge base files."""
        parts: list[str] = []

        # Model-type specific knowledge
        mt_file = self._knowledge_dir / f"model_type_{self._profile.model_type}.md"
        if mt_file.exists():
            try:
                content = mt_file.read_text(encoding="utf-8")[:3000]
                parts.append(f"### {mt_file.name}\n{content}")
            except Exception:
                pass

        # Cross-cutting patterns
        patterns_file = self._knowledge_dir / "validation_patterns.md"
        if patterns_file.exists():
            try:
                content = patterns_file.read_text(encoding="utf-8")[:3000]
                parts.append(f"### {patterns_file.name}\n{content}")
            except Exception:
                pass

        return "\n\n".join(parts)

    def _default_risk_priorities(self) -> list[str]:
        """Heuristic risk priorities based on model profile."""
        priorities = []
        if self._profile.temporal_column:
            priorities.append("temporal_leakage")
        priorities.append("overfitting")
        priorities.append("data_leakage")
        if self._profile.data_format == "tabular":
            priorities.append("feature_sensitivity")
        if self._profile.protected_attributes_candidates:
            priorities.append("fairness")
        priorities.append("code_quality")
        return priorities

    def _generate_methodology_md(self, plan: MethodologyPlan) -> str:
        """Render plan as human-readable Markdown."""
        lines = [
            f"# Validation Methodology: {plan.model_summary}",
            "",
            f"**Confidence:** {plan.confidence}",
            f"**Methodology version:** {plan.methodology_version}",
            "",
            "## Risk Priorities (ordered)",
            "",
        ]
        for i, risk in enumerate(plan.risk_priorities, 1):
            lines.append(f"{i}. **{risk}**")
        lines += ["", "## Checks Selected", ""]
        for cid in plan.checks_to_run:
            lines.append(f"- {cid}")
        if plan.checks_to_skip:
            lines += ["", "## Checks Skipped", ""]
            for cid in plan.checks_to_skip:
                lines.append(f"- {cid}")
        if plan.checks_to_create:
            lines += ["", "## New Checks Proposed", ""]
            for proposal in plan.checks_to_create:
                lines.append(f"- **{proposal.get('check_id', '?')}**: {proposal.get('description', '')}")
        if plan.knowledge_references:
            lines += ["", "## Knowledge Referenced", ""]
            for ref in plan.knowledge_references:
                lines.append(f"- {ref}")
        lines.append("")
        return "\n".join(lines)

    def _save_plan(self, plan: MethodologyPlan) -> None:
        """Write methodology.md and methodology_plan.json to bundle."""
        meth_dir = self._bundle_dir / "methodology"
        meth_dir.mkdir(parents=True, exist_ok=True)

        (meth_dir / "methodology_plan.json").write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (meth_dir / "methodology.md").write_text(
            self._generate_methodology_md(plan),
            encoding="utf-8",
        )
