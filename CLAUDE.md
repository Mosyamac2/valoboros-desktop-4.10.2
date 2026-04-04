# CLAUDE.md — Ouroboros → VALOBOROS Adaptation Project

**Upstream:** [joi-lab/ouroboros-desktop](https://github.com/joi-lab/ouroboros-desktop) — codebase version 4.11.8 (last tagged release: v3.3.1)
**Goal:** Repurpose Ouroboros into a **self-improving ML-model validation platform** (codename VALOBOROS) that autonomously develops and refines validation methodologies.

---

## How to Use This File

This file is the single entry point for Claude Code. Read it before any task. It replaces ad-hoc context dumps in prompts.

---

## Project Identity

Ouroboros is a self-evolving AI agent: it reads its own source, modifies it through git commits, reviews changes with multiple LLM models, and accumulates experience across restarts. VALOBOROS preserves this self-improvement loop and redirects it toward ML-model validation.

**Core value proposition:** The system doesn't just validate models — it learns from each validation, discovers patterns of model failure, and autonomously improves its own validation methodologies over time.

**What "ML-model validation" means here:** Evaluating a trained model's out-of-sample performance to confirm it generalizes, doesn't overfit/underfit, doesn't leak data, doesn't exhibit bias, handles feature sensitivity correctly, and behaves intuitively. Each incoming model arrives as a bundle: ZIP archive with source code, a model report (task description, intended use, known limitations), and sample data (train/test splits, example inputs/outputs).

---

## Repository Structure

Verified against the actual source tree (ouroboros-desktop-main.zip).

```
ouroboros-desktop/
├── launcher.py                  # Process manager (PyWebView desktop / headless)
│                                # Syncs 3 safety files from bundle on every start
│                                # (lines 99-103: sync_paths)
├── server.py                    # Starlette + uvicorn HTTP/WebSocket server, port 8765 (44K)
├── BIBLE.md                     # Constitution: 9 principles P0–P8, 393 lines (🔒 agent cannot write)
├── VERSION                      # Semver string: "4.11.8"
├── README.md                    # Changelog and project description (38K)
├── pyproject.toml               # Package metadata (version must match VERSION)
├── requirements.txt             # Runtime deps: openai, requests, dulwich, starlette,
│                                # uvicorn, websockets, huggingface_hub, claude-agent-sdk,
│                                # playwright, playwright-stealth
├── requirements-launcher.txt    # Launcher-only deps
├── Makefile                     # make test, make test-v, make health, make clean
├── build.sh                     # macOS app signing + notarization + DMG packaging
├── Ouroboros.spec               # PyInstaller spec for frozen builds
├── entitlements.plist           # macOS sandbox entitlements
│
├── prompts/                     # System prompts loaded into LLM context
│   ├── SYSTEM.md                # Operational brain, 760 lines (✏️ agent-writable, high-risk)
│   ├── SAFETY.md                # Safety verdict definitions, 32 lines (🔒🔄 overwritten every launch)
│   └── CONSCIOUSNESS.md         # Background thinking tasks, 142 lines (✏️ agent-writable)
│
├── docs/                        # Architecture and conventions (✏️ agent-writable)
│   ├── ARCHITECTURE.md          # Full component map, 61K — agent reads as "body map"
│   ├── DEVELOPMENT.md           # Naming, entity types, module limits, design system
│   └── CHECKLISTS.md            # Pre-commit review: 13 repo + 6 intent/scope items
│
├── ouroboros/                   # Agent core — ~13,400 lines Python across 30+ modules
│   ├── __init__.py
│   ├── config.py                # SETTINGS_DEFAULTS, paths, load/save with file locking
│   ├── compat.py                # Platform abstraction (Unix/Windows file locking, etc.)
│   ├── version.py               # Version reading helper
│   ├── provider_models.py       # Model value migration between providers
│   │
│   │   # ── Agent orchestration ──
│   ├── agent.py                 # Task orchestrator (19K)
│   ├── agent_task_pipeline.py   # Task processing pipeline (17K)
│   ├── agent_startup_checks.py  # Boot-time health checks (13K)
│   │
│   │   # ── LLM loop ──
│   ├── loop.py                  # LLM ↔ tool-call loop (23K)
│   ├── loop_llm_call.py         # LLM call logic inside loop (9K)
│   ├── loop_tool_execution.py   # Tool dispatch: parallelism, timeouts, truncation (24K)
│   │
│   │   # ── Context and memory ──
│   ├── context.py               # Builds 3-part system message (static/semi-stable/dynamic) (34K)
│   ├── context_compaction.py    # Context compression when hitting limits (12K)
│   ├── consciousness.py         # Background thinking loop + _BG_TOOL_WHITELIST (25K)
│   ├── consolidator.py          # Dialog → dialogue_blocks.json (29K)
│   ├── memory.py                # Seeds identity.md / scratchpad.md on first run (18K)
│   ├── owner_inject.py          # Owner message injection during running tasks (3.5K)
│   │
│   │   # ── Review and reflection ──
│   ├── reflection.py            # Post-error LLM reflections, 150–250 words (10K)
│   ├── review.py                # Multi-model pre-commit review (11K)
│   ├── review_state.py          # Advisory review state tracking (13K)
│   ├── deep_self_review.py      # Full project self-review (8.5K)
│   │
│   │   # ── Safety ──
│   ├── safety.py                # LLM Safety Supervisor: 2-pass check (🔒🔄 overwritten) (11K)
│   │
│   │   # ── LLM and models ──
│   ├── llm.py                   # Sole LLM API caller: OpenRouter / direct / local (51K — largest module)
│   ├── pricing.py               # Model pricing table, cost estimation (7.5K)
│   ├── local_model.py           # llama-cpp-python lifecycle management (17K)
│   ├── local_model_api.py       # Local model API helpers (2.5K)
│   ├── local_model_autostart.py # Auto-start local model on boot (1.5K)
│   │
│   │   # ── Tool system ──
│   ├── tool_capabilities.py     # CORE_TOOL_NAMES (38), META_TOOL_NAMES, result limits (5K)
│   ├── tool_policy.py           # is_initial_task_tool() — round-1 visibility (2K)
│   ├── task_results.py          # Cross-task result storage on Drive (2K)
│   │
│   │   # ── Server and UI APIs ──
│   ├── server_entrypoint.py     # Server startup (2.5K)
│   ├── server_runtime.py        # Runtime lifecycle (7.5K)
│   ├── server_web.py            # Web route handlers (2K)
│   ├── server_auth.py           # Network password auth (10K)
│   ├── server_control.py        # /panic, /restart, /status endpoints (3K)
│   ├── server_history_api.py    # Chat history API (7K)
│   ├── file_browser_api.py      # Web UI file browser (23K)
│   ├── model_catalog_api.py     # Model selection API (8K)
│   ├── onboarding_wizard.py     # First-run setup wizard (13K)
│   ├── launcher_bootstrap.py    # Bootstrap repo from bundle on first install (13K)
│   │
│   │   # ── Utilities ──
│   ├── utils.py                 # Shared utilities (19K)
│   ├── world_profiler.py        # Generates WORLD.md (OS, CPU, RAM, CLIs) (2K)
│   │
│   ├── gateways/
│   │   ├── __init__.py
│   │   └── claude_code.py       # Claude Agent SDK wrapper with PreToolUse safety hooks (13K)
│   │
│   └── tools/                   # Auto-discovered plugins (each exports get_tools() → List[ToolEntry])
│       ├── __init__.py
│       ├── registry.py          # Sandbox, ToolContext, ToolRegistry, SAFETY_CRITICAL_PATHS (🔒🔄) (16K)
│       ├── core.py              # repo_read/list, data_read/list/write, code_search, etc. (24K)
│       ├── git.py               # repo_write, repo_commit, str_replace_editor, git_status/diff (45K)
│       ├── shell.py             # run_shell, claude_code_edit (17K)
│       ├── control.py           # restart, schedule_task, update_scratchpad/identity, etc. (20K)
│       ├── knowledge.py         # knowledge_read/write/list (11K)
│       ├── memory_tools.py      # memory_map, memory_update_registry (4.5K)
│       ├── search.py            # web_search via OpenAI Responses API (7K)
│       ├── browser.py           # browse_page, browser_action via Playwright/stealth (17K)
│       ├── vision.py            # analyze_screenshot, vlm_query (7K)
│       ├── github.py            # GitHub Issues CRUD via gh CLI (11K)
│       ├── review.py            # multi_model_review tool (36K)
│       ├── review_helpers.py    # Shared review utilities (10K)
│       ├── scope_review.py      # Scope/intent review — runs after triad review (17K)
│       ├── claude_advisory_review.py  # advisory_pre_review, review_status (27K)
│       ├── evolution_stats.py   # generate_evolution_stats (8.5K)
│       ├── health.py            # codebase_health (4K)
│       ├── compact_context.py   # compact_context tool (3K)
│       └── tool_discovery.py    # list_available_tools, enable_tools (meta) (4K)
│
├── supervisor/                  # Process management — ~157K total
│   ├── __init__.py
│   ├── events.py                # Event dispatch (25K)
│   ├── git_ops.py               # Git operations for supervisor (30K)
│   ├── message_bus.py           # WebSocket message bus (26K)
│   ├── queue.py                 # Task queue and scheduling (21K)
│   ├── state.py                 # Budget tracking, session, worker states (27K)
│   └── workers.py               # Worker lifecycle management (28K)
│
├── web/                         # SPA frontend (HTML/JS/CSS)
│   ├── index.html               # Main page shell
│   ├── app.js                   # App entry point
│   ├── style.css                # Main stylesheet (43K) — glassmorphism design system
│   ├── settings.css / onboarding.css
│   ├── chart.umd.min.js         # Chart.js (bundled)
│   ├── modules/                 # JS modules: chat (49K), settings_ui (26K),
│   │                            # onboarding_wizard (51K), evolution, files,
│   │                            # logs, log_events, costs, about, ws, utils,
│   │                            # settings_catalog, settings_controls, settings_local_model
│   └── providers/               # Provider logo assets (openrouter, openai, anthropic, cloudru)
│
├── webview/                     # PyWebView JS bridge (desktop-only)
│   └── js/                      # api.js, customize.js, finish.js, lib/
│
├── scripts/
│   └── download_python_standalone.sh
│
├── tests/                       # 69 test files, pytest
│   ├── test_smoke.py            # Core smoke tests (20K)
│   ├── test_constitution.py     # BIBLE.md compliance tests (10K)
│   ├── test_commit_gate.py      # Pre-commit review gate (29K)
│   ├── test_scope_review.py     # Scope review (35K)
│   ├── test_phase7_pipeline.py  # Full pipeline tests (33K)
│   └── ... (64 more test files)
│
└── assets/                      # Icons (icns/ico/png), logo, screenshots
```

### Runtime Data Directory (`~/Ouroboros/`)

Created on first launch. This is where the agent lives and accumulates experience:

```
~/Ouroboros/
├── repo/                        # Self-modifying local Git repository (clone of source)
│                                # Branches: ouroboros (work), ouroboros-stable (promoted), main (untouched)
├── data/
│   ├── state/
│   │   ├── state.json           # Budget, session, worker states (📖 auto)
│   │   ├── settings.json        # All settings from SETTINGS_DEFAULTS (✏️ via Web UI)
│   │   └── server_port          # Current server port
│   ├── memory/
│   │   ├── identity.md          # "Who am I" manifesto (✏️, deletion forbidden, rewrites allowed)
│   │   ├── scratchpad.md        # Working memory rendered from blocks (📖 auto-generated)
│   │   ├── scratchpad_blocks.json  # FIFO queue, max 10 blocks (✏️ via update_scratchpad)
│   │   ├── WORLD.md             # System profile: OS, CPU, RAM, CLIs (📖 auto on first run)
│   │   ├── registry.md          # Metacognitive data map (✏️ via memory_update_registry)
│   │   ├── dialogue_blocks.json # Consolidated long-term dialog memory (📖 auto)
│   │   ├── dialogue_meta.json   # Consolidation offset (📖 auto)
│   │   └── knowledge/
│   │       ├── index-full.md    # Auto-index of all topics (📖 auto-updated)
│   │       ├── patterns.md      # Error patterns + root causes (✏️)
│   │       └── {topic}.md       # Individual knowledge topics (✏️)
│   └── logs/
│       ├── chat.jsonl           # Dialogs and responses
│       ├── progress.jsonl       # "Thinking aloud" progress messages
│       ├── task_reflections.jsonl  # Post-error reflections (last 10 → next task context)
│       ├── events.jsonl         # System events + LLM usage
│       ├── tools.jsonl          # Tool call log
│       └── supervisor.jsonl     # Supervisor diagnostics
```

---

## Critical Architecture Facts

### File protection levels

| Symbol | Meaning | How enforced |
|--------|---------|--------------|
| 🔒 | Agent cannot write | `SAFETY_CRITICAL_PATHS` in `ouroboros/tools/registry.py` line 23 |
| 🔄 | Overwritten every launch | `launcher.py → _sync_core_files()` lines 92–110 restores from bundle |
| ✏️ | Agent-writable | Changes persist in `~/Ouroboros/repo/` through git |
| 📖 | Read-only / auto-generated | Written by system, loaded into LLM context |

**Three files are both 🔒 and 🔄 — locked AND overwritten on every launch:**

| File | What it protects |
|------|-----------------|
| `ouroboros/safety.py` | 2-pass LLM safety supervisor, CHECKED_TOOLS, SAFE_SHELL_COMMANDS, whitelist logic |
| `prompts/SAFETY.md` | DANGEROUS/SUSPICIOUS/SAFE verdict definitions sent to safety LLM |
| `ouroboros/tools/registry.py` | Hardcoded sandbox: SAFETY_CRITICAL_PATHS, shell write indicators, git readonly subcommands, ToolRegistry, ToolContext |

**To change them permanently:** edit the file in the project source, then EITHER rebuild the frozen bundle OR remove the file from `sync_paths` list in `launcher.py` lines 99–103 OR keep them in sync_paths and always edit the bundle source. See Reference 3, section 3.3 for the three options with tradeoffs.

### Four-level safety system

1. **Hardcoded sandbox** (`registry.py`) — blocks writes to `SAFETY_CRITICAL_PATHS` = {`BIBLE.md`, `ouroboros/safety.py`, `ouroboros/tools/registry.py`, `prompts/SAFETY.md`}; detects dangerous shell write indicators (`rm`, `sed -i`, `tee`, `truncate`, `mv`, `cp`, `chmod`, `chown`, `unlink`, `rsync`); blocks mutable git commands through shell wrappers
2. **Deterministic whitelist** (`safety.py → _is_whitelisted()`) — `data_write`, `repo_write*`, `repo_commit`, `claude_code_edit` always pass (sandbox already caught dangerous paths); `run_shell` passes if first word ∈ `SAFE_SHELL_COMMANDS` = {`ls`, `cat`, `head`, `tail`, `grep`, `rg`, `find`, `wc`, `git`, `pip`, `pytest`, `pwd`, `whoami`, `date`, `which`, `file`, `stat`, `diff`, `tree`}
3. **LLM Safety Supervisor** (`safety.py → check_safety()`) — 2-pass for non-whitelisted `run_shell`: light model first → escalate to heavy model with nudge: "Think carefully. Is this actually malicious, or just a normal development command?" Three verdicts: SAFE / SUSPICIOUS (execute + warn) / DANGEROUS (block). Default: "When in doubt → SAFE."
4. **Post-edit revert** (`registry.py → _revert_safety_critical_files()`) — after `claude_code_edit`, uncommitted changes to critical files auto-reverted via `git checkout`

### Seven-step evolution protocol (SYSTEM.md lines ~599–621)

Every self-modification follows: **assess → select → implement → smoke test → multi-model review → Bible check → commit + restart.** This protocol is the core of self-improvement. Removing it turns the system into a regular chatbot.

### Context assembly (context.py → build_context)

The LLM system message is built in 3 parts with different caching strategies:

| Part | Cache | Sources |
|------|-------|---------|
| **Static** | Ephemeral, 1-hour TTL | SYSTEM.md, BIBLE.md, ARCHITECTURE.md, DEVELOPMENT.md, README.md, CHECKLISTS.md |
| **Semi-stable** | Ephemeral, no TTL | scratchpad_blocks.json, identity.md, dialogue_blocks.json, knowledge/index-full.md, knowledge/patterns.md, registry.md |
| **Dynamic** | None | Health invariants, state.json, runtime context (utc_now, git_head, budget, platform), recent chat, progress log, tool results, events, last 10 reflections, advisory review status, owner messages |

### Ten mechanisms that must survive adaptation

These are the unique self-improvement infrastructure. Removing any one degrades the system into a static agent:

1. **7-step evolution protocol** — structured self-modification with review gates
2. **Background consciousness loop** — proactive self-maintenance between tasks (7 rotating tasks)
3. **Identity persistence** — `identity.md` accumulates self-knowledge across restarts
4. **Pattern register** — `knowledge/patterns.md` for systematic error analysis
5. **Knowledge base** — growing domain knowledge in `knowledge/` with auto-index
6. **Multi-model review** — 2–3 models from different families cross-check every commit
7. **Task reflections** — 150–250 word LLM reflection after errors, last 10 loaded into context
8. **Scratchpad** — FIFO working memory (max 10 blocks) with eviction to journal
9. **Git versioning** — full traceability, branches (ouroboros / ouroboros-stable / main)
10. **Drift detector + 4-question self-diagnostics** — prevents loss of agency

**Adaptation principle: change GOALS and CONTEXT, preserve MECHANISMS.**

---

## VALOBOROS Domain: ML-Model Validation

### What the agent validates

Each incoming model is a bundle:

- **ZIP archive** with model source code and dependencies
- **Model report** — task description, intended use, known limitations, training details
- **Sample data** — training/test splits, example inputs/outputs

### Validation checks the agent must learn to perform

Out-of-sample/out-of-time performance, overfitting/underfitting detection, data leakage identification, bias/fairness assessment, feature sensitivity analysis (counterintuitive behavior), reproducibility verification, documentation completeness audit.

### How self-improvement applies

Through its evolution loop the agent: discovers new failure patterns across models → records in `knowledge/patterns.md`; develops new validation methodologies → stores as knowledge topics; refines its own validation code → commits through the 7-step protocol; accumulates domain expertise in `identity.md` and the knowledge base; reflects on validation failures → improves via `task_reflections.jsonl`.

---

## Adaptation Roadmap

Derived from Reference 3. Three levels, ordered by dependency.

### Level 1 — Redefine identity and goals

| File | What to change | Risk |
|------|----------------|------|
| `prompts/SYSTEM.md` (✏️) | Replace "I Am Ouroboros" self-identification → VALOBOROS validation role. Adapt 4 self-diagnostic questions to validation context. Add `## Business Context` section with domain vocabulary, KPIs, stakeholders. **Keep**: 7-step evolution protocol (lines ~599–621), all memory protocols, drift detector, tool result processing protocol | High — operational brain |
| `BIBLE.md` (🔒) | Adapt principle descriptions: P0 Agency → autonomous validation agent, P2 Self-Creation → creates/improves validation tools, P4 Authenticity → competent within validation domain, P6 Becoming → axes: technical/operational/strategic. Keep P1, P3, P5, P7, P8 unchanged. Add Business Hard Limits to Constraints. **Requires editing source + rebuild** | High — constitutional |
| `ouroboros/memory.py` (✏️) | Edit `_default_identity()` seed text → "I am VALOBOROS, a self-evolving ML-model validation agent..." | Medium |

### Level 2 — Configure operational parameters

| File | What to change |
|------|----------------|
| `ouroboros/config.py` → `SETTINGS_DEFAULTS` | Models, budgets, timeouts — see Reference 3 section 2.1. Or adjust at runtime via Web UI / env vars |
| `ouroboros/tool_capabilities.py` → `CORE_TOOL_NAMES` | Add new validation tool names so they're visible from round 1 |
| `prompts/CONSCIOUSNESS.md` (✏️) | Replace 7 maintenance tasks with validation-oriented ones. See Reference 3, section 2.3 |
| `docs/CHECKLISTS.md` (✏️) | Add validation-specific review items |
| `docs/DEVELOPMENT.md` (✏️) | Adapt coding standards if your team differs |

### Level 3 — Safety perimeter

| File | What to change | How to persist changes |
|------|----------------|----------------------|
| `prompts/SAFETY.md` (🔒🔄) | Extend DANGEROUS/SUSPICIOUS/SAFE for validation context | Edit source + rebuild, OR remove from `sync_paths` in launcher.py line 101 |
| `ouroboros/tools/registry.py` (🔒🔄) | Expand `SAFETY_CRITICAL_PATHS` | Same (launcher.py line 102) |
| `ouroboros/safety.py` (🔒🔄) | Add tools to `CHECKED_TOOLS` if they need LLM safety review | Same (launcher.py line 100) |

### Level 4 — Add validation tools

New tools go in `ouroboros/tools/` as Python modules exporting `get_tools() → List[ToolEntry]`. Auto-discovered at startup. Full 7-step process in Reference 3, section 4:

1. Create file in `ouroboros/tools/`, e.g. `validation.py`
2. Implement `ToolEntry`: `name` (str), `schema` (JSON Schema dict), `handler` (fn(ctx: ToolContext, **kwargs) → str), optionally `is_code_tool`, `timeout_sec`
3. Export `get_tools() → List[ToolEntry]`
4. Add tool names to `CORE_TOOL_NAMES` in `tool_capabilities.py` if needed from round 1
5. Add to `_BG_TOOL_WHITELIST` in `consciousness.py` (~line 490) if needed in background mode
6. For frozen builds: add module name to `_FROZEN_TOOL_MODULES` in `registry.py`
7. Update `docs/ARCHITECTURE.md` and `docs/CHECKLISTS.md`

**ToolContext** provides: `ctx.repo_dir` (Path), `ctx.drive_root` (Path to ~/Ouroboros/data/), `ctx.emit_progress_fn`, `ctx.pending_events`, `ctx.task_id`, `ctx.current_task_type`, `ctx.repo_path(rel)` (safe path with boundary check), `ctx.drive_path(rel)` (same for data dir).

---

## Coding Conventions

From `docs/DEVELOPMENT.md` — verified against actual source.

### Naming

| Element | Convention | Example |
|---------|-----------|---------|
| Modules, variables, functions | `snake_case` | `tool_capabilities.py`, `check_safety()` |
| Classes | `PascalCase` | `ToolRegistry`, `ClaudeCodeResult` |
| Constants | `UPPER_SNAKE_CASE` | `SAFETY_CRITICAL_PATHS`, `CORE_TOOL_NAMES` |
| Tools (LLM-callable) | `verb_noun` snake_case | `repo_read`, `browse_page`, `web_search` |

### Entity types

| Type | Purpose | Business logic? |
|------|---------|----------------|
| **Gateway** | Thin adapter to external API. Transport only, no routing. | No |
| **Service** | Orchestrates a domain concern. May use Gateways, manage state. | Yes |
| **Tool** | LLM-callable function. Thin wrapper connecting agent to Gateway/Service. | Minimal |

### Module limits (P5 Minimalism)

- Module: **~1000 lines max** (note: `llm.py` at 51K is the known exception)
- Method: **<150 lines**
- Function parameters: **<8**
- No gratuitous abstract layers, frameworks, ORMs, or base classes for abstraction's sake

### Structural rules

- **All LLM calls go through `ouroboros/llm.py`** — no ad-hoc HTTP clients or direct provider SDKs elsewhere (P3: LLM-First)
- **New tools** must export `get_tools()` using `ToolEntry` pattern from `registry.py`
- **No silent truncation** of cognitive artifacts. If content must be shortened, include explicit omission note
- **Read before write** — always read a file before modifying it
- **Prompts are code** (P5 DRY) — same rigor as source code
- **JS modules** must use CSS classes from `web/style.css`, not inline `style=""` attributes

### Design system (for UI changes)

Glassmorphism: `background: rgba(26, 21, 32, 0.75–0.88); backdrop-filter: blur(8–12px)`. Primary accent: `#c93545`. Hover: `#e85d6f`. Working/active states use crimson, not blue. Border radius scale defined via CSS vars. Full spec in `docs/DEVELOPMENT.md` "Design System" section.

---

## Task Guardrails

**Planning tasks:** Write output to `aux_notes/`. Do not modify project source files.

**Implementation tasks:** Follow the 7-step evolution protocol. One coherent transformation per commit. Read the file before writing it. Run smoke tests before committing. Preferred multi-file workflow: `repo_write` all files first, then `repo_commit` to stage + review + commit in one diff.

**Code editing strategy** (from SYSTEM.md):

| Scenario | Tool |
|----------|------|
| 1–3 surgical edits | `str_replace_editor` → `repo_commit` |
| New files / full rewrite | `repo_write` → `repo_commit` (shrink guard: >30% compression blocked) |
| 4+ files / cross-cutting refactor | `claude_code_edit` → `repo_commit` (Claude Agent SDK, PreToolUse hooks) |
| Legacy single-call | `repo_write_commit` |

**Ambiguities:** Do not guess. Flag as open questions. Check knowledge base (`knowledge_read`) before assuming.

**Dependencies:** Justify explicitly and add to `requirements.txt`.

**Scope:** One requirement or feature per prompt. Do not bundle unrelated changes.

---

## Commands

Available in the chat interface:

| Command | What it does |
|---------|-------------|
| `/panic` | Emergency SIGKILL of ALL processes. Absolute, outside principle hierarchy. Exit code 99 |
| `/restart` | Soft restart: save state → kill workers → re-launch. Exit code 42 |
| `/status` | Show active workers, task queue, budget breakdown |
| `/evolve` | Toggle autonomous evolution mode on/off |
| `/review` | Queue a deep self-review task |
| `/bg` | Background consciousness: `/bg start`, `/bg stop`, `/bg status` |

---

## Useful Commands

```bash
# Run all tests (69 files)
make test
# Verbose:
make test-v
# Or directly:
python3 -m pytest tests/ -q --tb=short

# Codebase health check
make health

# Run server (development)
python server.py
# → http://127.0.0.1:8765

# Clean caches
make clean

# Inspect agent's repo (manual)
cd ~/Ouroboros/repo && git log --oneline -10

# View budget/state
python3 -c "import json; print(json.dumps(json.load(open('$HOME/Ouroboros/data/state/state.json')), indent=2))"
```

---

## Key Config Values

All from `ouroboros/config.py` → `SETTINGS_DEFAULTS`. Override via env vars or Web UI Settings.

| Parameter | Default | Env Var |
|-----------|---------|---------|
| Main model | `anthropic/claude-opus-4.6` | `OUROBOROS_MODEL` |
| Code model | `anthropic/claude-opus-4.6` | `OUROBOROS_MODEL_CODE` |
| Light model | `anthropic/claude-sonnet-4.6` | `OUROBOROS_MODEL_LIGHT` |
| Fallback | `anthropic/claude-sonnet-4.6` | `OUROBOROS_MODEL_FALLBACK` |
| Claude Code model | `opus` | `CLAUDE_CODE_MODEL` |
| Web search model | `gpt-5.2` | `OUROBOROS_WEBSEARCH_MODEL` |
| Total budget | `$10` | `TOTAL_BUDGET` |
| Per-task cost cap | `$20` | `OUROBOROS_PER_TASK_COST_USD` |
| Soft timeout | `600s` | `OUROBOROS_SOFT_TIMEOUT_SEC` |
| Hard timeout | `1800s` | `OUROBOROS_HARD_TIMEOUT_SEC` |
| Tool timeout | `600s` | `OUROBOROS_TOOL_TIMEOUT_SEC` |
| Max workers | `5` | `OUROBOROS_MAX_WORKERS` |
| Review enforcement | `advisory` | `OUROBOROS_REVIEW_ENFORCEMENT` |
| Review models | `openai/gpt-5.4, gemini-3.1-pro-preview, claude-opus-4.6` | `OUROBOROS_REVIEW_MODELS` |
| Scope review model | `anthropic/claude-opus-4.6` | `OUROBOROS_SCOPE_REVIEW_MODEL` |
| BG wakeup range | `30–7200s` | `OUROBOROS_BG_WAKEUP_MIN` / `_MAX` |
| BG max rounds | `5` | `OUROBOROS_BG_MAX_ROUNDS` |
| Evo cost threshold | `$0.10` | `OUROBOROS_EVO_COST_THRESHOLD` |
| Effort: task | `medium` | `OUROBOROS_EFFORT_TASK` |
| Effort: evolution | `high` | `OUROBOROS_EFFORT_EVOLUTION` |
| Effort: review | `medium` | `OUROBOROS_EFFORT_REVIEW` |
| Effort: scope review | `high` | `OUROBOROS_EFFORT_SCOPE_REVIEW` |
| Effort: consciousness | `low` | `OUROBOROS_EFFORT_CONSCIOUSNESS` |

### Paths

| Path | Default | Env Var |
|------|---------|---------|
| App root | `~/Ouroboros/` | `OUROBOROS_APP_ROOT` |
| Repo dir | `~/Ouroboros/repo/` | `OUROBOROS_REPO_DIR` |
| Data dir | `~/Ouroboros/data/` | `OUROBOROS_DATA_DIR` |
| Settings file | `~/Ouroboros/data/settings.json` | `OUROBOROS_SETTINGS_PATH` |
| PID file | `~/Ouroboros/ouroboros.pid` | `OUROBOROS_PID_FILE` |

---

## What NOT to Do

- Do not remove or weaken the **7-step evolution protocol** — it is the core self-improvement mechanism
- Do not delete `BIBLE.md` or its git history — absolute constitutional prohibition
- Do not delete `identity.md` — rewriting content is allowed, file deletion is forbidden
- Do not bypass the **4-level safety system** or remove SAFETY_CRITICAL_PATHS
- Do not commit API keys, secrets, or credentials anywhere (BIBLE constraint)
- Do not make direct LLM API calls outside `ouroboros/llm.py` (P3: LLM-First)
- Do not silently truncate context, memory, or tool results — use explicit omission notes
- Do not introduce abstract base classes, frameworks, or ORMs (P5: Minimalism)
- Do not refactor unrelated code while implementing a specific requirement
- Do not remove background consciousness, pattern register, knowledge base, task reflections, or scratchpad — these are the learning infrastructure
- Do not add inline `style=""` in JS modules — use CSS classes from `web/style.css`
- Do not introduce new hardcoded border-radius values — add to `:root` in `style.css` first
