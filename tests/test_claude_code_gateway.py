"""Tests for Claude Code gateway safety guards and orchestration helpers.

The gateway module (ouroboros/gateways/claude_code.py) raises ImportError
at import time when claude-agent-sdk is not installed (the correct behavior
for triggering CLI fallback in callers). We therefore test:
  - ClaudeCodeResult (importable from gateway even w/o SDK via careful mocking)
  - Path guard and readonly guard hooks (function-level, no SDK dependency)
  - Orchestration helpers (_load_project_context etc.) now in shell.py
"""

import asyncio
import json
import pathlib
import subprocess
import sys
import types
import pytest


# ---------------------------------------------------------------------------
# Mock SDK so the gateway can be imported on Python 3.9 / without SDK
# ---------------------------------------------------------------------------

def _ensure_gateway_importable():
    """Install a lightweight mock of claude_agent_sdk if the real one is absent."""
    if "claude_agent_sdk" not in sys.modules:
        mock_sdk = types.ModuleType("claude_agent_sdk")
        # Provide the names the gateway expects at import time
        mock_sdk.ClaudeAgentOptions = type("ClaudeAgentOptions", (), {})
        mock_sdk.ClaudeSDKClient = type("ClaudeSDKClient", (), {})
        mock_sdk.HookMatcher = type("HookMatcher", (), {"__init__": lambda self, **kw: None})
        mock_sdk.AssistantMessage = type("AssistantMessage", (), {})
        mock_sdk.ResultMessage = type("ResultMessage", (), {})
        mock_sdk.query = lambda **kw: None  # async generator mock
        sys.modules["claude_agent_sdk"] = mock_sdk


_ensure_gateway_importable()

from ouroboros.gateways.claude_code import (  # noqa: E402
    ClaudeCodeResult,
    make_path_guard,
    make_readonly_guard,
    SAFETY_CRITICAL,
)

# Orchestration helpers now live in shell.py
from ouroboros.tools.shell import (  # noqa: E402
    _load_project_context,
    _get_changed_files,
    _get_diff_stat,
    _run_validation,
)


# ---------------------------------------------------------------------------
# ClaudeCodeResult
# ---------------------------------------------------------------------------

class TestClaudeCodeResult:
    def test_success_to_json(self):
        r = ClaudeCodeResult(
            success=True,
            result_text="Edited 2 files",
            session_id="abc-123",
            cost_usd=0.05,
            changed_files=["foo.py", "bar.py"],
            diff_stat="2 files changed, 10 insertions",
        )
        out = json.loads(r.to_tool_output())
        assert out["success"] is True
        assert out["result"] == "Edited 2 files"
        assert out["session_id"] == "abc-123"
        assert out["cost_usd"] == 0.05
        assert out["changed_files"] == ["foo.py", "bar.py"]
        assert "diff_stat" in out

    def test_error_to_json(self):
        r = ClaudeCodeResult(success=False, error="Something went wrong")
        out = json.loads(r.to_tool_output())
        assert out["success"] is False
        assert "error" in out

    def test_empty_fields_omitted(self):
        r = ClaudeCodeResult(success=True, result_text="ok")
        out = json.loads(r.to_tool_output())
        assert "session_id" not in out
        assert "changed_files" not in out
        assert "error" not in out
        assert "validation" not in out


# ---------------------------------------------------------------------------
# Path guard hook
# ---------------------------------------------------------------------------

class TestPathGuard:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_allows_file_inside_cwd(self, tmp_path):
        guard = make_path_guard(str(tmp_path))
        result = self._run(guard(
            {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "foo.py")}},
            "tid-1", None,
        ))
        assert result == {}

    def test_blocks_file_outside_cwd(self, tmp_path):
        guard = make_path_guard(str(tmp_path / "subdir"))
        (tmp_path / "subdir").mkdir()
        result = self._run(guard(
            {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "outside.py")}},
            "tid-2", None,
        ))
        assert result != {}
        assert "deny" in str(result)

    def test_blocks_safety_critical_file(self, tmp_path):
        guard = make_path_guard(str(tmp_path))
        for critical in SAFETY_CRITICAL:
            result = self._run(guard(
                {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / critical)}},
                f"tid-{critical}", None,
            ))
            assert "deny" in str(result), f"Should block {critical}"

    def test_allows_read_tool(self, tmp_path):
        guard = make_path_guard(str(tmp_path))
        result = self._run(guard(
            {"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}},
            "tid-read", None,
        ))
        assert result == {}

    def test_blocks_relative_path_escape(self, tmp_path):
        guard = make_path_guard(str(tmp_path))
        result = self._run(guard(
            {"tool_name": "Write", "tool_input": {"file_path": "../../../etc/evil"}},
            "tid-escape", None,
        ))
        assert "deny" in str(result)


# ---------------------------------------------------------------------------
# Read-only guard hook
# ---------------------------------------------------------------------------

class TestReadonlyGuard:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_blocks_edit(self):
        guard = make_readonly_guard()
        result = self._run(guard(
            {"tool_name": "Edit", "tool_input": {}}, "tid-1", None,
        ))
        assert "deny" in str(result)

    def test_blocks_bash(self):
        guard = make_readonly_guard()
        result = self._run(guard(
            {"tool_name": "Bash", "tool_input": {}}, "tid-2", None,
        ))
        assert "deny" in str(result)

    def test_allows_read(self):
        guard = make_readonly_guard()
        result = self._run(guard(
            {"tool_name": "Read", "tool_input": {}}, "tid-3", None,
        ))
        assert result == {}

    def test_allows_grep(self):
        guard = make_readonly_guard()
        result = self._run(guard(
            {"tool_name": "Grep", "tool_input": {}}, "tid-4", None,
        ))
        assert result == {}

    def test_allows_glob(self):
        guard = make_readonly_guard()
        result = self._run(guard(
            {"tool_name": "Glob", "tool_input": {}}, "tid-5", None,
        ))
        assert result == {}


# ---------------------------------------------------------------------------
# Orchestration helpers (now in shell.py)
# ---------------------------------------------------------------------------

class TestProjectContext:
    def test_loads_existing_docs(self, tmp_path):
        (tmp_path / "BIBLE.md").write_text("# Constitution")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "DEVELOPMENT.md").write_text("# Dev guide")
        ctx = _load_project_context(tmp_path)
        assert "CONSTITUTION" in ctx
        assert "DEVELOPMENT GUIDE" in ctx

    def test_handles_missing_docs(self, tmp_path):
        ctx = _load_project_context(tmp_path)
        assert ctx == ""  # no docs, empty context

    def test_truncates_large_docs(self, tmp_path):
        (tmp_path / "BIBLE.md").write_text("x" * 100_000)
        ctx = _load_project_context(tmp_path)
        assert "truncated" in ctx.lower()


# ---------------------------------------------------------------------------
# SDK import fallback contract
# ---------------------------------------------------------------------------

class TestImportFallback:
    """Verify the gateway raises ImportError when SDK is unavailable."""

    def test_gateway_import_requires_sdk(self):
        """Without the real SDK (or our mock), import should raise ImportError."""
        # We can't truly un-mock here, but we can verify the design intent:
        # The module does `from claude_agent_sdk import ...` at module level,
        # so ImportError is raised before any code runs.
        # This test documents the contract.
        import importlib
        saved = sys.modules.get("claude_agent_sdk")
        try:
            # Temporarily remove the mock
            sys.modules.pop("claude_agent_sdk", None)
            # Also remove cached gateway module
            sys.modules.pop("ouroboros.gateways.claude_code", None)
            with pytest.raises(ImportError):
                importlib.import_module("ouroboros.gateways.claude_code")
        finally:
            # Restore
            if saved is not None:
                sys.modules["claude_agent_sdk"] = saved
            # Re-import with mock
            sys.modules.pop("ouroboros.gateways.claude_code", None)
            _ensure_gateway_importable()
            importlib.import_module("ouroboros.gateways.claude_code")


# ---------------------------------------------------------------------------
# SDK API surface verification tests (v4.8.1 fixes)
# ---------------------------------------------------------------------------

class TestSDKAPISurface:
    """Verify that the gateway uses correct SDK API method names and signatures.

    These tests inspect source code to catch method name mismatches that would
    cause AttributeError at runtime (e.g. receive_response vs receive_messages).
    """

    def _gateway_source(self):
        import inspect
        from ouroboros.gateways import claude_code
        return inspect.getsource(claude_code)

    def test_edit_path_uses_receive_response(self):
        """Edit path must use receive_response() — it auto-stops after ResultMessage.

        receive_messages() streams indefinitely and can hang.
        receive_response() is the correct high-level method.
        """
        src = self._gateway_source()
        assert "receive_response()" in src, "Edit path must call receive_response()"
        assert "receive_messages()" not in src, (
            "receive_messages() streams indefinitely — use receive_response() instead"
        )

    def test_readonly_path_uses_query_function(self):
        """v4.8.1 fix: read-only path should use query() not ClaudeSDKClient."""
        src = self._gateway_source()
        # _run_readonly_async should iterate with `async for message in query(`
        assert "async for message in query(" in src, \
            "Read-only path should use query() function for one-shot requests"

    def test_max_budget_in_constructor(self):
        """v4.8.1 fix: max_budget_usd should be passed in ClaudeAgentOptions constructor."""
        src = self._gateway_source()
        # Should NOT have post-assignment pattern
        assert "options.max_budget_usd" not in src, \
            "max_budget_usd should be in constructor, not post-assigned"
        # Should have it in the constructor call
        assert "max_budget_usd=budget" in src, \
            "max_budget_usd should be passed as constructor kwarg"

    def test_query_imported_from_sdk(self):
        """query() must be imported from claude_agent_sdk."""
        from ouroboros.gateways.claude_code import query as gw_query
        # The mock installs query on the mock module
        mock_sdk = sys.modules.get("claude_agent_sdk")
        assert gw_query is mock_sdk.query, \
            "Gateway's query should be the SDK's query function"
