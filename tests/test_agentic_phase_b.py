"""Phase 2 tests — agentic Phase B (project authoring).

These tests reuse the fake SDK pattern from
``test_agentic_runner_skeleton.py``. The fake SDK stages a methodology
artifact (from Phase A) plus a fake validation_project skeleton (from
Phase B), so we exercise the A→B chain through the runner without burning
real subscription budget.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ouroboros.validation import agentic_runner
from ouroboros.validation.agentic_runner import AgenticValidator


# ---------------------------------------------------------------------------
# Reuse the fake SDK pattern. We extend it with a queue of post-query
# filesystem effects so different phases can stage different artifacts.
# ---------------------------------------------------------------------------

@dataclass
class _FakeAssistantMessage:
    content: list[Any] = field(default_factory=list)


@dataclass
class _FakeTextBlock:
    text: str


@dataclass
class _FakeResultMessage:
    session_id: str = "test-session"
    total_cost_usd: float = 0.0
    subtype: str = "success"
    usage: dict[str, int] = field(default_factory=dict)


class _ScriptedSDKClient:
    """Each ``ClaudeSDKClient(...)`` invocation pops the next scripted
    response from the class-level queue. A scripted response is a tuple
    ``(messages, fs_effect)`` — ``fs_effect`` is a callable that gets the
    bundle_dir as its only arg and applies any filesystem changes that a
    real Claude Code session would have made via Write/Bash."""

    _queue: list[tuple[list[Any], Any]] = []
    _bundle_dir: Path | None = None

    def __init__(self, options: Any) -> None:
        if not _ScriptedSDKClient._queue:
            raise AssertionError("Test bug: SDK invoked without scripted response")
        self._messages, self._fs_effect = _ScriptedSDKClient._queue.pop(0)

    async def __aenter__(self) -> "_ScriptedSDKClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def query(self, prompt: str) -> None:
        if self._fs_effect is not None and _ScriptedSDKClient._bundle_dir is not None:
            self._fs_effect(_ScriptedSDKClient._bundle_dir)

    async def receive_response(self):
        for msg in self._messages:
            yield msg


class _FakeHookMatcher:
    def __init__(self, matcher: str, hooks: list[Any]) -> None:
        self.matcher = matcher
        self.hooks = hooks


class _FakeOptions:
    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agentic_runner,
        "_try_import_sdk",
        lambda: {
            "AssistantMessage": _FakeAssistantMessage,
            "ClaudeAgentOptions": _FakeOptions,
            "ClaudeSDKClient": _ScriptedSDKClient,
            "HookMatcher": _FakeHookMatcher,
            "ResultMessage": _FakeResultMessage,
        },
    )


def _run_async(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


# Filesystem effects (what a real Claude Code session would do)

def _phase_a_writes_methodology(bundle: Path) -> None:
    target = bundle / "methodology" / "methodology.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Methodology\n\n"
        "## Block 1: Qualitative analysis\n### q1 — target column\n"
        "## Block 2: Quantitative analysis\n### quant1 — OOS AUC\n",
        encoding="utf-8",
    )


def _phase_b_writes_project(bundle: Path) -> None:
    proj = bundle / "methodology" / "validation_project"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "run_all.py").write_text(
        "if __name__ == '__main__':\n    print('{\"tests\":[]}')\n",
        encoding="utf-8",
    )
    (proj / "requirements.txt").write_text("# none\n", encoding="utf-8")
    (proj / "README.md").write_text("# Validation project\n", encoding="utf-8")
    for sub in ("qualitative", "quantitative", "common"):
        (proj / sub).mkdir(exist_ok=True)
        (proj / sub / "__init__.py").write_text("", encoding="utf-8")


def _phase_b_writes_nothing(bundle: Path) -> None:
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_phase_b_chains_after_phase_a_and_validates_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running ``AgenticValidator.run()`` invokes Phase A then Phase B.
    Both phases land artifacts on disk and the aggregate reports both
    as successful."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "raw").mkdir()
    (bundle_dir / "raw" / "model.py").write_text("# fake model", encoding="utf-8")

    _ScriptedSDKClient._bundle_dir = bundle_dir
    _ScriptedSDKClient._queue = [
        (
            [
                _FakeAssistantMessage(content=[_FakeTextBlock(text="Designing methodology...")]),
                _FakeResultMessage(session_id="sid-a", total_cost_usd=0.30),
            ],
            _phase_a_writes_methodology,
        ),
        (
            [
                _FakeAssistantMessage(content=[_FakeTextBlock(text="Authoring project...")]),
                _FakeAssistantMessage(content=[_FakeTextBlock(text="IMPORT_OK")]),
                _FakeResultMessage(session_id="sid-b", total_cost_usd=0.60),
            ],
            _phase_b_writes_project,
        ),
    ]
    _install_fake_sdk(monkeypatch)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    validator = AgenticValidator(
        bundle_id="bundle-ab",
        bundle_dir=bundle_dir,
        model_type="classification",
        knowledge_dir=tmp_path / "knowledge",
        repo_dir=repo_dir,
    )

    result = _run_async(validator.run())

    assert result.success is True, f"run reported failure: {result.error}"
    assert [p.phase for p in result.phases] == ["A", "B"]
    pa, pb = result.phases
    assert pa.success and pb.success
    assert pa.cost_usd == pytest.approx(0.30)
    assert pb.cost_usd == pytest.approx(0.60)
    assert result.total_cost_usd == pytest.approx(0.90)
    # Phase B files persisted
    assert (bundle_dir / "methodology" / "validation_project" / "run_all.py").exists()
    assert (bundle_dir / "methodology" / "validation_project" / "requirements.txt").exists()
    # Expected-output reporting
    assert "methodology/validation_project/run_all.py" in pb.files_written
    assert "methodology/validation_project/requirements.txt" in pb.files_written
    # Aggregate persisted with both phases
    blob = json.loads(
        (bundle_dir / "_agentic_transcripts" / "result.json").read_text(encoding="utf-8")
    )
    assert len(blob["phases"]) == 2
    assert blob["phases"][1]["phase"] == "B"
    # Per-phase transcripts persisted
    assert (bundle_dir / "_agentic_transcripts" / "phase_a.jsonl").exists()
    assert (bundle_dir / "_agentic_transcripts" / "phase_b.jsonl").exists()


def test_phase_b_failure_does_not_corrupt_phase_a_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If Phase B finishes without writing run_all.py, expected-output check
    flips its success to False, but Phase A's artifact is intact and the
    aggregate reports the failure clearly."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "raw").mkdir()

    _ScriptedSDKClient._bundle_dir = bundle_dir
    _ScriptedSDKClient._queue = [
        (
            [
                _FakeAssistantMessage(content=[_FakeTextBlock(text="Phase A ok.")]),
                _FakeResultMessage(session_id="sid-a", total_cost_usd=0.1),
            ],
            _phase_a_writes_methodology,
        ),
        (
            [
                _FakeAssistantMessage(content=[_FakeTextBlock(text="Hmm, can't figure out the project.")]),
                _FakeResultMessage(session_id="sid-b", total_cost_usd=0.05),
            ],
            _phase_b_writes_nothing,
        ),
    ]
    _install_fake_sdk(monkeypatch)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    validator = AgenticValidator(
        bundle_id="bundle-b-fail",
        bundle_dir=bundle_dir,
        model_type="regression",
        knowledge_dir=tmp_path / "knowledge",
        repo_dir=repo_dir,
    )

    result = _run_async(validator.run())

    assert (bundle_dir / "methodology" / "methodology.md").exists()
    assert not (bundle_dir / "methodology" / "validation_project" / "run_all.py").exists()
    assert [p.phase for p in result.phases] == ["A", "B"]
    pa, pb = result.phases
    assert pa.success is True
    assert pb.success is False
    assert "run_all.py" in pb.error or "requirements.txt" in pb.error
    # Aggregate-level success should reflect the last phase's outcome
    assert result.success is False
    assert "phase_b" in result.error
