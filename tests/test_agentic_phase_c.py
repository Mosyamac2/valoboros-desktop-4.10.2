"""Phase 3 tests — agentic Phase C (execution + interpretation).

Same scripted-SDK pattern as Phase B's tests. Phase C is verified by
checking that the runner correctly hands control to Claude Code for the
A→B→C chain and that the expected-output gate (results.json +
interpretation.md) flips success correctly.

We do NOT actually run a real validation_project here — the fake SDK
stages a results.json that mimics what Phase B's authored runner would
produce. End-to-end execution is tested by the live demo (sub-phase 11).
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
# Reuse the scripted SDK pattern
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


# ---------------------------------------------------------------------------
# Filesystem effects
# ---------------------------------------------------------------------------

def _phase_a_writes_methodology(bundle: Path) -> None:
    target = bundle / "methodology" / "methodology.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Methodology\n", encoding="utf-8")


def _phase_b_writes_project(bundle: Path) -> None:
    proj = bundle / "methodology" / "validation_project"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "run_all.py").write_text("# runner\n", encoding="utf-8")
    (proj / "requirements.txt").write_text("# none\n", encoding="utf-8")


_SAMPLE_RESULTS_JSON = {
    "schema_version": "1",
    "bundle_id": "bundle-abc",
    "tests": [
        {"id": "q1", "name": "target column", "block": "qualitative",
         "verdict": "pass", "metric": None, "evidence": "single target", "error": None},
        {"id": "quant1", "name": "OOS AUC", "block": "quantitative",
         "verdict": "warn", "metric": {"AUC": 0.71}, "evidence": "below 0.75",
         "error": None},
    ],
    "summary": {"n_pass": 1, "n_warn": 1, "n_fail": 0, "n_deferred": 0, "n_error": 0},
}


def _phase_c_writes_results_and_interpretation(bundle: Path) -> None:
    (bundle / "results").mkdir(parents=True, exist_ok=True)
    (bundle / "results" / "results.json").write_text(
        json.dumps(_SAMPLE_RESULTS_JSON, indent=2), encoding="utf-8"
    )
    (bundle / "results" / "interpretation.md").write_text(
        "# Interpretation\n\n## Verdict\nConditional pass. OOS AUC borderline.\n",
        encoding="utf-8",
    )


def _phase_c_writes_only_results(bundle: Path) -> None:
    """Simulates Claude writing results.json but forgetting interpretation.md."""
    (bundle / "results").mkdir(parents=True, exist_ok=True)
    (bundle / "results" / "results.json").write_text(
        json.dumps(_SAMPLE_RESULTS_JSON, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_phase_c_chains_after_phase_b_and_persists_both_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The A→B→C chain runs cleanly and lands results.json +
    interpretation.md. Aggregate cost reflects all three phases."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "raw").mkdir()

    _ScriptedSDKClient._bundle_dir = bundle_dir
    _ScriptedSDKClient._queue = [
        (
            [_FakeAssistantMessage(content=[_FakeTextBlock(text="A")]),
             _FakeResultMessage(session_id="A", total_cost_usd=0.1)],
            _phase_a_writes_methodology,
        ),
        (
            [_FakeAssistantMessage(content=[_FakeTextBlock(text="B")]),
             _FakeResultMessage(session_id="B", total_cost_usd=0.4)],
            _phase_b_writes_project,
        ),
        (
            [_FakeAssistantMessage(content=[_FakeTextBlock(text="C")]),
             _FakeAssistantMessage(content=[_FakeTextBlock(text="EXIT=0")]),
             _FakeResultMessage(session_id="C", total_cost_usd=0.3)],
            _phase_c_writes_results_and_interpretation,
        ),
    ]
    _install_fake_sdk(monkeypatch)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    validator = AgenticValidator(
        bundle_id="bundle-abc",
        bundle_dir=bundle_dir,
        model_type="classification",
        knowledge_dir=tmp_path / "knowledge",
        repo_dir=repo_dir,
    )

    result = _run_async(validator.run())

    assert result.success is True, f"run reported failure: {result.error}"
    assert [p.phase for p in result.phases] == ["A", "B", "C"]
    pa, pb, pc = result.phases
    assert pa.success and pb.success and pc.success
    assert result.total_cost_usd == pytest.approx(0.8)

    # Phase C outputs land on disk
    assert (bundle_dir / "results" / "results.json").exists()
    assert (bundle_dir / "results" / "interpretation.md").exists()
    # Results JSON is the schema the parser will eventually consume
    blob = json.loads(
        (bundle_dir / "results" / "results.json").read_text(encoding="utf-8")
    )
    assert blob["schema_version"] == "1"
    assert blob["summary"]["n_warn"] == 1

    # Aggregate persisted with all three phases
    aggregate = json.loads(
        (bundle_dir / "_agentic_transcripts" / "result.json").read_text(encoding="utf-8")
    )
    assert [p["phase"] for p in aggregate["phases"]] == ["A", "B", "C"]
    assert aggregate["total_cost_usd"] == pytest.approx(0.8)


def test_phase_c_fails_if_interpretation_md_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If Claude writes results.json but skips interpretation.md, the phase
    must fail loudly — Phase C is interpretation, not just execution."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "raw").mkdir()

    _ScriptedSDKClient._bundle_dir = bundle_dir
    _ScriptedSDKClient._queue = [
        ([_FakeAssistantMessage(content=[_FakeTextBlock(text="A")]),
          _FakeResultMessage(session_id="A", total_cost_usd=0.05)],
         _phase_a_writes_methodology),
        ([_FakeAssistantMessage(content=[_FakeTextBlock(text="B")]),
          _FakeResultMessage(session_id="B", total_cost_usd=0.1)],
         _phase_b_writes_project),
        ([_FakeAssistantMessage(content=[_FakeTextBlock(text="C ran but forgot interp")]),
          _FakeResultMessage(session_id="C", total_cost_usd=0.2)],
         _phase_c_writes_only_results),
    ]
    _install_fake_sdk(monkeypatch)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    validator = AgenticValidator(
        bundle_id="bundle-missing-interp",
        bundle_dir=bundle_dir,
        model_type="regression",
        knowledge_dir=tmp_path / "knowledge",
        repo_dir=repo_dir,
    )

    result = _run_async(validator.run())
    assert [p.phase for p in result.phases] == ["A", "B", "C"]
    pc = result.phases[-1]
    assert pc.success is False
    assert "interpretation.md" in pc.error
    # results.json should still have been written by the fake
    assert (bundle_dir / "results" / "results.json").exists()
    assert not (bundle_dir / "results" / "interpretation.md").exists()
    assert result.success is False
    assert "phase_c" in result.error
