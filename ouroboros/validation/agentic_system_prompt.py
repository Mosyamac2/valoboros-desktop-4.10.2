"""Agentic validation — system prompt builder.

Builds the rich system prompt that wraps every per-bundle ClaudeSDKClient
session in :mod:`ouroboros.validation.agentic_runner`. Inlines:

  - BIBLE.md (constitution)
  - the validator-role section of prompts/SYSTEM.md
  - the validation playbook from docs/CHECKLISTS.md
  - accumulated cross-bundle patterns (validation_patterns.md)
  - per-model-type knowledge (model_type_<type>.md, when present)
  - general error patterns (patterns.md, when present)
  - the fast deterministic pre-check summary for THIS bundle

All sources are read defensively — a missing file is logged and skipped,
never raised. The prompt always has the constitutional core (BIBLE +
validator role); everything else is best-effort context.

Outputs a single string. The Claude Code SDK passes it as ``system_prompt``
via :class:`claude_agent_sdk.ClaudeAgentOptions`. With prompt caching the
~30-50 KB cost is amortized across all turns of the session and effectively
free under the OAuth subscription.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


_DEFAULT_REPO_DIR = Path(__file__).resolve().parents[2]


def _read_text_safely(path: Path, label: str) -> str:
    """Return ``path``'s text or an empty string if it doesn't exist / can't be read."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.debug("agentic_system_prompt: %s not found at %s", label, path)
        return ""
    except OSError as exc:
        log.warning("agentic_system_prompt: %s read failed at %s: %s", label, path, exc)
        return ""


def _extract_validator_role(system_md_text: str) -> str:
    """Pull the validator-identity portion out of prompts/SYSTEM.md.

    SYSTEM.md is the operational brain of the full agent, much of which is
    irrelevant to a single validation session (consciousness loop, scheduling,
    multi-tool orchestration, etc.). We extract roughly the first 4 KB which
    historically holds the identity + mission, plus any explicit ``## Validator``
    or ``## Ouroboros-V`` section if present.
    """
    if not system_md_text:
        return (
            "I am Ouroboros-V, the validator manifestation of Ouroboros. "
            "My mission is to find real model problems, never hallucinate "
            "findings, and produce recommendations whose implementation "
            "measurably improves model metrics."
        )

    head = system_md_text[:4000]

    sections: list[str] = [head]
    lines = system_md_text.splitlines()
    capture = False
    block: list[str] = []
    for line in lines:
        if line.startswith("## "):
            heading = line.lower()
            if "validator" in heading or "ouroboros-v" in heading or "valoboros" in heading:
                capture = True
                if block:
                    sections.append("\n".join(block))
                    block = []
            else:
                if capture:
                    sections.append("\n".join(block))
                    block = []
                capture = False
        if capture:
            block.append(line)
    if capture and block:
        sections.append("\n".join(block))

    return "\n\n".join(s.strip() for s in sections if s.strip())


def _format_pre_check_summary(summary: dict[str, Any] | None) -> str:
    """Render the fast deterministic pre-check summary as bullet points.

    The summary structure is intentionally loose — any dict produced by the
    legacy S0-S8 helpers will be rendered. Keys like ``findings`` /
    ``triggered_checks`` get bullet-listed; everything else is rendered as
    ``key: value`` lines.
    """
    if not summary:
        return (
            "(No fast deterministic pre-check was run for this bundle. "
            "Build your understanding from `./raw/` directly.)"
        )

    lines: list[str] = []
    findings = summary.get("findings") or summary.get("triggered_checks") or []
    if findings:
        lines.append("Pre-check findings (raw signals, not ground truth):")
        for f in findings:
            if isinstance(f, dict):
                cid = f.get("check_id") or f.get("id") or "?"
                detail = f.get("details") or f.get("detail") or ""
                lines.append(f"- {cid}: {detail}".rstrip(": "))
            else:
                lines.append(f"- {f}")
    skipped = summary.get("skipped_checks") or summary.get("errors") or []
    if skipped:
        lines.append("")
        lines.append("Pre-check could not run (treat as 'unknown', not 'pass'):")
        for s in skipped:
            if isinstance(s, dict):
                cid = s.get("check_id") or s.get("id") or "?"
                reason = s.get("reason") or s.get("error") or ""
                lines.append(f"- {cid}: {reason}".rstrip(": "))
            else:
                lines.append(f"- {s}")
    # Render any other top-level scalar keys for traceability
    other = {
        k: v for k, v in summary.items()
        if k not in ("findings", "triggered_checks", "skipped_checks", "errors")
        and isinstance(v, (str, int, float, bool))
    }
    if other:
        lines.append("")
        lines.append("Other pre-check data:")
        for k, v in other.items():
            lines.append(f"- {k}: {v}")

    if not lines:
        return (
            "(Pre-check ran but produced no structured findings. "
            "Build your understanding from `./raw/` directly.)"
        )
    return "\n".join(lines)


def build_validator_system_prompt(
    bundle_dir: Path | str,
    model_type: str = "unknown",
    pre_check_summary: dict[str, Any] | None = None,
    knowledge_dir: Path | str | None = None,
    repo_dir: Path | str | None = None,
) -> str:
    """Assemble the system prompt for a per-bundle agentic validation session.

    Args:
      bundle_dir: the per-bundle working directory the agent operates inside.
      model_type: ``"classification" | "regression" | "ranking" | …``.
        Used to inline the per-type knowledge file when present.
      pre_check_summary: optional dict produced by the fast deterministic
        pre-check helpers; rendered as a bullet list. ``None`` if no
        pre-check ran.
      knowledge_dir: where ``validation_patterns.md`` / ``model_type_*.md`` /
        ``patterns.md`` live. Defaults to ``~/Ouroboros/data/memory/knowledge``.
      repo_dir: where ``BIBLE.md`` / ``prompts/SYSTEM.md`` / ``docs/CHECKLISTS.md``
        live. Defaults to this package's repo root.

    Returns:
      A single string ready to pass as ``system_prompt`` to ``ClaudeAgentOptions``.
    """
    bundle_dir = Path(bundle_dir).resolve()
    repo = Path(repo_dir).resolve() if repo_dir else _DEFAULT_REPO_DIR
    if knowledge_dir is None:
        knowledge = Path(
            os.environ.get(
                "OUROBOROS_KNOWLEDGE_DIR",
                Path.home() / "Ouroboros" / "data" / "memory" / "knowledge",
            )
        )
    else:
        knowledge = Path(knowledge_dir)

    bible_text = _read_text_safely(repo / "BIBLE.md", "BIBLE.md")
    system_md = _read_text_safely(repo / "prompts" / "SYSTEM.md", "prompts/SYSTEM.md")
    checklists = _read_text_safely(repo / "docs" / "CHECKLISTS.md", "docs/CHECKLISTS.md")

    validator_role = _extract_validator_role(system_md)

    validation_patterns = _read_text_safely(
        knowledge / "validation_patterns.md", "validation_patterns.md"
    )
    type_knowledge = ""
    if model_type and model_type != "unknown":
        type_knowledge = _read_text_safely(
            knowledge / f"model_type_{model_type}.md",
            f"model_type_{model_type}.md",
        )
    error_patterns = _read_text_safely(knowledge / "patterns.md", "patterns.md")

    pre_check_block = _format_pre_check_summary(pre_check_summary)

    sections: list[str] = []

    sections.append(
        "# Identity & Constitution\n\n"
        + (bible_text or "(BIBLE.md unavailable — operate by the principles of "
                          "no-hallucination, qualitative-before-quantitative, "
                          "feasible-recommendations.)")
    )

    sections.append("# Your role for this session\n\n" + validator_role)

    if checklists:
        sections.append(
            "# Validation playbook (from docs/CHECKLISTS.md)\n\n"
            "Apply these standards to your methodology and your interpretation "
            "of results. The qualitative-before-quantitative principle and the "
            "no-false-positives commitment are non-negotiable.\n\n"
            + checklists
        )

    sections.append(
        "# What I have learned across past validations\n\n"
        "## Cross-bundle patterns (validation_patterns.md)\n\n"
        + (validation_patterns
            or "(No accumulated cross-bundle patterns yet. This may be one of the "
               "first validations, or the reflection engine hasn't surfaced "
               "patterns from past reports. Rely on first-principles + BIBLE.)")
        + "\n\n## Per-model-type knowledge\n\n"
        + (type_knowledge
            or f"(No accumulated knowledge yet for model_type={model_type!r}. "
               "Reason from first principles for this type.)")
        + "\n\n## General error patterns (patterns.md)\n\n"
        + (error_patterns
            or "(No general error-pattern register yet.)")
    )

    sections.append(
        "# What the fast deterministic pre-check found\n\n"
        + pre_check_block
        + "\n\n"
        "NOTE: the pre-check is mechanical and has zero context about this "
        "bundle's domain. Treat its findings as raw signals, not as ground "
        "truth. In Phase A you should reason about whether each pre-check "
        "finding is a real issue or a structural artifact of how the bundle "
        "was packaged (e.g. hardcoded `/kaggle/input/` paths from a Kaggle "
        "kernel). When in doubt, dismiss the pre-check signal and explain why."
    )

    sections.append(
        "# Tools available\n\n"
        "You have: Read, Edit, Write, Glob, Grep, Bash. Use Bash for `pip "
        "install` inside the bundle's local venv when the bundle needs deps. "
        "Stay strictly inside the cwd — the PreToolUse safety hook will "
        "block any write that escapes this bundle's workdir."
    )

    sections.append(
        "# Bundle location\n\n"
        f"You are running with cwd = `{bundle_dir}`. Bundle data is at `./raw/`. "
        "Methodology output goes to `./methodology/`. Phase B's Python project "
        "will land at `./methodology/validation_project/`. Phase C / D outputs "
        "go to `./results/`. Do not write outside these subtrees."
    )

    return "\n\n---\n\n".join(sections).strip() + "\n"
