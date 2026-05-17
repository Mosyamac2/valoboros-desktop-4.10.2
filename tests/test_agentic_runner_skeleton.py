"""Phase 1 tests — AgenticValidator skeleton.

These tests do NOT call the real Claude Agent SDK. They patch the SDK
import shim with fakes so we exercise the orchestration logic
(transcript writing, expected-output checks, aggregate persistence)
without burning subscription budget.
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


def _run_async(coro: Any) -> Any:
    """Run ``coro`` on a fresh event loop and leave the default loop
    pointer restored. Plain ``asyncio.run()`` clears the default loop in
    Python 3.12, which breaks legacy tests that call
    ``asyncio.get_event_loop()`` later in the same pytest session
    (e.g. ``tests/test_claude_code_gateway.py``)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


# ---------------------------------------------------------------------------
# Fake SDK that emulates ClaudeSDKClient just enough for the runner
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


class _FakeClaudeSDKClient:
    """Yields a fixed message sequence and (optionally) writes the expected
    methodology.md so the post-run output check passes."""

    _next_messages: list[Any] = []
    _next_methodology_write: str | None = None
    _bundle_dir: Path | None = None

    def __init__(self, options: Any) -> None:
        self._options = options
        self._queued: list[str] = []

    async def __aenter__(self) -> "_FakeClaudeSDKClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self._queued.append(prompt)
        # If the test asked for a methodology to be written, do it now —
        # simulating what the real Claude Code session would do via its
        # Write tool inside cwd.
        if (
            _FakeClaudeSDKClient._next_methodology_write is not None
            and _FakeClaudeSDKClient._bundle_dir is not None
        ):
            target = _FakeClaudeSDKClient._bundle_dir / "methodology" / "methodology.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                _FakeClaudeSDKClient._next_methodology_write, encoding="utf-8"
            )

    async def receive_response(self):
        for msg in _FakeClaudeSDKClient._next_messages:
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
            "ClaudeSDKClient": _FakeClaudeSDKClient,
            "HookMatcher": _FakeHookMatcher,
            "ResultMessage": _FakeResultMessage,
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_phase_a_persists_transcript_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful Phase A run must:
       - persist phase_a.jsonl transcript
       - persist result.json aggregate
       - report success and the methodology.md file written
    """
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "raw").mkdir()
    (bundle_dir / "raw" / "model.py").write_text("print('hi')", encoding="utf-8")

    # Stage fake conversation: Claude emits two assistant messages then a success Result.
    _FakeClaudeSDKClient._next_messages = [
        _FakeAssistantMessage(content=[_FakeTextBlock(text="Reading bundle...")]),
        _FakeAssistantMessage(
            content=[_FakeTextBlock(text="Methodology written to ./methodology/methodology.md")]
        ),
        _FakeResultMessage(session_id="sid-123", total_cost_usd=0.42, subtype="success"),
    ]
    _FakeClaudeSDKClient._next_methodology_write = (
        "# Methodology\n## Block 1: Qualitative\n### q1 — target column\n"
        "## Block 2: Quantitative\n### quant1 — AUC on holdout"
    )
    _FakeClaudeSDKClient._bundle_dir = bundle_dir
    _install_fake_sdk(monkeypatch)

    # Use isolated knowledge_dir (empty) and repo_dir (empty) so the test is hermetic.
    knowledge_dir = tmp_path / "knowledge"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    validator = AgenticValidator(
        bundle_id="bundle-test",
        bundle_dir=bundle_dir,
        model_type="classification",
        knowledge_dir=knowledge_dir,
        repo_dir=repo_dir,
    )

    # Phase 1 covers Phase A in isolation; chained-run behavior is
    # exercised by tests/test_agentic_phase_b.py.
    pa = _run_async(validator.run_phase_a())

    assert pa.phase == "A"
    assert pa.success, f"Phase A reported failure: {pa.error}"
    assert pa.session_id == "sid-123"
    assert pa.cost_usd == pytest.approx(0.42)
    assert pa.turns == 2
    assert pa.files_written == ["methodology/methodology.md"]

    transcript_path = Path(pa.transcript_path)
    assert transcript_path.exists()
    lines = transcript_path.read_text(encoding="utf-8").splitlines()
    # 2 assistant + 1 result message recorded
    assert len(lines) == 3
    assert all("kind" in json.loads(line) for line in lines)


def test_phase_a_fails_when_no_methodology_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the fake SDK never writes methodology.md, Phase A's
    expected-output check must flip success to False with an explanatory error."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "raw").mkdir()

    _FakeClaudeSDKClient._next_messages = [
        _FakeAssistantMessage(content=[_FakeTextBlock(text="Looking around...")]),
        _FakeResultMessage(session_id="sid-x", total_cost_usd=0.01, subtype="success"),
    ]
    _FakeClaudeSDKClient._next_methodology_write = None  # never write the file
    _FakeClaudeSDKClient._bundle_dir = bundle_dir
    _install_fake_sdk(monkeypatch)

    knowledge_dir = tmp_path / "knowledge"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    validator = AgenticValidator(
        bundle_id="bundle-empty",
        bundle_dir=bundle_dir,
        model_type="regression",
        knowledge_dir=knowledge_dir,
        repo_dir=repo_dir,
    )

    pa = _run_async(validator.run_phase_a())
    assert pa.success is False
    assert "methodology.md" in pa.error
    assert pa.files_written == []
