"""Agentic phase user-prompt templates.

Each .md file in this directory is a user prompt for one phase of the
agentic validation flow (A=methodology, B=implement, C=execute, D=report).

Loaded at runtime by ``ouroboros.validation.agentic_runner`` via
:func:`load_phase_prompt`. The templates are plain text with
``{placeholder}`` markers that the runner fills in per bundle.

These files are an evolution target — the methodology evolver may
append/edit directives here. The 7-step protocol gates such changes.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


def load_phase_prompt(phase: str) -> str:
    """Read the .md prompt template for the given phase ('a'..'d').

    Raises FileNotFoundError if the template is missing — callers should
    not silently fall back to a stale embedded copy.
    """
    name = f"phase_{phase.lower()}_*.md"
    matches = sorted(_PROMPTS_DIR.glob(name))
    if not matches:
        raise FileNotFoundError(
            f"No phase prompt template found for phase {phase!r} "
            f"in {_PROMPTS_DIR}"
        )
    return matches[0].read_text(encoding="utf-8")
