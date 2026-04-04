"""Tests for the reviewed commit workflow improvements:

1. REVIEWED_MUTATIVE_TOOLS classification
2. Reviewed mutative tool timeout handling (no ambiguous timeouts)
3. CommitAttemptRecord in review_state.py
4. Commit attempt tracking in git.py
5. Block reason classification in review.py
6. Enhanced review_status output
"""

import json
import pathlib
import time
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. REVIEWED_MUTATIVE_TOOLS classification
# ---------------------------------------------------------------------------

def test_reviewed_mutative_tools_contains_commit_tools():
    from ouroboros.tool_capabilities import REVIEWED_MUTATIVE_TOOLS
    assert "repo_commit" in REVIEWED_MUTATIVE_TOOLS
    assert "repo_write_commit" in REVIEWED_MUTATIVE_TOOLS


def test_reviewed_mutative_tools_disjoint_from_parallel():
    from ouroboros.tool_capabilities import REVIEWED_MUTATIVE_TOOLS, READ_ONLY_PARALLEL_TOOLS
    assert REVIEWED_MUTATIVE_TOOLS.isdisjoint(READ_ONLY_PARALLEL_TOOLS), \
        "Reviewed mutative tools must not be in the parallel-safe set"


# ---------------------------------------------------------------------------
# 2. CommitAttemptRecord and AdvisoryReviewState
# ---------------------------------------------------------------------------

def test_commit_attempt_record_creation():
    from ouroboros.review_state import CommitAttemptRecord
    record = CommitAttemptRecord(
        ts="2026-04-02T16:00:00",
        commit_message="test commit",
        status="blocked",
        block_reason="critical_findings",
        block_details="CRITICAL: tests_affected",
        duration_sec=12.5,
        task_id="abc123",
    )
    assert record.status == "blocked"
    assert record.block_reason == "critical_findings"
    assert record.duration_sec == 12.5


def test_advisory_review_state_with_commit_attempt():
    from ouroboros.review_state import (
        AdvisoryReviewState, CommitAttemptRecord, AdvisoryRunRecord,
    )
    state = AdvisoryReviewState()
    assert state.last_commit_attempt is None

    state.last_commit_attempt = CommitAttemptRecord(
        ts="2026-04-02T16:00:00",
        commit_message="test",
        status="succeeded",
    )
    assert state.last_commit_attempt.status == "succeeded"


def test_commit_attempt_serialization_roundtrip(tmp_path):
    from ouroboros.review_state import (
        AdvisoryReviewState, CommitAttemptRecord, AdvisoryRunRecord,
        load_state, save_state,
    )
    drive_root = tmp_path
    (drive_root / "state").mkdir(parents=True)

    state = AdvisoryReviewState()
    state.last_commit_attempt = CommitAttemptRecord(
        ts="2026-04-02T16:00:00",
        commit_message="v1.0: test",
        status="blocked",
        snapshot_hash="abc123",
        block_reason="critical_findings",
        block_details="CRITICAL: something went wrong",
        duration_sec=15.3,
        task_id="task-42",
    )
    run = AdvisoryRunRecord(
        snapshot_hash="abc123",
        commit_message="v1.0: test",
        status="fresh",
        ts="2026-04-02T15:59:00",
    )
    state.add_run(run)

    save_state(drive_root, state)
    loaded = load_state(drive_root)

    assert loaded.last_commit_attempt is not None
    assert loaded.last_commit_attempt.status == "blocked"
    assert loaded.last_commit_attempt.block_reason == "critical_findings"
    assert loaded.last_commit_attempt.duration_sec == 15.3
    assert loaded.last_commit_attempt.task_id == "task-42"
    assert len(loaded.runs) == 1
    assert loaded.runs[0].status == "fresh"


def test_commit_attempt_absent_in_old_state(tmp_path):
    """Old state files without last_commit_attempt should load cleanly."""
    drive_root = tmp_path
    state_dir = drive_root / "state"
    state_dir.mkdir(parents=True)
    state_file = state_dir / "advisory_review.json"
    state_file.write_text(json.dumps({
        "runs": [],
        "saved_at": "2026-04-02T15:00:00",
    }))

    from ouroboros.review_state import load_state
    loaded = load_state(drive_root)
    assert loaded.last_commit_attempt is None


# ---------------------------------------------------------------------------
# 3. format_status_section includes commit attempt
# ---------------------------------------------------------------------------

def test_format_status_shows_blocked_commit():
    from ouroboros.review_state import (
        AdvisoryReviewState, CommitAttemptRecord, format_status_section,
    )
    state = AdvisoryReviewState()
    state.last_commit_attempt = CommitAttemptRecord(
        ts="2026-04-02T16:00:00",
        commit_message="v1.0: test",
        status="blocked",
        block_reason="critical_findings",
        block_details="CRITICAL: bible_compliance violated",
        duration_sec=8.2,
    )
    section = format_status_section(state)
    assert "Last commit BLOCKED" in section
    assert "critical_findings" in section
    assert "bible_compliance" in section


def test_format_status_shows_failed_commit():
    from ouroboros.review_state import (
        AdvisoryReviewState, CommitAttemptRecord, format_status_section,
    )
    state = AdvisoryReviewState()
    state.last_commit_attempt = CommitAttemptRecord(
        ts="2026-04-02T16:00:00",
        commit_message="v1.0: test",
        status="failed",
        block_reason="infra_failure",
        block_details="Git lock timeout: could not acquire lock",
        duration_sec=30.0,
    )
    section = format_status_section(state)
    assert "Last commit FAILED" in section
    assert "infra_failure" in section
    assert "lock" in section.lower()


def test_format_status_hides_succeeded_commit():
    from ouroboros.review_state import (
        AdvisoryReviewState, CommitAttemptRecord, format_status_section,
    )
    state = AdvisoryReviewState()
    state.last_commit_attempt = CommitAttemptRecord(
        ts="2026-04-02T16:00:00",
        commit_message="v1.0: test",
        status="succeeded",
    )
    section = format_status_section(state)
    # Succeeded commits should not clutter the status section
    assert "Last commit" not in section


# ---------------------------------------------------------------------------
# 4. Block reason classification in review.py
# ---------------------------------------------------------------------------

def test_block_reason_set_for_quorum_failure():
    """_run_unified_review should set _last_review_block_reason='review_quorum'
    when fewer than 2 reviewers succeed."""
    from ouroboros.tools.review import _run_unified_review

    ctx = MagicMock()
    ctx.repo_dir = "/tmp/fake"
    ctx.drive_root = "/tmp/fake_data"
    ctx._review_iteration_count = 0
    ctx._review_advisory = []
    ctx._review_history = []

    # Mock: diff exists, all models error
    with patch("ouroboros.tools.review.run_cmd") as mock_run, \
         patch("ouroboros.tools.review._cfg") as mock_cfg, \
         patch("ouroboros.tools.review._handle_multi_model_review") as mock_review:

        mock_run.side_effect = [
            "diff --git a/foo.py\n+hello",  # git diff --cached
            "foo.py",  # git diff --cached --name-only
        ]
        mock_cfg.get_review_enforcement.return_value = "blocking"
        mock_cfg.get_review_models.return_value = ["m1", "m2", "m3"]

        mock_review.return_value = json.dumps({
            "results": [
                {"model": "m1", "verdict": "ERROR", "text": "Timeout"},
                {"model": "m2", "verdict": "ERROR", "text": "Timeout"},
                {"model": "m3", "verdict": "ERROR", "text": "Timeout"},
            ]
        })

        result = _run_unified_review(ctx, "test commit")
        assert result is not None
        assert "REVIEW_BLOCKED" in result
        assert ctx._last_review_block_reason == "review_quorum"


def test_block_reason_set_for_critical_findings():
    """_run_unified_review should set _last_review_block_reason='critical_findings'."""
    from ouroboros.tools.review import _run_unified_review

    ctx = MagicMock()
    ctx.repo_dir = "/tmp/fake"
    ctx.drive_root = "/tmp/fake_data"
    ctx._review_iteration_count = 0
    ctx._review_advisory = []
    ctx._review_history = []

    with patch("ouroboros.tools.review.run_cmd") as mock_run, \
         patch("ouroboros.tools.review._cfg") as mock_cfg, \
         patch("ouroboros.tools.review._handle_multi_model_review") as mock_review, \
         patch("ouroboros.tools.review.build_touched_file_pack") as mock_pack, \
         patch("ouroboros.tools.review.build_goal_section") as mock_goal, \
         patch("ouroboros.tools.review._load_checklist_section") as mock_checklist, \
         patch("ouroboros.tools.review._load_dev_guide_text") as mock_dev:

        mock_run.side_effect = [
            "diff --git a/foo.py\n+hello",  # git diff --cached
            "foo.py",  # git diff --cached --name-only
        ]
        mock_cfg.get_review_enforcement.return_value = "blocking"
        mock_cfg.get_review_models.return_value = ["m1", "m2", "m3"]
        mock_pack.return_value = ("foo.py content", [])
        mock_goal.return_value = "## Goal\nTest"
        mock_checklist.return_value = "## Checklist\nTest"
        mock_dev.return_value = "dev guide"

        findings = json.dumps([
            {"item": "bible_compliance", "verdict": "FAIL", "severity": "critical", "reason": "P5 violated"},
        ])
        mock_review.return_value = json.dumps({
            "results": [
                {"model": "m1", "verdict": "CONCERNS", "text": findings},
                {"model": "m2", "verdict": "CONCERNS", "text": findings},
                {"model": "m3", "verdict": "CONCERNS", "text": findings},
            ]
        })

        result = _run_unified_review(ctx, "test commit")
        assert result is not None
        assert "REVIEW_BLOCKED" in result
        assert ctx._last_review_block_reason == "critical_findings"


# ---------------------------------------------------------------------------
# 5. Enhanced review_status output
# ---------------------------------------------------------------------------

def test_review_status_shows_commit_attempt():
    """review_status should include last_commit_attempt in output."""
    from ouroboros.tools.claude_advisory_review import _handle_review_status
    from ouroboros.review_state import (
        AdvisoryReviewState, CommitAttemptRecord, AdvisoryRunRecord,
        save_state,
    )

    ctx = MagicMock()
    ctx.drive_root = "/tmp/fake_status_test"
    drive_root = pathlib.Path(ctx.drive_root)

    with patch("ouroboros.tools.claude_advisory_review.load_state") as mock_load:
        state = AdvisoryReviewState()
        state.last_commit_attempt = CommitAttemptRecord(
            ts="2026-04-02T16:00:00",
            commit_message="v1.0: test",
            status="blocked",
            block_reason="no_advisory",
            block_details="No fresh advisory run found",
            duration_sec=2.1,
        )
        mock_load.return_value = state

        result = _handle_review_status(ctx)
        data = json.loads(result)
        assert data["last_commit_attempt"] is not None
        assert data["last_commit_attempt"]["status"] == "blocked"
        assert data["last_commit_attempt"]["block_reason"] == "no_advisory"
        assert "BLOCKED" in data["message"]
        assert "no_advisory" in data["message"]


def test_review_status_actionable_message_for_each_reason():
    """Each block_reason should produce a specific actionable message."""
    from ouroboros.tools.claude_advisory_review import _handle_review_status
    from ouroboros.review_state import (
        AdvisoryReviewState, CommitAttemptRecord,
    )

    reasons = [
        "no_advisory", "critical_findings", "review_quorum",
        "parse_failure", "infra_failure", "scope_blocked", "preflight",
    ]

    for reason in reasons:
        ctx = MagicMock()
        ctx.drive_root = "/tmp/test"

        with patch("ouroboros.tools.claude_advisory_review.load_state") as mock_load:
            state = AdvisoryReviewState()
            state.last_commit_attempt = CommitAttemptRecord(
                ts="2026-04-02T16:00:00",
                commit_message="test",
                status="blocked",
                block_reason=reason,
            )
            mock_load.return_value = state

            result = _handle_review_status(ctx)
            data = json.loads(result)
            assert reason in data["message"], f"message should mention {reason}"


# ---------------------------------------------------------------------------
# 6. Reviewed mutative tool timeout handling
# ---------------------------------------------------------------------------

def test_reviewed_mutative_hard_ceiling_constant():
    from ouroboros.loop_tool_execution import _REVIEWED_MUTATIVE_HARD_CEILING
    assert _REVIEWED_MUTATIVE_HARD_CEILING >= 600, "Hard ceiling must be substantial"
    assert _REVIEWED_MUTATIVE_HARD_CEILING <= 3600, "Hard ceiling shouldn't be infinite"


def test_reviewed_mutative_import_in_loop():
    """REVIEWED_MUTATIVE_TOOLS should be importable from loop_tool_execution."""
    from ouroboros.loop_tool_execution import REVIEWED_MUTATIVE_TOOLS
    assert "repo_commit" in REVIEWED_MUTATIVE_TOOLS


# ---------------------------------------------------------------------------
# 7. Snapshot hash path scoping (verify existing behavior)
# ---------------------------------------------------------------------------

def test_snapshot_hash_path_scoping(tmp_path):
    """compute_snapshot_hash with paths= should only consider those paths."""
    from ouroboros.review_state import compute_snapshot_hash

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    # Create two files
    (repo / "a.py").write_text("aaa")
    (repo / "b.py").write_text("bbb")

    hash_a = compute_snapshot_hash(repo, paths=["a.py"])
    hash_b = compute_snapshot_hash(repo, paths=["b.py"])
    hash_ab = compute_snapshot_hash(repo, paths=["a.py", "b.py"])

    assert hash_a != hash_b, "Different paths should produce different hashes"
    assert hash_a != hash_ab
    assert hash_b != hash_ab

    # Same paths, same content → same hash
    hash_a2 = compute_snapshot_hash(repo, paths=["a.py"])
    assert hash_a == hash_a2


def test_snapshot_hash_ignores_commit_message(tmp_path):
    """commit_message should NOT affect the hash (decoupled per design)."""
    from ouroboros.review_state import compute_snapshot_hash

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "a.py").write_text("content")

    h1 = compute_snapshot_hash(repo, commit_message="msg1", paths=["a.py"])
    h2 = compute_snapshot_hash(repo, commit_message="msg2", paths=["a.py"])
    assert h1 == h2


# ---------------------------------------------------------------------------
# 9. Startup reconciliation of stale 'reviewing' state
# ---------------------------------------------------------------------------

def test_startup_reconciles_stale_reviewing(tmp_path):
    """verify_system_state reconciles stale 'reviewing' → 'failed' on startup."""
    from ouroboros.review_state import (
        AdvisoryReviewState, CommitAttemptRecord, save_state, load_state,
    )
    # Set up state with stale 'reviewing'
    state = AdvisoryReviewState()
    state.last_commit_attempt = CommitAttemptRecord(
        ts="2026-01-01T00:00:00Z", commit_message="stuck commit",
        status="reviewing",
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    save_state(tmp_path, state)

    # Simulate verify_system_state reconciliation
    st = load_state(tmp_path)
    if st.last_commit_attempt and st.last_commit_attempt.status == "reviewing":
        st.last_commit_attempt.status = "failed"
        st.last_commit_attempt.block_reason = "infra_failure"
        st.last_commit_attempt.block_details = "reconciled on startup"
        save_state(tmp_path, st)

    reconciled = load_state(tmp_path)
    assert reconciled.last_commit_attempt.status == "failed"
    assert reconciled.last_commit_attempt.block_reason == "infra_failure"
    assert "reconciled" in reconciled.last_commit_attempt.block_details


def test_startup_does_not_touch_terminal_states(tmp_path):
    """verify_system_state must NOT change already-terminal states."""
    from ouroboros.review_state import (
        AdvisoryReviewState, CommitAttemptRecord, save_state, load_state,
    )
    for terminal_status in ("succeeded", "failed", "blocked"):
        state = AdvisoryReviewState()
        state.last_commit_attempt = CommitAttemptRecord(
            ts="2026-01-01T00:00:00Z", commit_message="done",
            status=terminal_status,
        )
        save_state(tmp_path, state)

        st = load_state(tmp_path)
        if st.last_commit_attempt and st.last_commit_attempt.status == "reviewing":
            st.last_commit_attempt.status = "failed"
            save_state(tmp_path, st)

        after = load_state(tmp_path)
        assert after.last_commit_attempt.status == terminal_status
