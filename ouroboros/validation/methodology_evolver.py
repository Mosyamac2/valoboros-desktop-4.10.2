"""
Valoboros — methodology evolver.

Creates, fixes, and deletes validation checks based on effectiveness data
and knowledge accumulated from past validations and literature scans.
Follows the 7-step evolution protocol from BIBLE.md.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from ouroboros.validation.check_registry import CheckRegistry
from ouroboros.validation.effectiveness import EffectivenessTracker, EvolutionTarget
from ouroboros.validation.types import EvolutionAction, ValidationConfig

log = logging.getLogger(__name__)


class MethodologyEvolver:
    """Creates and improves validation checks based on accumulated knowledge."""

    def __init__(
        self,
        repo_dir: Path,
        check_registry: CheckRegistry,
        effectiveness_tracker: EffectivenessTracker,
        knowledge_dir: Path,
        config: ValidationConfig,
    ) -> None:
        self._repo_dir = Path(repo_dir)
        self._registry = check_registry
        self._tracker = effectiveness_tracker
        self._knowledge_dir = Path(knowledge_dir)
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evolve(self) -> Optional[EvolutionAction]:
        """Pick ONE evolution action and execute it. Returns None if nothing to do."""
        return self._do_evolve()

    def evolve_sync(self) -> Optional[EvolutionAction]:
        """Synchronous wrapper for testing."""
        return self._do_evolve()

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _do_evolve(self) -> Optional[EvolutionAction]:
        targets = self._get_targets()
        if not targets:
            # Check knowledge base for arxiv-inspired ideas
            arxiv_ideas = self._get_arxiv_ideas()
            if arxiv_ideas:
                return self._create_from_idea(arxiv_ideas[0])
            return None

        # Pick highest-priority target
        target = targets[0]

        if target.target_type == "fix_check":
            return self._fix_check(target)
        elif target.target_type == "create_check":
            return self._create_check(target)
        elif target.target_type in ("delete_check", "disable_check"):
            return self._disable_check(target)
        else:
            log.info("Unknown evolution target type: %s", target.target_type)
            return None

    # ------------------------------------------------------------------
    # Target discovery
    # ------------------------------------------------------------------

    def _get_targets(self) -> list[EvolutionTarget]:
        """Get prioritized evolution targets from effectiveness tracker."""
        return self._tracker.get_evolution_targets()

    def _get_arxiv_ideas(self) -> list[dict[str, Any]]:
        """Read arxiv_recent.md for check ideas not yet implemented."""
        arxiv_file = self._knowledge_dir / "arxiv_recent.md"
        if not arxiv_file.exists():
            return []
        # Simple: return empty for now — arxiv ideas require LLM parsing
        # which is deferred to when the consciousness loop calls this
        return []

    # ------------------------------------------------------------------
    # Evolution actions
    # ------------------------------------------------------------------

    def _fix_check(self, target: EvolutionTarget) -> EvolutionAction:
        """Fix a check with low precision by rewriting its code."""
        check_id = target.description.split()[1] if len(target.description.split()) > 1 else ""
        # Extract check_id from description like "Check S8.CODE_SMELLS has low precision"
        for word in target.description.split():
            if "." in word and word[0] == "S":
                check_id = word
                break

        if not check_id:
            return EvolutionAction(
                action_type="fix_check", check_id="",
                description="Could not determine check_id from target",
                success=False, error_message="No check_id in target description",
            )

        try:
            check = self._registry.get_check(check_id)
        except KeyError:
            return EvolutionAction(
                action_type="fix_check", check_id=check_id,
                description=f"Check {check_id} not found in registry",
                success=False, error_message="Check not found",
            )

        # For now, fixing requires LLM — return a description of what to fix
        stats = self._tracker.get_finding_stats(check_id)
        return EvolutionAction(
            action_type="fix_check", check_id=check_id,
            description=(
                f"Check {check_id} has precision {stats.precision:.2f} "
                f"(TP={stats.tp:.0f}, FP={stats.fp:.0f}). "
                f"Needs code improvement to reduce false positives."
            ),
            success=False,
            error_message="LLM-based fix not yet implemented in offline mode",
        )

    def _create_check(self, target: EvolutionTarget) -> EvolutionAction:
        """Create a new check based on an evolution target."""
        return EvolutionAction(
            action_type="create_check", check_id="",
            description=f"Proposed: {target.description}",
            success=False,
            error_message="LLM-based check creation not yet implemented in offline mode",
        )

    def _create_from_idea(self, idea: dict[str, Any]) -> EvolutionAction:
        """Create a check from an arxiv-inspired idea."""
        return EvolutionAction(
            action_type="create_check", check_id="",
            description=f"Arxiv-inspired: {idea.get('description', '?')}",
            success=False,
            error_message="LLM-based check creation not yet implemented in offline mode",
        )

    def _disable_check(self, target: EvolutionTarget) -> EvolutionAction:
        """Disable a low-value check."""
        check_id = ""
        for word in target.description.split():
            if "." in word and word[0] == "S":
                check_id = word
                break

        if not check_id:
            return EvolutionAction(
                action_type="delete_check", check_id="",
                description="Could not determine check_id",
                success=False, error_message="No check_id in target",
            )

        try:
            self._registry.disable_check(check_id, f"Evolution: {target.description}")
            return EvolutionAction(
                action_type="delete_check", check_id=check_id,
                description=f"Disabled {check_id}: {target.description}",
                success=True, error_message=None,
            )
        except KeyError:
            return EvolutionAction(
                action_type="delete_check", check_id=check_id,
                description=f"Check {check_id} not found",
                success=False, error_message="Check not found in registry",
            )
