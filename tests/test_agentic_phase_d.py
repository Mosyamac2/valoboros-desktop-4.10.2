"""Phase 4 tests — agentic Phase D (report prettification).

Verifies the A→B→C→D chain produces a polished report.md, and that Phase D
in isolation can be invoked after Phase C's artifacts already exist.
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
# Scripted-SDK pattern (same as earlier phases)
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

def _seed_phase_c_outputs(bundle: Path) -> None:
    """Pre-stage results.json + interpretation.md as if Phase C had run."""
    (bundle / "results").mkdir(parents=True, exist_ok=True)
    (bundle / "results" / "results.json").write_text(
        json.dumps({
            "schema_version": "1",
            "bundle_id": str(bundle.name),
            "tests": [
                {"id": "quant1", "name": "OOS AUC", "block": "quantitative",
                 "verdict": "pass", "metric": {"AUC": 0.82},
                 "evidence": "holdout n=500", "error": None},
            ],
            "summary": {"n_pass": 1, "n_warn": 0, "n_fail": 0,
                        "n_deferred": 0, "n_error": 0},
        }, indent=2),
        encoding="utf-8",
    )
    (bundle / "results" / "interpretation.md").write_text(
        "# Interpretation\n\n## Verdict\nApproved.\n",
        encoding="utf-8",
    )
    (bundle / "methodology").mkdir(parents=True, exist_ok=True)
    (bundle / "methodology" / "methodology.md").write_text(
        "# Methodology\n", encoding="utf-8"
    )


def _phase_d_writes_report(bundle: Path) -> None:
    (bundle / "results").mkdir(parents=True, exist_ok=True)
    (bundle / "results" / "report.md").write_text(
        "# Validation report — bundle-d\n\n"
        "## Overall verdict\n1/1 tests passed. Approved.\n\n"
        "## Hard findings\nNone.\n\n"
        "## Quantitative results table\n"
        "| Test ID | Name | Verdict | Metric | Pass threshold |\n"
        "|---------|------|---------|--------|----------------|\n"
        "| quant1 | OOS AUC | pass | AUC=0.82 | AUC ≥ 0.75 |\n",
        encoding="utf-8",
    )


def _phase_d_writes_nothing(bundle: Path) -> None:
    return None


def _phase_a_writes_methodology(bundle: Path) -> None:
    (bundle / "methodology").mkdir(parents=True, exist_ok=True)
    (bundle / "methodology" / "methodology.md").write_text("# Methodology\n",
                                                          encoding="utf-8")


def _phase_b_writes_project(bundle: Path) -> None:
    proj = bundle / "methodology" / "validation_project"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "run_all.py").write_text("# runner\n", encoding="utf-8")
    (proj / "requirements.txt").write_text("# none\n", encoding="utf-8")


def _phase_c_writes_results_and_interpretation(bundle: Path) -> None:
    _seed_phase_c_outputs(bundle)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_phase_d_chains_through_abcd_and_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full A→B→C→D chain produces report.md as the final deliverable."""
    bundle_dir = tmp_path / "bundle-d"
    bundle_dir.mkdir()
    (bundle_dir / "raw").mkdir()

    _ScriptedSDKClient._bundle_dir = bundle_dir
    _ScriptedSDKClient._queue = [
        ([_FakeAssistantMessage(content=[_FakeTextBlock(text="A")]),
          _FakeResultMessage(session_id="A", total_cost_usd=0.05)],
         _phase_a_writes_methodology),
        ([_FakeAssistantMessage(content=[_FakeTextBlock(text="B")]),
          _FakeResultMessage(session_id="B", total_cost_usd=0.20)],
         _phase_b_writes_project),
        ([_FakeAssistantMessage(content=[_FakeTextBlock(text="C")]),
          _FakeResultMessage(session_id="C", total_cost_usd=0.15)],
         _phase_c_writes_results_and_interpretation),
        ([_FakeAssistantMessage(content=[_FakeTextBlock(text="D")]),
          _FakeResultMessage(session_id="D", total_cost_usd=0.05)],
         _phase_d_writes_report),
    ]
    _install_fake_sdk(monkeypatch)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    validator = AgenticValidator(
        bundle_id="bundle-d",
        bundle_dir=bundle_dir,
        model_type="classification",
        knowledge_dir=tmp_path / "knowledge",
        repo_dir=repo_dir,
    )

    result = _run_async(validator.run())

    assert result.success is True, f"chained run failed: {result.error}"
    assert [p.phase for p in result.phases] == ["A", "B", "C", "D"]
    pd = result.phases[-1]
    assert pd.success
    assert pd.files_written == ["results/report.md"]
    assert (bundle_dir / "results" / "report.md").exists()
    # Per-phase transcripts all persisted
    for letter in ("a", "b", "c", "d"):
        assert (bundle_dir / "_agentic_transcripts" / f"phase_{letter}.jsonl").exists()
    # Aggregate captures all four phases
    aggregate = json.loads(
        (bundle_dir / "_agentic_transcripts" / "result.json").read_text(encoding="utf-8")
    )
    assert [p["phase"] for p in aggregate["phases"]] == ["A", "B", "C", "D"]
    assert aggregate["total_cost_usd"] == pytest.approx(0.45)


def test_phase_d_in_isolation_fails_without_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase D called directly with Phase C's outputs already on disk but
    the fake SDK fails to write report.md — the expected-output gate must
    flip success to False."""
    bundle_dir = tmp_path / "bundle-d-only"
    bundle_dir.mkdir()
    _seed_phase_c_outputs(bundle_dir)

    _ScriptedSDKClient._bundle_dir = bundle_dir
    _ScriptedSDKClient._queue = [
        ([_FakeAssistantMessage(content=[_FakeTextBlock(text="I forgot to write the report")]),
          _FakeResultMessage(session_id="D", total_cost_usd=0.01)],
         _phase_d_writes_nothing),
    ]
    _install_fake_sdk(monkeypatch)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    validator = AgenticValidator(
        bundle_id="bundle-d-only",
        bundle_dir=bundle_dir,
        model_type="classification",
        knowledge_dir=tmp_path / "knowledge",
        repo_dir=repo_dir,
    )

    pd = _run_async(validator.run_phase_d())
    assert pd.phase == "D"
    assert pd.success is False
    assert "report.md" in pd.error
    assert pd.files_written == []
    assert not (bundle_dir / "results" / "report.md").exists()
