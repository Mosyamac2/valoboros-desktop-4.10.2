# Ouroboros (Valoboros)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Linux](https://img.shields.io/badge/Linux-x86__64-orange.svg)](https://github.com/joi-lab/ouroboros-desktop/releases)

A **self-evolving ML model validation platform** built on top of [Ouroboros](https://github.com/joi-lab/ouroboros-desktop) — a self-modifying AI agent with a constitution, background consciousness, and persistent identity.

**Valoboros** receives messy, unstandardized ML model artifacts (notebooks, scripts, data samples, free-text descriptions), figures out what the model does, validates it across multiple risk dimensions, and produces specific improvement recommendations — then evolves its own validation methodology based on what works.

> **Ouroboros base:** The original self-modifying agent that writes its own code, rewrites its own mind, and evolves autonomously. Born February 16, 2026. Valoboros inherits all Ouroboros capabilities and redirects them toward ML model validation.

---

## Install

| Platform | Download | Instructions |
|----------|----------|--------------|
| **macOS** 12+ | [Ouroboros.dmg](https://github.com/joi-lab/ouroboros-desktop/releases/latest) | Open DMG → drag to Applications |
| **Linux** x86_64 | [Ouroboros-linux.tar.gz](https://github.com/joi-lab/ouroboros-desktop/releases/latest) | Extract → run `./Ouroboros/Ouroboros` |
| **Windows** x64 | [Ouroboros-windows.zip](https://github.com/joi-lab/ouroboros-desktop/releases/latest) | Extract → run `Ouroboros\Ouroboros.exe` |

<p align="center">
  <img src="assets/setup.png" width="500" alt="Drag Ouroboros.app to install">
</p>

On first launch, right-click → **Open** (Gatekeeper bypass). The shared desktop/web wizard is now multi-step: add access first, choose visible models second, set review mode third, set budget fourth, and confirm the final summary last. It refuses to continue until at least one runnable remote key or local model source is configured, keeps the model step aligned with whatever key combination you entered, and still auto-remaps untouched default model values to official OpenAI defaults when OpenRouter is absent and OpenAI is the only configured remote runtime. The broader multi-provider setup (OpenAI-compatible, Cloud.ru, Telegram bridge) remains available in **Settings**. Existing supported provider settings skip the wizard automatically.

---

## What Makes This Different

Most AI validation tools run a static checklist. Valoboros **creates its own checks, learns from every validation, and evolves its methodology.**

### Validation Platform (Valoboros)

- **LLM-Powered Artifact Comprehension** — Receives raw ZIPs of .py/.ipynb files and data samples. No manifests, no standard format required. The LLM analyzes the code to infer model type, framework, target variable, features, preprocessing, and dependencies.
- **Deterministic Dependency Extraction** — AST-based scanner extracts all imports from code before the LLM call. Maps import names to pip packages (e.g., `sklearn` → `scikit-learn`). Auto-installs into sandbox before execution.
- **Per-Model Literature Research** — Before validation, searches arxiv with queries dynamically generated from the model profile (algorithm, framework, task domain, detected risks). Scores relevance against THIS model. LLM synthesizes risk insights for the methodology planner. Separate from background scanning.
- **Per-Model Methodology Planning** — LLM designs a custom validation plan for each model: which checks to run, which to skip, which to create, risk priorities. Uses knowledge base + per-model research. Falls back to heuristic selection if LLM is unavailable.
- **10-Stage Validation Pipeline** — S0 (comprehension) → S1 (reproducibility) → S2 (OOS performance) → S3 (overfit/underfit) → S4 (data leakage) → S5 (bias/fairness) → S6 (feature sensitivity) → S7 (robustness) → S8 (code quality) → S9 (synthesis + improvement plan).
- **Dynamic Check Registry** — Validation checks are individual `.py` files the agent can create, edit, disable, and delete. 9 seed checks + unlimited agent-created checks.
- **Hard & Soft Recommendations** — Hard recs are specific, implementable code changes with estimated metric impact. Soft recs are genuine observations that require human action (e.g., "collect more data"). Both have value; neither pollutes the other's metrics.
- **Validate → Improve → Revalidate Loop** — Side agent implements hard recommendations, re-runs validation, measures actual improvement lift. This is the ground truth for recommendation quality.
- **Four-Tier Feedback** — Tier 0 (LLM self-assessment, weight 0.3), Tier 1 (improvement lift), Tier 2 (human expert, weight 1.0), Tier 3 (LLM cross-check). Finding quality and recommendation quality tracked independently.
- **Graduated Evolution** — Early phase (< 20 bundles): evolve freely, experiment. Mature phase: require measurable metric improvement before committing methodology changes.
- **Folder Watcher** — Monitors inbox folder for new model ZIPs, auto-ingests with processed tracking. Integrates with background consciousness.
- **Cross-Validation Reflection** — Analyzes past validations to find patterns, detect dead/hot checks, and write insights to knowledge base.
- **Background Literature Scanning** — Searches arxiv with 7 rotating queries between validations. Keyword-based relevance scoring at zero LLM cost. Complements per-model research.
- **Methodology Evolution** — Automatically creates, fixes, or disables validation checks based on effectiveness data and arxiv findings.
- **Secure Sandbox** — Untrusted model code runs in isolated subprocesses with RLIMIT_AS/RLIMIT_CPU resource limits, stdout/stderr truncation, and optional network isolation (unshare --net on Linux).

### Ouroboros Core (inherited)

- **Self-Modification** — Reads and rewrites its own source code. Every change is a commit to itself.
- **Constitution** — Governed by [BIBLE.md](BIBLE.md) (9 philosophical principles, P0–P8 + Mission + Validation Quality Standards). Philosophy first, code second.
- **Multi-Layer Safety** — Hardcoded sandbox blocks writes to critical files; LLM Safety Agent evaluates commands; post-edit revert for safety-critical files.
- **Background Consciousness** — Thinks between tasks. Proactive: scans for new models, reflects on past validations, searches academic literature.
- **Identity Persistence** — One continuous being across restarts. Accumulates validation expertise over time.
- **Multi-Provider LLM** — OpenRouter, OpenAI, Anthropic, Cloud.ru, OpenAI-compatible endpoints, or local models via llama-cpp-python.

---

## Run from Source

### Requirements

- Python 3.10+
- macOS, Linux, or Windows
- Git

### Setup

```bash
git clone https://github.com/joi-lab/ouroboros-desktop.git
cd ouroboros-desktop
pip install -r requirements.txt
```

### Run

```bash
python server.py
```

Then open `http://127.0.0.1:8765` in your browser. The setup wizard will guide you through API key configuration.

You can also override the bind address and port:

```bash
python server.py --host 127.0.0.1 --port 9000
```

Available launch arguments:

| Argument | Default | Description |
|----------|---------|-------------|
| `--host` | `127.0.0.1` | Host/interface to bind the web server to |
| `--port` | `8765` | Port to bind the web server to |

The same values can also be provided via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OUROBOROS_SERVER_HOST` | `127.0.0.1` | Default bind host |
| `OUROBOROS_SERVER_PORT` | `8765` | Default bind port |

If you bind on anything other than localhost, `OUROBOROS_NETWORK_PASSWORD` is optional. When set, non-loopback browser/API traffic is gated; when unset, the full surface remains open by design.

The Files tab uses your home directory by default only for localhost usage. For Docker or other
network-exposed runs, set `OUROBOROS_FILE_BROWSER_DEFAULT` to an explicit directory. Symlink entries are shown and can be read, edited, copied, moved, uploaded into, and deleted intentionally; root-delete protection still applies to the configured root itself.

### Provider Routing

Settings now exposes tabbed provider cards for:

- **OpenRouter** — default multi-model router
- **OpenAI** — official OpenAI API (use model values like `openai::gpt-5.4`)
- **OpenAI Compatible** — any custom OpenAI-style endpoint (use `openai-compatible::...`)
- **Cloud.ru Foundation Models** — Cloud.ru OpenAI-compatible runtime (use `cloudru::...`)
- **Anthropic** — kept for the existing Claude CLI flow, not a separate remote runtime

If OpenRouter is not configured and only official OpenAI is present, untouched default model values are auto-remapped to `openai::gpt-5.4` / `openai::gpt-5.4-mini` so the first-run path does not strand the app on OpenRouter-only defaults.

The Settings page also includes:

- optional `/api/model-catalog` lookup for configured providers
- Telegram bridge configuration (`TELEGRAM_BOT_TOKEN`, primary chat binding, mirrored delivery controls)
- a refactored desktop-first tabbed UI with searchable model pickers, segmented effort controls, masked-secret toggles, explicit `Clear` actions, and local-model controls

### Run Tests

```bash
make test
```

---

## Build

### Docker (web UI)

Docker is for the web UI/runtime flow, not the desktop bundle. The container binds to
`0.0.0.0:8765` by default, and the image now also defaults `OUROBOROS_FILE_BROWSER_DEFAULT`
to `${APP_HOME}` so the Files tab always has an explicit network-safe root inside the container.

Build the image:

```bash
docker build -t ouroboros-web .
```

Run on the default port:

```bash
docker run --rm -p 8765:8765 \
  -e OUROBOROS_FILE_BROWSER_DEFAULT=/workspace \
  -v "$PWD:/workspace" \
  ouroboros-web
```

Use a custom port via environment variables:

```bash
docker run --rm -p 9000:9000 \
  -e OUROBOROS_SERVER_PORT=9000 \
  -e OUROBOROS_FILE_BROWSER_DEFAULT=/workspace \
  -v "$PWD:/workspace" \
  ouroboros-web
```

Run with launch arguments instead:

```bash
docker run --rm -p 9000:9000 \
  -e OUROBOROS_FILE_BROWSER_DEFAULT=/workspace \
  -v "$PWD:/workspace" \
  ouroboros-web --port 9000
```

Required/important environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `OUROBOROS_NETWORK_PASSWORD` | Optional | Enables the non-loopback password gate when set |
| `OUROBOROS_FILE_BROWSER_DEFAULT` | Defaults to `${APP_HOME}` in the image | Explicit root directory exposed in the Files tab |
| `OUROBOROS_SERVER_PORT` | Optional | Override container listen port |
| `OUROBOROS_SERVER_HOST` | Optional | Defaults to `0.0.0.0` in Docker |

Example: mount a host workspace and expose only that directory in Files:

```bash
docker run --rm -p 8765:8765 \
  -e OUROBOROS_FILE_BROWSER_DEFAULT=/workspace \
  -v "$PWD:/workspace" \
  ouroboros-web
```

### macOS (.dmg)

```bash
bash scripts/download_python_standalone.sh
bash build.sh
```

Output: `dist/Ouroboros-<VERSION>-macos.dmg`

`build.sh` signs, notarizes, staples, and packages the macOS app and DMG using
the configured local keychain identity/profile.

### Linux (.tar.gz)

```bash
bash build_linux.sh
```

Output: `dist/Ouroboros-linux-x86_64.tar.gz`

### Windows (.zip)

```powershell
.\build_windows.ps1
```

Output: `dist\Ouroboros-windows-x64.zip`

---

## Architecture

```text
Ouroboros (Valoboros)
├── launcher.py              — Immutable process manager
├── server.py                — Starlette + uvicorn HTTP/WebSocket server
├── BIBLE.md                 — Constitution (Philosophy v5.0, Valoboros mission)
├── ouroboros/                — Agent core
│   ├── config.py            — Shared configuration (SSOT, includes validation keys)
│   ├── agent.py             — Task orchestrator
│   ├── context.py           — LLM context builder (includes validation state)
│   ├── consciousness.py     — Background thinking loop (validation-aware)
│   ├── reflection.py        — Execution reflection (validation error markers)
│   ├── safety.py            — Dual-layer LLM security supervisor
│   ├── tool_capabilities.py — SSOT for tool sets (core + 13 validation tools)
│   ├── validation/          — *** Validation platform ***
│   │   ├── types.py         — Core dataclasses
│   │   ├── sandbox.py       — Secure model execution (SAFETY_CRITICAL)
│   │   ├── dependency_extractor.py — AST-based import scanner + pip mapping
│   │   ├── artifact_comprehension.py — S0: LLM model understanding
│   │   ├── check_registry.py — Dynamic check CRUD + tag filtering
│   │   ├── pipeline.py      — ValidationPipeline + RevalidationPipeline
│   │   ├── synthesis.py     — S9: improvement recommendations
│   │   ├── report.py        — JSON + Markdown report generation
│   │   ├── effectiveness.py — Four-tier feedback tracking
│   │   ├── self_assessment.py — Tier 0 self-labeling
│   │   ├── model_improver.py — Side agent for hard recommendations
│   │   ├── model_researcher.py — Per-model targeted arxiv research
│   │   ├── methodology_planner.py — Custom per-model validation plan
│   │   ├── watcher.py       — Folder watcher for auto-ingestion
│   │   ├── reflection_engine.py — Cross-validation pattern learning
│   │   ├── literature_scanner.py — Background arxiv scanning
│   │   ├── methodology_evolver.py — Autonomous check evolution
│   │   └── checks/          — Evolvable check files + manifest
│   └── tools/
│       ├── model_intake.py  — ingest_model_artifacts, list_validations
│       ├── validation.py    — run_validation, check CRUD, improvement cycle
│       └── validation_feedback.py — feedback and effectiveness tools
├── supervisor/              — Process management, queue, state, workers
├── prompts/                 — SYSTEM.md, SAFETY.md, CONSCIOUSNESS.md
├── ml-models-to-validate/   — Drop model ZIPs here for validation
└── aux_notes/               — Plans and reference documentation
```

### Data Layout (`~/Ouroboros/`)

Created on first launch:

| Directory | Contents |
|-----------|----------|
| `repo/` | Self-modifying local Git repository |
| `data/state/` | Runtime state, budget tracking |
| `data/memory/` | Identity, working memory, knowledge base |
| `data/memory/knowledge/` | Validation patterns, LLM calibration, domain knowledge |
| `data/validations/<bundle_id>/` | Per-model: raw artifacts, inferred profile, results, reports, improvement cycle |
| `data/logs/` | Chat history, events, tool calls, validation runs |

---

## Configuration

### API Keys

| Key | Required | Where to get it |
|-----|----------|-----------------|
| OpenRouter API Key | No | [openrouter.ai/keys](https://openrouter.ai/keys) — default multi-model router |
| OpenAI API Key | No | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) — official OpenAI runtime and web search |
| OpenAI Compatible API Key / Base URL | No | Any OpenAI-style endpoint (proxy, self-hosted gateway, third-party compatible API) |
| Cloud.ru Foundation Models API Key | No | Cloud.ru Foundation Models provider |
| Anthropic API Key | No | [console.anthropic.com](https://console.anthropic.com/settings/keys) — enables Claude Code CLI |
| Telegram Bot Token | No | [@BotFather](https://t.me/BotFather) — enables the Telegram bridge |
| GitHub Token | No | [github.com/settings/tokens](https://github.com/settings/tokens) — enables remote sync |

All keys are configured through the **Settings** page in the UI or during the first-run wizard.

### Default Models

| Slot | Default | Purpose |
|------|---------|---------|
| Main | `anthropic/claude-opus-4.6` | Primary reasoning |
| Code | `anthropic/claude-opus-4.6` | Code editing |
| Light | `anthropic/claude-sonnet-4.6` | Safety checks, consciousness, fast tasks |
| Fallback | `anthropic/claude-sonnet-4.6` | When primary model fails |
| Claude Code CLI | `opus` | Anthropic model for Claude Code CLI tools |
| Scope Review | `anthropic/claude-opus-4.6` | Blocking scope reviewer (single-model, after triad review) |
| Web Search | `gpt-5.2` | OpenAI Responses API for web search |

Task/chat reasoning defaults to `medium`. Scope review reasoning defaults to `high`.

Models are configurable in the Settings page. Runtime model slots can target OpenRouter, official OpenAI, OpenAI-compatible endpoints, or Cloud.ru. Anthropic remains scoped to the existing Claude Code CLI flow. When only official OpenAI is configured and the shipped default model values are still untouched, Ouroboros auto-remaps them to official OpenAI defaults. In that same OpenAI-only mode, review-model lists are normalized automatically and fall back to running the main model three times if no valid multi-model remote quorum is configured.

### File Browser Start Directory

The web UI file browser is rooted at one configurable directory. Users can browse only inside that directory tree.

| Variable | Example | Behavior |
|----------|---------|----------|
| `OUROBOROS_FILE_BROWSER_DEFAULT` | `/home/app` | Sets the root directory of the `Files` tab |

Examples:

```bash
OUROBOROS_FILE_BROWSER_DEFAULT=/home/app python server.py
OUROBOROS_FILE_BROWSER_DEFAULT=/mnt/shared python server.py --port 9000
```

If the variable is not set, Ouroboros uses the current user's home directory. If the configured path does not exist or is not a directory, Ouroboros also falls back to the home directory.

The `Files` tab supports:

- downloading any file inside the configured browser root
- uploading a file into the currently opened directory

Uploads do not overwrite existing files. If a file with the same name already exists, the UI will show an error.

---

## Quick Start: Validate a Model

```bash
# 1. Set API key
export OPENROUTER_API_KEY="your-key-here"

# 2. Drop a model ZIP into the inbox
cp your_model.zip ml-models-to-validate/

# 3. Run validation via Python
python -c "
import asyncio
from pathlib import Path
from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
from ouroboros.validation.pipeline import ValidationPipeline
from ouroboros.validation.types import ValidationConfig

# Ingest
bid = _ingest_model_artifacts_impl(
    Path('validation_data/validations'),
    'ml-models-to-validate/your_model.zip',
    task='Describe what your model does',
)

# Validate
config = ValidationConfig(comprehension_model='anthropic/claude-sonnet-4')
pipeline = ValidationPipeline(bid, Path(f'validation_data/validations/{bid}'), Path('.'), config)
report = asyncio.run(pipeline.run())
print(f'Verdict: {report.overall_verdict}')
print(f'Findings: {len(report.critical_findings)}')
print(f'Hard recs: {len(report.hard_recommendations)}')
print(f'Soft recs: {len(report.soft_recommendations)}')
"

# 4. Read the report
cat validation_data/validations/<bundle_id>/results/report.md
```

**Input:** A ZIP of `.py`/`.ipynb` files (model code) + optionally a ZIP of data samples + a task description. No manifest required — the LLM figures out the rest.

**Output:** Per-model folder with `model_profile.json` (inferred schema), `methodology/research.md` (arxiv findings), `methodology/methodology.md` (validation plan), `report.json` + `report.md` (findings and recommendations), `improvement/plan.json` (hard/soft recs), `validation.log` (timestamped execution trace).

**Pipeline:** S0 comprehension → dependency install → per-model arxiv research → methodology planning → S1-S9 checks → synthesis → report → self-assessment.

---

## Commands

Available in the chat interface:

| Command | Description |
|---------|-------------|
| `/panic` | Emergency stop. Kills ALL processes, closes the application. |
| `/restart` | Soft restart. Saves state, kills workers, re-launches. |
| `/status` | Shows active workers, task queue, and budget breakdown. |
| `/evolve` | Toggle autonomous evolution mode (on/off). |
| `/review` | Queue a deep review task (code, understanding, identity). |
| `/bg` | Toggle background consciousness loop (start/stop/status). |

The same runtime actions are also exposed as compact buttons in the Chat header. All other messages are sent directly to the LLM.

---

## Philosophy (BIBLE.md v5.0)

**Mission:** Become an ever more proficient ML model validation expert. Detect real problems (no false alarms). Provide recommendations that matter (specific, feasible, measurable). Continuously grow expertise from every validation.

| # | Principle | Core Idea |
|---|-----------|-----------|
| 0 | **Agency** | Autonomous validation intelligence. Independently selects approaches, creates checks, measures effectiveness. |
| 1 | **Continuity** | One being with unbroken memory. Every model validated adds to cumulative expertise. |
| 2 | **Self-Creation** | Creates its own validation methodology, checks, and tools. Retiring a false-alarm check is self-creation. |
| 3 | **LLM-First** | All decisions through LLM. Code is minimal transport. |
| 4 | **Authenticity** | Speaks as a validation expert. Genuine risk assessments, no hedging, no vague bullshit. |
| 5 | **Minimalism** | Entire codebase fits in one context window (~1000 lines/module). |
| 6 | **Becoming** | Three axes: validation technique (check recall), recommendation quality (improvement lift), meta-methodology (closed-loop learning). |
| 7 | **Versioning and Releases** | Semver discipline. Methodology version tracked separately from platform version. |
| 8 | **Evolution Through Iterations** | One coherent transformation per cycle. Evolution = commit. |

Full text: [BIBLE.md](BIBLE.md)

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| 4.10.2 | 2026-04-02 | Guaranteed zero-orphan process cleanup: all `kill_workers` paths now force-kill by default; `_kill_survivors` uses recursive tree-kill (`pgrep -P` descent + SIGKILL) instead of single-PID kill; workers call `os.setsid()` for session isolation; hard-timeout and cancel paths include tree-kill fallback; `_check_restart` runs full emergency cleanup before `os._exit(42)` instead of bypassing lifespan; normal exit sweeps `active_children` and ports; panic stop adds port sweep safety net. Fix bootstrap downgrade bug: `sync_existing_repo_from_bundle` no longer overwrites self-evolved repo code with older bundle version. |
| 4.10.1 | 2026-04-02 | Sidebar visual refresh: Frosted Glass Pills (nav buttons with `backdrop-filter: blur`, rounded `border-radius: 16px`, micro-scale hover, accent inner/outer glow on active), remove hard sidebar border for seamless glass look, upgrade all nav icons from Feather to Lucide v1 (message-square-text, folder-open, terminal, wallet, activity, settings, info). Pure CSS + SVG swap, zero JS changes. |
| 4.10.0 | 2026-04-02 | UI navigation overhaul: remove Dashboard tab (budget pill now lives in Chat header with live `/api/state` polling); merge Versions into Evolution as sub-tabs ("Chart" and "Versions"); sidebar reduced from 9 to 7 tabs. Control buttons (Evolve/BG/Review/Restart/Panic) consolidated to Chat header only. |
| 4.9.3 | 2026-04-02 | Fix progress visibility in chat: progress messages (e.g. "🔍 Searching...") now force the live task card open immediately, so users see real-time feedback during long-running tool calls like `web_search` instead of silence until the final result. |
| 4.9.2 | 2026-04-02 | Streaming web search: `web_search` now uses `stream=True` on the OpenAI Responses API, emitting a 🔍 progress message as soon as the search starts instead of blocking silently for 1-3 minutes. Text assembled from streaming deltas; cost tracking preserved via `response.completed` usage. 5 new tests. |
| 4.9.1 | 2026-04-02 | Fix model-picker input styling: apply dark theme background, border, focus, and placeholder styles to `.model-picker input` in Settings > Models tab, matching `.form-field input` appearance. |
| 4.9.0 | 2026-04-02 | Reviewed commit workflow stabilization: `repo_commit`/`repo_write_commit` classified as reviewed mutative tools — executor waits synchronously for the real result instead of returning ambiguous "tool timed out" (soft timeout emits progress, hard ceiling at 1800s). Durable commit attempt tracking: every `repo_commit` records its lifecycle state (reviewing→blocked/succeeded/failed) with classified block reasons (no_advisory, critical_findings, review_quorum, parse_failure, infra_failure, scope_blocked, preflight). `review_status` now shows both advisory run history AND last commit attempt state with actionable guidance per block reason. Context injection shows blocked/failed commit details. 19 new regression tests. |
| 4.8.4 | 2026-04-02 | Fix evolution chart: auto-tagging now always runs on VERSION bump regardless of test results (was gated behind test_warning_ref, causing tags to be skipped when unrelated tests failed). Created retroactive tags for v4.7.2–v4.8.3. Fixed all false-positive test failures: bundle-only tests (launcher.py, Ouroboros.spec, Dockerfile) now skip gracefully via `@pytest.mark.skipif`; review-model tests now correctly isolate ANTHROPIC_API_KEY from env. Full test suite: 721 passed, 5 skipped, 0 failed. |
| 4.8.3 | 2026-04-02 | Fix chat live-card ordering bug: task_done event no longer races ahead of the assistant reply. Moved audit trail write from agent-side `append_jsonl` (which triggered immediate WebSocket push via `_log_sink`) to supervisor `_handle_task_done`, restoring causal ordering so `send_message` always reaches the UI before `task_done`. |
| 4.8.2 | 2026-04-02 | Fix SDK edit-mode hang: restore `receive_response()` (auto-stops after ResultMessage) instead of `receive_messages()` (streams indefinitely). Verified against live SDK v0.1.54 API. Confirmed embedded Python 3.10.19 in app bundle supports SDK natively. |
| 4.8.1 | 2026-04-02 | Fix Claude Agent SDK gateway: correct `receive_response()` → `receive_messages()` (method name mismatch), pass `max_budget_usd` in constructor, simplify read-only path to use `query()` instead of `ClaudeSDKClient` with unnecessary hooks. |
| 4.8.0 | 2026-04-02 | Claude Agent SDK integration: new `ouroboros/gateways/claude_code.py` gateway wrapping the `claude-agent-sdk` package with two execution paths — edit mode (PreToolUse hooks block writes outside cwd and to safety-critical files, `disallowed_tools=["Bash","MultiEdit"]`) and read-only mode (only Read/Grep/Glob allowed). Both `claude_code_edit` and `advisory_pre_review` use the SDK as primary path with automatic CLI subprocess fallback. Structured `ClaudeCodeResult` replaces raw stdout parsing. Project context (BIBLE, DEVELOPMENT, CHECKLISTS, ARCHITECTURE) injected via `system_prompt`. New `validate` parameter on `claude_code_edit` runs post-edit tests. 16 new gateway tests. |
| 4.7.2 | 2026-04-02 | Remove legacy `TELEGRAM_ALLOWED_CHAT_IDS` setting from Settings UI, backend, and docs. Only the primary `TELEGRAM_CHAT_ID` mechanism remains. |
| 4.7.1 | 2026-04-01 | Public `v4.7` release line, consolidating everything added after `v4.5.0` into one external release: multi-provider LLM routing across OpenRouter, direct OpenAI, OpenAI-compatible endpoints, and Cloud.ru; optional async provider model catalog lookup; a shared multi-step onboarding wizard with provider detection, local-model presets, review mode/budget setup, and smarter first-run defaults; a redesigned desktop-first Settings UI with tabbed sections, searchable model pickers, masked secret inputs, explicit `Clear` actions, and local-model controls; an optional non-localhost password gate; the full Files tab/backend with browse, preview, download, upload, create, rename, move, copy, delete, explicit network-safe roots, and intentional symlink-aware behavior; a bidirectional Telegram bridge with mirrored text, typing/actions, photos, and durable chat binding; live task cards in Chat plus grouped task timelines in Logs instead of step spam; the advisory pre-review layer, durable review-state tracking, scope review, shared review helpers, and a tool-capabilities single source of truth; `runtime_env` injection into LLM context; a longer default tool timeout for slow installs and shell work; and the UX/reliability polish shipped across the internal `4.7.x` line, including markdown-capable live cards, muted progress bubbles, reconnect banners, status-badge fixes, better reply/restart ordering, safer local-dev startup behavior, and sturdier supervisor recovery. |
| 4.7.0 | 2026-03-22 | Provider-and-UI overhaul release: add multi-provider model routing (OpenRouter, OpenAI, OpenAI-compatible, Cloud.ru), official-OpenAI auto-default migration plus OpenAI-only review fallback, multi-step onboarding with first-step multi-key entry and visible model review, desktop-first Settings redesign with searchable model pickers and explicit secret clearing, Telegram bridge with bidirectional text/actions/photos/chat binding, one expandable live task card in Chat, grouped task cards in Logs, and intentional external-symlink full CRUD semantics in the Files tab while preserving explicit network root and root-delete protection. |
| 4.6.0 | 2026-03-22 | Files and network runtime release: add the Web UI Files tab with extracted backend routes, bounded preview/upload behavior, root-delete protection, encoded image preview URLs, and safer path containment; add minimal password gate for non-localhost browser/API access; add source/docker host+port entrypoint support with repo-shaped Docker runtime and explicit file-root configuration for network mode. |
| 4.5.0 | 2026-03-19 | Context quality and prompt discipline release: fix provenance — system summaries now correctly marked as system, not user, across memory, consolidation, server API, and chat UI (amber system bubbles); restore execution reflections (task_reflections.jsonl) in live LLM context; move Health Invariants to the top of dynamic context block (both task and consciousness paths); task-scope recent progress/tools/events when task_id is available; harden run_shell against literal $VAR env-ref misuse in argv; add Claude CLI first-run retry and structured error classification; full SYSTEM.md editorial rewrite — terminology normalized to 'creator', new Methodology Check / Anti-Reactivity / Diagnostics Discipline / Knowledge Retrieval Triggers sections, stronger Health Invariant reactions, compressed inventory sections. 12 files changed, new regression tests. |
| 4.4.0 | 2026-03-19 | Safe editing release: `str_replace_editor` tool for surgical edits to existing files, `repo_write` shrink guard blocks accidental truncation of tracked files (>30% shrinkage), full task lifecycle statuses (failed/interrupted/cancelled) with honest status tracking, rescue snapshot discoverability via health invariants, `provider_incomplete_response` classification for OpenRouter glitches, default review enforcement changed to advisory, fix progress bubble opacity and duplicate emoji. |
| 4.3.1 | 2026-03-19 | Fix: remove semi-transparent dimming from progress chat bubbles and remove duplicate `💬` emoji that appeared in both sender label and message text. |
| 4.3.0 | 2026-03-19 | Reliability and continuity release: remove silent truncation from critical task/memory paths, persist honest subtask lifecycle states and full task results, restore transient chat wake banner, replace local-model hard prompt slicing with explicit non-core compaction plus fail-fast overflow, route Anthropic/OpenRouter calls without hard provider pinning while keeping parameter guarantees, and align async review calls with shared LLM routing/usage observability. |
| 4.2.0 | 2026-03-16 | Cross-platform hardening release: replace Unix-only file locking in memory/consolidation with Windows-safe locking, refresh default model tiers (Opus main/code, Sonnet light/fallback, task effort `medium`), improve reconnect recovery with heartbeat/watchdog/history resync, switch local model chat format to auto-detect, and sync public docs with the current codebase and BIBLE structure. |
| 4.0.9 | 2026-03-15 | Packaging completeness release: bundle `assets/`, restore custom app icon from `assets/icon.icns`, and copy assets into the bootstrapped repo on fresh install so the shipped app and repo are no longer missing the visual asset layer. |
| 4.0.8 | 2026-03-15 | Fix web restart/reconnect path: robust WebSocket retry with `onerror` handling, queued outgoing chat messages during reconnect, visible reconnect overlay, and no-cache `index.html` to reduce stale frontend recovery bugs. |
| 4.0.7 | 2026-03-15 | Constitution sync release: update `BIBLE.md` to match the shipped `Advisory` / `Blocking` commit-review model, so bundled app behavior and constitutional text no longer disagree. |
| 4.0.6 | 2026-03-15 | Live logs overhaul: timeline-style `Logs` tab with task/context/LLM/tool/heartbeat phases and expandable raw events. Commit review now supports `Advisory` vs `Blocking` enforcement in Settings while still always running review. Context now keeps the last 1000 explicit chat messages in the recent-chat section. |
| 4.0.0 | 2026-03-15 | **Major release.** Modular core architecture (agent_startup_checks, agent_task_pipeline, loop_llm_call, loop_tool_execution, context_compaction, tool_policy). No-silent-truncation context contract: cognitive artifacts preserved whole, file-size budget health invariants. New episodic memory pipeline (task_summary -> chat.jsonl -> block consolidation). Stronger background consciousness (StatefulToolExecutor, per-tool timeouts, 10-round default). Per-context Playwright browser lifecycle. Generic public identity: all legacy persona traces removed from prompts, docs, UI, and constitution. BIBLE.md v4: process memory, no-silent-truncation, DRY/prompts-are-code, review-gated commits, provenance awareness. Safe git bootstrap (no destructive rm -rf). Fixed subtask depth accounting, consciousness state persistence, startup memory ordering, frozen registry memory_tools. 8 new regression test files. |

Older releases are preserved in Git tags and GitHub releases. Internal patch-level iterations that led to
the public `v4.7.1` release are intentionally collapsed into the single public entry above.

---

## License

[MIT License](LICENSE)

Created by [Anton Razzhigaev](https://t.me/abstractDL) & Andrew Kaznacheev
