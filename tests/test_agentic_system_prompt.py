"""Phase 1 tests — agentic_system_prompt.build_validator_system_prompt."""

from __future__ import annotations

from pathlib import Path

from ouroboros.validation.agentic_system_prompt import build_validator_system_prompt


def test_build_includes_required_sections(tmp_path: Path) -> None:
    """The builder must always emit the load-bearing sections, even when
    every optional knowledge file is missing."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    knowledge_dir = tmp_path / "knowledge"  # deliberately empty
    repo_dir = tmp_path / "repo"
    (repo_dir / "prompts").mkdir(parents=True)
    (repo_dir / "docs").mkdir(parents=True)
    (repo_dir / "BIBLE.md").write_text("# BIBLE\nP3 LLM-First.", encoding="utf-8")
    (repo_dir / "prompts" / "SYSTEM.md").write_text(
        "I am Ouroboros, the agent.", encoding="utf-8"
    )
    (repo_dir / "docs" / "CHECKLISTS.md").write_text(
        "# Validation checklist\n- qualitative before quantitative",
        encoding="utf-8",
    )

    prompt = build_validator_system_prompt(
        bundle_dir=bundle_dir,
        model_type="classification",
        pre_check_summary={"findings": [{"check_id": "S8.SMELLS", "details": "x"}]},
        knowledge_dir=knowledge_dir,
        repo_dir=repo_dir,
    )

    assert "# Identity & Constitution" in prompt
    assert "P3 LLM-First" in prompt
    assert "# Your role for this session" in prompt
    assert "# Validation playbook" in prompt
    assert "qualitative before quantitative" in prompt
    assert "# What I have learned across past validations" in prompt
    assert "# What the fast deterministic pre-check found" in prompt
    assert "S8.SMELLS" in prompt
    assert "# Tools available" in prompt
    assert "# Bundle location" in prompt
    assert str(bundle_dir) in prompt
    # Per-model-type knowledge file was missing → builder must gracefully say so
    assert "model_type='classification'" in prompt or "classification" in prompt


def test_build_handles_missing_optional_inputs(tmp_path: Path) -> None:
    """No BIBLE, no SYSTEM.md, no checklists, no pre-check, no knowledge —
    the builder must still produce a useable prompt with the fallback role
    statement, not crash."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    knowledge_dir = tmp_path / "knowledge"  # never created
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    prompt = build_validator_system_prompt(
        bundle_dir=bundle_dir,
        model_type="unknown",
        pre_check_summary=None,
        knowledge_dir=knowledge_dir,
        repo_dir=repo_dir,
    )

    # Even with nothing on disk, we get the constitutional anchor + role fallback
    assert "Ouroboros-V" in prompt or "validator" in prompt.lower()
    assert "BIBLE.md unavailable" in prompt
    # Pre-check absent → explicit message about that
    assert "No fast deterministic pre-check" in prompt
    # No model-type knowledge → explicit message
    assert "No accumulated knowledge yet for model_type='unknown'" in prompt
    # Bundle location must still be present
    assert "# Bundle location" in prompt
    assert str(bundle_dir) in prompt
