"""Phase 10 tests — SourceEvolutionExecutor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ouroboros.validation.agentic_source_evolution import (
    EvolutionAttempt,
    SourceEvolutionExecutor,
)
from ouroboros.validation.types import EvolutionProposal


def _make_proposal(**kw) -> EvolutionProposal:
    defaults = dict(
        proposal_id="evo.2026-05-17.test",
        target_kind="prompt",
        target_path="ouroboros/validation/agentic_prompts/phase_a_methodology.md",
        rationale="test rationale",
        directive="test directive — append a single line at the end of the file.",
        source_pattern_kinds=["recurring_failure"],
        source_pattern_count=3,
        confidence=0.7,
        estimated_effort="trivial",
        created_at="2026-05-17T00:00:00Z",
    )
    defaults.update(kw)
    return EvolutionProposal(**defaults)


def test_executor_denies_safety_critical_paths_and_outside_allow_list(
    tmp_path: Path,
) -> None:
    """Three categories must be denied:
       (1) explicit SAFETY-CRITICAL files (BIBLE.md, safety.py, ...)
       (2) target_kind/target_path mismatch
       (3) paths outside the evolution allow-list
    Each must persist an attempt row with outcome='denied' and no edit
    invocation occurs.
    """
    knowledge = tmp_path / "knowledge"
    fake_editor_calls: list[dict] = []

    def fake_editor(**kw):
        fake_editor_calls.append(kw)
        return {"success": True, "changed_files": []}

    executor = SourceEvolutionExecutor(
        repo_dir=tmp_path / "repo",
        knowledge_dir=knowledge,
        editor=fake_editor,
    )
    (tmp_path / "repo").mkdir()

    # 1. SAFETY-CRITICAL: BIBLE.md
    p_bible = _make_proposal(
        target_kind="prompt",
        target_path="BIBLE.md",  # constructor allows prompt kind, target check rejects
    )
    # The target_kind/path check happens before the deny-list, since
    # target_kind=prompt requires the agentic_prompts/ prefix. Either
    # way it must be denied.
    res_bible = executor.attempt(p_bible, dry_run=True)
    assert res_bible.outcome == "denied"
    assert (
        "safety-critical" in res_bible.reason.lower()
        or "prefix" in res_bible.reason.lower()
    )

    # 2. SAFETY-CRITICAL: sandbox.py — even with the matching pre_check kind
    p_sandbox = _make_proposal(
        target_kind="pre_check",
        target_path="ouroboros/validation/sandbox.py",
    )
    res_sandbox = executor.attempt(p_sandbox, dry_run=True)
    assert res_sandbox.outcome == "denied"
    # sandbox.py IS in the deny list AND doesn't match the pre_check prefix
    # which is ouroboros/validation/checks/. Either denial path is fine.

    # 3. Mismatch: prompt kind pointing at a helper path
    p_mismatch = _make_proposal(
        target_kind="prompt",
        target_path="ouroboros/validation/agentic_helpers/foo.py",
    )
    res_mismatch = executor.attempt(p_mismatch, dry_run=True)
    assert res_mismatch.outcome == "denied"
    assert "does not match" in res_mismatch.reason or "prefix" in res_mismatch.reason

    # 4. Outside allow-list entirely: target_kind=prompt path that doesn't
    #    start with agentic_prompts/ — caught by the prefix-consistency
    #    check before allow-list, but for system_prompt kind pointing
    #    somewhere bogus we get the allow-list / repo-resolve denial.
    p_bogus = _make_proposal(
        target_kind="system_prompt",
        target_path="ouroboros/agent.py",
    )
    res_bogus = executor.attempt(p_bogus, dry_run=True)
    assert res_bogus.outcome == "denied"

    # No edit was attempted for any of these (dry_run + denied combo)
    assert fake_editor_calls == []

    # Attempts persisted to evolution_attempts.jsonl
    rows = [
        json.loads(l)
        for l in (knowledge / "evolution_attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert len(rows) == 4
    assert all(r["outcome"] == "denied" for r in rows)


def test_executor_applies_allowed_proposal_via_injected_editor(
    tmp_path: Path,
) -> None:
    """A well-formed proposal against an allow-listed path runs the
    injected editor and records outcome='applied' with the editor's
    reported changed_files."""
    repo = tmp_path / "repo"
    (repo / "ouroboros" / "validation" / "agentic_prompts").mkdir(parents=True)
    (repo / "ouroboros" / "validation" / "agentic_prompts" / "phase_a_methodology.md").write_text(
        "Original methodology prompt.\n", encoding="utf-8"
    )

    fake_editor_calls: list[dict] = []

    def fake_editor(**kw):
        fake_editor_calls.append(kw)
        # Simulate Claude having appended a line
        return {
            "success": True,
            "changed_files": [
                "ouroboros/validation/agentic_prompts/phase_a_methodology.md"
            ],
            "session_id": "ses-evo-1",
            "cost_usd": 0.01,
        }

    knowledge = tmp_path / "knowledge"
    executor = SourceEvolutionExecutor(
        repo_dir=repo, knowledge_dir=knowledge, editor=fake_editor,
    )

    proposal = _make_proposal(confidence=0.9)
    attempt = executor.attempt(proposal, dry_run=False)

    assert attempt.outcome == "applied"
    assert attempt.changed_files == [
        "ouroboros/validation/agentic_prompts/phase_a_methodology.md"
    ]
    assert attempt.sdk_result is not None
    assert attempt.sdk_result["session_id"] == "ses-evo-1"

    # Editor was called exactly once with the proposal's directive
    assert len(fake_editor_calls) == 1
    call = fake_editor_calls[0]
    assert call["cwd"] == str(repo)
    assert call["target_path"] == proposal.target_path
    assert "append a single line" in call["directive"]
    assert call["proposal_id"] == proposal.proposal_id

    # Persisted
    rows = [
        json.loads(l)
        for l in (knowledge / "evolution_attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "applied"
    assert rows[0]["dry_run"] is False
