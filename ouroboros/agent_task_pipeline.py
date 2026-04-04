"""
Post-task processing pipeline for the Ouroboros agent.

Handles task-result emission, trace summarization, memory consolidation,
scratchpad compaction, execution reflection, and review context building.
Extracted from agent.py to keep the agent thin.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
import time
from typing import Any, Dict, List

from ouroboros.task_results import STATUS_COMPLETED, write_task_result
from ouroboros.utils import utc_now_iso, append_jsonl

log = logging.getLogger(__name__)


def _parse_model_provider(model: str) -> str:
    model_name = str(model or "").strip()
    for prefix, provider in (
        ("openai::", "openai"),
        ("anthropic::", "anthropic"),
        ("cloudru::", "cloudru"),
        ("openai-compatible::", "openai-compatible"),
        ("openrouter::", "openrouter"),
    ):
        if model_name.startswith(prefix):
            return provider
    return "openrouter"


def _has_provider_credentials(provider: str) -> bool:
    if provider == "openai":
        return bool(str(os.environ.get("OPENAI_API_KEY", "") or "").strip())
    if provider == "anthropic":
        return bool(str(os.environ.get("ANTHROPIC_API_KEY", "") or "").strip())
    if provider == "cloudru":
        return bool(str(os.environ.get("CLOUDRU_FOUNDATION_MODELS_API_KEY", "") or "").strip())
    if provider == "openai-compatible":
        compatible_key = str(os.environ.get("OPENAI_COMPATIBLE_API_KEY", "") or "").strip()
        legacy_key = str(os.environ.get("OPENAI_API_KEY", "") or "").strip()
        legacy_base = str(os.environ.get("OPENAI_BASE_URL", "") or "").strip()
        return bool(compatible_key or (legacy_key and legacy_base))
    return bool(str(os.environ.get("OPENROUTER_API_KEY", "") or "").strip())


def _resolve_task_summary_model(default_model: str) -> str:
    if _has_provider_credentials(_parse_model_provider(default_model)):
        return default_model

    for env_name in (
        "OUROBOROS_MODEL_LIGHT",
        "OUROBOROS_MODEL_FALLBACK",
        "OUROBOROS_MODEL",
        "OUROBOROS_MODEL_CODE",
    ):
        candidate = str(os.environ.get(env_name, "") or "").strip()
        if candidate and _has_provider_credentials(_parse_model_provider(candidate)):
            return candidate
    return default_model


def _truncate_with_notice(text: Any, limit: int) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    return raw[:limit] + f"\n...[truncated from {len(raw)} chars; omitted {len(raw) - limit}]"


def build_trace_summary(llm_trace: dict) -> str:
    """Return a compact human-readable summary of tool calls and agent notes."""
    tool_calls = llm_trace.get("tool_calls", []) or []
    notes = llm_trace.get("reasoning_notes", []) or []

    n = len(tool_calls)
    errors = sum(1 for tc in tool_calls if isinstance(tc, dict) and tc.get("is_error"))

    lines: list[str] = [f"## Tool trace ({n} calls, {errors} errors)"]

    if not tool_calls:
        lines.append("No tool calls.")
    else:
        def _fmt_call(idx: int, tc: dict) -> str:
            name = tc.get("tool", "unknown")
            args = tc.get("args", {})
            if isinstance(args, dict):
                parts = []
                for k, v in list(args.items())[:2]:
                    v_str = str(v)
                    if len(v_str) > 60:
                        v_str = v_str[:57] + "..."
                    parts.append(f"{k}={v_str!r}")
                if len(args) > 2:
                    parts.append(f"... (+{len(args) - 2} more args)")
                args_str = ", ".join(parts)
            else:
                args_str = repr(args)
                if len(args_str) > 80:
                    args_str = args_str[:77] + "..."
            facts = []
            status = str(tc.get("status") or "").strip()
            if status and status != "ok":
                facts.append(f"status={status}")
            if tc.get("exit_code") not in (None, 0):
                facts.append(f"exit_code={tc.get('exit_code')}")
            if tc.get("signal"):
                facts.append(f"signal={tc.get('signal')}")
            fact_suffix = f" [{', '.join(facts)}]" if facts else ""
            suffix = " → ERROR" if tc.get("is_error") else ""
            return f"{idx}. {name}({args_str}){fact_suffix}{suffix}"

        if n > 30:
            shown = (
                [_fmt_call(i + 1, tool_calls[i]) for i in range(15)]
                + [f"... ({n - 30} more calls) ..."]
                + [_fmt_call(n - 14 + i, tool_calls[n - 15 + i]) for i in range(15)]
            )
        else:
            shown = [_fmt_call(i + 1, tool_calls[i]) for i in range(n)]
        lines.extend(shown)

    if notes:
        lines.append("\n## Agent notes (supplementary, not source of truth)")
        lines.extend(f"- {note}" for note in notes)

    summary = "\n".join(lines)
    if len(summary) > 4000:
        summary = summary[:3997] + "..."
    return summary


def _run_post_task_processing_async(
    env: Any,
    task: Dict[str, Any],
    usage: Dict[str, Any],
    llm_trace: Dict[str, Any],
    drive_logs: pathlib.Path,
) -> None:
    """Best-effort async post-task memory work that must not block reply delivery."""
    task_snapshot = json.loads(json.dumps(task, ensure_ascii=False, default=str))
    usage_snapshot = json.loads(json.dumps(usage, ensure_ascii=False, default=str))
    trace_snapshot = json.loads(json.dumps(llm_trace, ensure_ascii=False, default=str))

    def _run() -> None:
        try:
            from ouroboros.llm import LLMClient

            llm_client = LLMClient()
            _run_task_summary(env, llm_client, task_snapshot, usage_snapshot, trace_snapshot, drive_logs)
            _run_reflection(env, llm_client, task_snapshot, usage_snapshot, trace_snapshot)
        except Exception:
            log.warning("Async post-task processing failed", exc_info=True)

    threading.Thread(target=_run, daemon=True).start()


def emit_task_results(
    env: Any, memory: Any, llm: Any,
    pending_events: List[Dict[str, Any]],
    task: Dict[str, Any], text: str,
    usage: Dict[str, Any], llm_trace: Dict[str, Any],
    start_time: float, drive_logs: pathlib.Path,
    ctx: Any = None,
) -> None:
    """Emit all end-of-task events to supervisor and run post-task processing."""
    pending_events.append({
        "type": "send_message", "chat_id": task["chat_id"],
        "text": text or "\u200b", "log_text": text or "",
        "format": "markdown",
        "task_id": task.get("id"), "ts": utc_now_iso(),
    })

    duration_sec = round(time.time() - start_time, 3)
    n_tool_calls = len(llm_trace.get("tool_calls", []))
    n_tool_errors = sum(1 for tc in llm_trace.get("tool_calls", [])
                        if isinstance(tc, dict) and tc.get("is_error"))
    try:
        append_jsonl(drive_logs / "events.jsonl", {
            "ts": utc_now_iso(), "type": "task_eval", "ok": True,
            "task_id": task.get("id"), "task_type": task.get("type"),
            "duration_sec": duration_sec,
            "tool_calls": n_tool_calls,
            "tool_errors": n_tool_errors,
            "response_len": len(text),
        })
    except Exception:
        log.warning("Failed to log task eval event", exc_info=True)
        pass

    pending_events.append({
        "type": "task_metrics",
        "task_id": task.get("id"), "task_type": task.get("type"),
        "duration_sec": duration_sec,
        "tool_calls": n_tool_calls, "tool_errors": n_tool_errors,
        "cost_usd": round(float(usage.get("cost") or 0), 6),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_rounds": int(usage.get("rounds") or 0),
        "ts": utc_now_iso(),
    })

    pending_events.append({
        "type": "task_done",
        "task_id": task.get("id"),
        "task_type": task.get("type"),
        "cost_usd": round(float(usage.get("cost") or 0), 6),
        "total_rounds": int(usage.get("rounds") or 0),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "ts": utc_now_iso(),
    })
    # NOTE: task_done is NOT written to events.jsonl here.
    # It goes through EVENT_Q → supervisor _handle_task_done → append_jsonl.
    # This ensures causal ordering: send_message reaches the UI before task_done,
    # preventing the live card from collapsing before the assistant reply arrives.

    _store_task_result(env, task, text, usage, llm_trace)
    restart_reason = str(getattr(ctx, "pending_restart_reason", "") or "").strip()
    if restart_reason:
        pending_events.append({
            "type": "restart_request",
            "reason": restart_reason,
            "ts": utc_now_iso(),
        })
        try:
            ctx.pending_restart_reason = None
        except Exception:
            pass

    _run_chat_consolidation(env, memory, llm, task, drive_logs)
    _run_scratchpad_consolidation(env, memory, llm)
    _run_post_task_processing_async(env, task, usage, llm_trace, drive_logs)


def _store_task_result(env: Any, task: Dict[str, Any], text: str,
                       usage: Dict[str, Any], llm_trace: Dict[str, Any]) -> None:
    """Store task result for parent task retrieval."""
    try:
        trace_summary = build_trace_summary(llm_trace)
        write_task_result(
            env.drive_root,
            str(task.get("id") or ""),
            STATUS_COMPLETED,
            parent_task_id=task.get("parent_task_id"),
            description=task.get("description"),
            context=task.get("context"),
            result=text or "",
            trace_summary=trace_summary,
            cost_usd=round(float(usage.get("cost") or 0), 6),
            total_rounds=int(usage.get("rounds") or 0),
            ts=utc_now_iso(),
        )
    except Exception as e:
        log.warning("Failed to store task result: %s", e)


_TASK_SUMMARY_PROMPT = """\
Summarize this completed task for Ouroboros's episodic memory.
Be specific about: what was tried, what worked, what failed, key decisions made.
Include file names, tool names, error messages when relevant.
Treat tool statuses and exit/signal facts as authoritative. Agent notes are supplementary only.
Never claim a tool succeeded when the trace shows non-zero exit, timeout, install_error, or any error status.
If the task was trivial (simple reply, no tool calls), keep it to 1-2 sentences.
End with: "Details: progress.jsonl + tools.jsonl for task_id={task_id}"

## Task
Goal: {goal}
Type: {task_type}
Rounds: {rounds}, Cost: ${cost:.2f}

## Execution trace
{trace_summary}
"""


def _run_task_summary(env, llm, task, usage, llm_trace, drive_logs):
    """Generate a detailed task summary and inject it into chat.jsonl."""
    try:
        from ouroboros.consolidator import (
            CONSOLIDATION_MODEL,
            CONSOLIDATION_REASONING_EFFORT,
        )
        summary_model = _resolve_task_summary_model(CONSOLIDATION_MODEL)
        task_id = task.get("id", "unknown")
        goal = _truncate_with_notice(task.get("text", ""), 500)
        rounds = int(usage.get("rounds") or 0)
        cost = float(usage.get("cost") or 0)
        trace = build_trace_summary(llm_trace)
        prompt = _TASK_SUMMARY_PROMPT.format(
            task_id=task_id, goal=goal or "(no goal text)",
            task_type=task.get("type", "user"), rounds=rounds,
            cost=cost, trace_summary=_truncate_with_notice(trace, 3000),
        )
        try:
            msg, _usage = llm.chat(messages=[{"role": "user", "content": prompt}],
                                   model=summary_model,
                                   reasoning_effort=CONSOLIDATION_REASONING_EFFORT,
                                   max_tokens=2048)
            summary_text = (msg.get("content") or "").strip()
            if _usage.get("cost"):
                try:
                    from supervisor.state import update_budget_from_usage
                    update_budget_from_usage(_usage)
                except Exception:
                    pass
        except Exception:
            log.warning("Task summary LLM call failed, using fallback", exc_info=True)
            summary_text = (
                f"Task {task_id} ({task.get('type', 'user')}): "
                f"{_truncate_with_notice(goal, 200)}. {rounds}r, ${cost:.2f}."
            )
        if summary_text:
            append_jsonl(drive_logs / "chat.jsonl", {
                "ts": utc_now_iso(), "direction": "system",
                "type": "task_summary", "task_id": task_id, "text": summary_text,
            })
    except Exception:
        log.debug("Task summary generation failed (non-critical)", exc_info=True)


def _run_chat_consolidation(env, memory, llm, task, drive_logs):
    """Run dialogue-block consolidation in a daemon thread."""
    try:
        from ouroboros import consolidator as _c

        should_consolidate = getattr(_c, "should_consolidate_chat_blocks", None) or getattr(_c, "should_consolidate")
        consolidate = getattr(_c, "consolidate_chat_blocks", None) or getattr(_c, "consolidate")
        chat_path = drive_logs / "chat.jsonl"
        blocks_path = env.drive_path("memory") / "dialogue_blocks.json"
        meta_path = env.drive_path("memory") / "dialogue_meta.json"
        if should_consolidate(meta_path, chat_path):
            _id, _ident, _llm, _logs = task.get("id"), memory.load_identity(), llm, drive_logs
            def _run():
                try:
                    u = consolidate(chat_path=chat_path, blocks_path=blocks_path,
                                    meta_path=meta_path, llm_client=_llm, identity_text=_ident)
                    if u:
                        append_jsonl(_logs / "events.jsonl", {"ts": utc_now_iso(),
                            "type": "chat_block_consolidation", "task_id": _id,
                            "cost_usd": round(float(u.get("cost") or 0), 6)})
                except Exception:
                    log.warning("Chat block consolidation failed", exc_info=True)
            threading.Thread(target=_run, daemon=True).start()
    except Exception:
        log.warning("Chat block consolidation setup failed", exc_info=True)


def _run_scratchpad_consolidation(env: Any, memory: Any, llm: Any) -> None:
    """Run scratchpad consolidation in a daemon thread."""
    try:
        from ouroboros import consolidator as _c

        should_consolidate = getattr(_c, "should_consolidate_scratchpad_blocks", None) or getattr(_c, "should_consolidate_scratchpad")
        consolidate = getattr(_c, "consolidate_scratchpad_blocks", None) or getattr(_c, "consolidate_scratchpad")
        if should_consolidate(memory):
            kb_dir = env.drive_path("memory/knowledge")
            _identity = memory.load_identity()

            def _run():
                try:
                    consolidate(memory, kb_dir, llm, _identity)
                except Exception:
                    log.warning("Scratchpad consolidation failed", exc_info=True)

            threading.Thread(target=_run, daemon=True).start()
    except Exception:
        log.debug("Scratchpad consolidation setup failed", exc_info=True)


def _run_reflection(env: Any, llm: Any, task: Dict[str, Any],
                    usage: Dict[str, Any], llm_trace: Dict[str, Any]) -> None:
    """Run execution reflection synchronously (process memory, Bible P1)."""
    try:
        from ouroboros.reflection import (
            should_generate_reflection, generate_reflection, append_reflection,
        )
        if should_generate_reflection(llm_trace):
            trace_summary = build_trace_summary(llm_trace)
            try:
                entry = generate_reflection(
                    task, llm_trace, trace_summary,
                    llm, usage,
                )
                append_reflection(env.drive_root, entry)
            except Exception:
                log.warning("Execution reflection failed (non-critical)", exc_info=True)
    except Exception:
        log.debug("Execution reflection setup failed", exc_info=True)


def build_review_context(env: Any) -> str:
    """Collect full codebase for review tasks (1M-context models get the whole thing)."""
    _TOKEN_LIMIT = 600_000
    try:
        from ouroboros.review import (
            collect_full_codebase, collect_sections,
            chunk_sections, compute_complexity_metrics, format_metrics,
            _SKIP_EXT, _SKIP_FILENAMES, _MAX_FILE_BYTES,
        )

        _dry_bytes = 0
        for _root, _skip_dirs, _skip_ext_extra in [
            (env.repo_dir,
             {"__pycache__", ".git", ".pytest_cache", ".mypy_cache",
              "node_modules", ".venv", ".idea", ".vscode"},
             frozenset()),
            (env.drive_root,
             {"archive", "locks", "downloads", "screenshots"},
             {".jsonl"}),
        ]:
            try:
                _root_resolved = _root.resolve()
                if not _root_resolved.exists():
                    continue
                for _dirpath, _dirnames, _filenames in os.walk(str(_root_resolved)):
                    _dirnames[:] = [d for d in _dirnames if d not in _skip_dirs]
                    for _fn in _filenames:
                        try:
                            _p = pathlib.Path(_dirpath) / _fn
                            if not _p.is_file() or _p.is_symlink():
                                continue
                            if _p.suffix.lower() in _SKIP_EXT:
                                continue
                            if _p.suffix.lower() in _skip_ext_extra:
                                continue
                            if _fn in _SKIP_FILENAMES:
                                continue
                            _dry_bytes += min(_p.stat().st_size, _MAX_FILE_BYTES)
                        except Exception:
                            continue
            except Exception:
                continue

        if _dry_bytes / 3.5 > _TOKEN_LIMIT:
            sections, stats = collect_sections(env.repo_dir, env.drive_root)
            metrics = compute_complexity_metrics(sections)
            parts = [
                "## Code Review Context\n"
                "(Fallback: codebase too large for single-context review)\n",
                format_metrics(metrics),
                f"\nFiles: {stats['files']}, chars: {stats['chars']}\n",
                "\nUse repo_read to inspect specific files. "
                "Use run_shell for tests. Key files below:\n",
            ]
            if stats.get("truncated"):
                parts.append(f"\nCompacted files: {stats['truncated']}\n")
            if stats.get("dropped"):
                dropped_paths = stats.get("dropped_paths") or []
                preview = ", ".join(dropped_paths[:5])
                parts.append(
                    f"\nDropped files due review budget: {stats['dropped']}"
                    + (f" ({preview}{' ...' if len(dropped_paths) > 5 else ''})" if preview else "")
                    + "\n"
                )
            chunks = chunk_sections(sections)
            parts.append(chunks[0] if chunks else "(No reviewable content found.)")
            return "\n".join(parts)

        full_text, full_stats = collect_full_codebase(env.repo_dir, env.drive_root)

        if full_stats["tokens"] <= _TOKEN_LIMIT:
            sections, _ = collect_sections(env.repo_dir, env.drive_root)
            metrics = compute_complexity_metrics(sections)
            parts = [
                "## Full Codebase for Review\n",
                format_metrics(metrics),
                f"\nFiles: {full_stats['files']}, estimated tokens: {full_stats['tokens']}\n",
                full_text,
                "\nYou have the complete codebase above. "
                "Identify issues, patterns, security concerns, and areas for improvement.",
            ]
            return "\n".join(parts)
        else:
            sections, stats = collect_sections(env.repo_dir, env.drive_root)
            metrics = compute_complexity_metrics(sections)
            parts = [
                "## Code Review Context\n"
                "(Fallback: codebase too large for single-context review)\n",
                format_metrics(metrics),
                f"\nFiles: {stats['files']}, chars: {stats['chars']}\n",
                "\nUse repo_read to inspect specific files. "
                "Use run_shell for tests. Key files below:\n",
            ]
            if stats.get("truncated"):
                parts.append(f"\nCompacted files: {stats['truncated']}\n")
            if stats.get("dropped"):
                dropped_paths = stats.get("dropped_paths") or []
                preview = ", ".join(dropped_paths[:5])
                parts.append(
                    f"\nDropped files due review budget: {stats['dropped']}"
                    + (f" ({preview}{' ...' if len(dropped_paths) > 5 else ''})" if preview else "")
                    + "\n"
                )
            chunks = chunk_sections(sections)
            parts.append(chunks[0] if chunks else "(No reviewable content found.)")
            return "\n".join(parts)
    except Exception as e:
        return f"## Code Review Context\n\n(Failed to collect: {e})\nUse repo_read and repo_list to inspect code."
