# Plan: Rewire Valoboros LLM Backend to Claude Code OAuth (Subscription)

**Status:** Draft — for review. No code changes yet.
**Author:** Planning pass on 2026-05-16.
**Goal:** Route as much of Valoboros's LLM traffic as possible through the **Claude Code CLI / `claude-agent-sdk`** using an Anthropic **OAuth subscription token** (`CLAUDE_CODE_OAUTH_TOKEN`) instead of pay-per-token API keys (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). Motivation: a fixed-price Claude subscription is dramatically cheaper than per-token billing for an agent that calls the LLM hundreds of times per evolution cycle.

This plan is descriptive — it does NOT modify project source. Outputs go to `aux_notes/` per Task Guardrails in `CLAUDE.md`.

---

## 1. TL;DR

* The project already has a **`claude-agent-sdk` gateway** at `ouroboros/gateways/claude_code.py` (used by `claude_code_edit` and `advisory_pre_review`). When `CLAUDE_CODE_OAUTH_TOKEN` is set in the environment, the underlying `claude` CLI will bill against the subscription instead of the API key — *for those two tools only*.
* **Everything else** (the main agent loop, safety supervisor, multi-model review, scope review, validation S0/S2/S9, consciousness, reflection, vision, web search) currently goes through `ouroboros/llm.py` → OpenRouter/direct providers via the OpenAI-compatible chat-completions API. These calls **do not** use the OAuth token. They will continue to bill per-token until rewired.
* Rewiring requires building a **new chat-completions adapter** that fronts the Anthropic-only SDK so existing call sites can be redirected with minimal disruption, plus deliberate decisions about three call surfaces that cannot trivially become single-model-Claude-only: **multi-model review**, **web search**, and **vision**.
* The single largest constitutional risk: the **multi-model review** mechanism (one of the ten that must survive per `CLAUDE.md`'s "Ten mechanisms that must survive adaptation") today depends on diversity across model *families*. Subscription auth gives us only Anthropic. We need an answer for review diversity before the switchover lands.

---

## 2. Current LLM Architecture (Money Flow Map)

Verified by reading source. All paths below currently send money out the per-token API door.

### 2.1 Single LLM client, many callers

`ouroboros/llm.py` (1301 lines) is the only module that talks to LLM APIs. Public contract: `chat()`, `chat_async()`, `vision_query()`, `default_model()`, `available_models()`. It routes by parsing a provider prefix on the model string (`anthropic::`, `openai::`, `openrouter::`, `openai-compatible::`, `cloudru::`) or falls back to OpenRouter. Direct Anthropic uses `x-api-key`/`anthropic-version` (llm.py:1029–1072). Direct OpenRouter uses an OpenAI-compatible client against `https://openrouter.ai/api/v1` (llm.py:264–338). A separate local `llama-cpp-python` path is supported (llm.py:560–646).

`LLMClient` is instantiated in **18 places** (search above). Notable call sites:

| Call site | Purpose | Tool calls? | Multi-model? |
|---|---|---|---|
| `ouroboros/agent.py:91` | Main agent (held by orchestrator) | yes | no |
| `ouroboros/loop_llm_call.py:88` | Main loop one-step LLM call (the hot path) | yes | no |
| `ouroboros/consciousness.py:65,271` | Background introspection daemon | yes (whitelist) | no |
| `ouroboros/safety.py:140,149,208` | 2-pass safety supervisor (light + heavy) | no | no |
| `ouroboros/reflection.py:180,290` | Post-error reflections | no | no |
| `ouroboros/agent_task_pipeline.py:155,302` | Task summary + reflection async work | no | no |
| `ouroboros/consolidator.py:302,350,648,753` | Dialogue → memory consolidation | no | no |
| `ouroboros/context_compaction.py:204` | Mid-task tool-result compaction | no | no |
| `ouroboros/tools/core.py:423` | `compact_context` tool | no | no |
| `ouroboros/tools/control.py:235` | `available_models()` for `switch_model` tool | n/a | n/a |
| `ouroboros/tools/review.py:188` | `multi_model_review` tool (heart of pre-commit gate) | no | **YES — 3 families** |
| `ouroboros/tools/scope_review.py:296` | Single-model scope review after triad | no | no |
| `ouroboros/tools/vision.py:34` (factory) | `analyze_screenshot`, `vlm_query` | no | no |
| `ouroboros/validation/artifact_comprehension.py:253` | S0: model comprehension | no | no |
| `ouroboros/validation/methodology_planner.py:136` | Per-model qual/quant plan | no | no |
| `ouroboros/validation/synthesis.py:164` | S9: hard/soft recommendations | no | no |
| `ouroboros/validation/self_assessment.py:82` | Tier-0 self-rating | no | no |
| `ouroboros/validation/model_researcher.py:464` | Per-model arxiv distillation | no | no |
| `ouroboros/validation/model_improver.py:142` | Side-agent that implements hard recs | no | no |
| `ouroboros/validation/report.py:186` | JSON+MD report writer | no | no |

### 2.2 Non-LLMClient API call sites (also burn API budget)

* `ouroboros/tools/search.py` — calls **OpenAI Responses API** directly for `web_search` (gpt-5.2). Bypasses `LLMClient`. Requires `OPENAI_API_KEY`.
* `ouroboros/llm.py:152` — `fetch_openrouter_pricing()` hits OpenRouter's public catalog (no auth, harmless).
* `ouroboros/tools/shell.py:812` (`claude_code_edit`) — uses the gateway and accepts the OAuth token already; falls back to CLI subprocess with `ANTHROPIC_API_KEY` if SDK absent.
* `ouroboros/tools/claude_advisory_review.py:197` (`advisory_pre_review`) — same as above.

### 2.3 What the "OAuth subscription" path already supports

The `claude` CLI auth precedence (relevant for both the SDK and the legacy CLI fallback):

1. `CLAUDE_CODE_OAUTH_TOKEN` — generated via `claude setup-token`. Bills against the **Pro/Max subscription** at flat rate. Rate-limited per Anthropic's tier rules.
2. `ANTHROPIC_API_KEY` — bills per-token via the Anthropic API.
3. Interactive `claude login` credentials file at `~/.claude/credentials.json` (desktop only).

**Critical:** if BOTH `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY` are set, the CLI prefers the API key (per current Anthropic behavior — verify before relying). The plan must **unset `ANTHROPIC_API_KEY`** in any process that fronts the SDK, otherwise we keep paying per-token silently.

Today `ouroboros/tools/shell.py:822` outright refuses to run `claude_code_edit` if `ANTHROPIC_API_KEY` is empty — that gate must be widened to also accept `CLAUDE_CODE_OAUTH_TOKEN`.

---

## 3. What Subscription Auth Gives Us — and What It Takes Away

### Gains

* Effectively flat cost. Heavy evolution / consciousness loops become affordable.
* Same Claude Opus 4.6 / Sonnet 4.6 / Haiku 4.5 quality.
* Built-in tools the SDK already supports: **WebSearch**, **Read**, **Grep**, **Glob**, **Edit**, **Bash**, **MultiEdit**. We can lean on Claude's WebSearch instead of OpenAI's Responses API.

### Constraints

* **Anthropic-only.** No GPT, no Gemini, no Cloud.ru, no local llama-cpp variants. This collides head-on with the multi-model review architecture.
* **Rate limits** are real and bursty: per-5-hour and per-week message caps depending on tier. Evolution cycles and consciousness wakeups must be paced.
* **No `chat.completions` primitive.** The SDK is an *agent runtime* (`query()` and `ClaudeSDKClient`), not a chat-completions endpoint. To repurpose it as a "completion service" we have three options (§5).
* **Tool-use orchestration differs.** Our existing `loop.py` runs an OpenAI-style tool loop where Ouroboros owns the dispatch table. The SDK runs *its* loop and exposes user tools via MCP. Either we keep our loop and demote the SDK to "complete this text" mode, or we move our tools into MCP and let the SDK drive.
* **Cost reporting** changes: SDK still emits `total_cost_usd`, but on subscription it reports the *notional* per-token cost the request would have been at API rates — useful for forecasting, but it does not represent real money out. Budget caps lose their bite; rate-limit headroom becomes the actual constraint.
* **Single point of failure.** When Anthropic is down or you hit a weekly cap, the whole agent is down. The existing local-model fallback path becomes load-bearing.

---

## 4. Constitutional / Bible Constraints to Honor

From `BIBLE.md` (loaded as constitution) and `CLAUDE.md` §"Ten mechanisms that must survive adaptation":

1. **Multi-model review** is listed as one of the ten protected mechanisms. Stripping it down to "one Claude reviewing itself" is a constitutional change, not a refactor. Need explicit rationale and a substitute (see §6.3).
2. **P3 LLM-First** — all LLM calls go through one module. Whatever adapter we build must continue to be the *only* place LLM calls originate. No ad-hoc SDK calls scattered into tools.
3. **P5 Minimalism** — do not introduce an "AbstractLLMBackend" hierarchy. Concrete adapter + capability flags is enough.
4. **Safety-critical files** (`ouroboros/safety.py`, `ouroboros/tools/registry.py`, `prompts/SAFETY.md`, `BIBLE.md`) are 🔒🔄 — locked AND overwritten on launch from the bundle. Any change there has to be made in the *project source* and synced to the bundle, not just in `~/Ouroboros/repo`.
5. **7-step evolution protocol** continues to require: smoke test, multi-model review, Bible check, commit + restart. The review step is the one in play here.

---

## 5. Architectural Decision: Three Options

### Option A — Compatibility-Shim Chat Adapter (recommended first phase)

Build a new gateway, `ouroboros/gateways/claude_code_chat.py`, that satisfies the existing `LLMClient.chat()` / `chat_async()` contract by driving `claude-agent-sdk` under the hood:

* For each call: spin up a one-shot `query()` with `allowed_tools=[]`, `max_turns=1`, pass the messages as a single concatenated user prompt with the existing `system` block as `system_prompt`.
* Stream `AssistantMessage` text back, package into the OpenAI-shaped `{"role": "assistant", "content": "..."}` envelope.
* Map `total_cost_usd` to `usage["cost"]`. Map input/output tokens via `ResultMessage.usage`.
* Vision: pass image blocks through the SDK's `query()` input as a multi-part message (SDK supports image content blocks).
* **Limitation:** tool calls. Calls that pass `tools=[...]` (i.e. the main agent loop and consciousness) cannot use this adapter without an MCP detour. Those stay on OpenRouter/Anthropic API for now, OR we go to Option B for them later.

**Modify `LLMClient` to route by capability**, not by model prefix:
* New env var `OUROBOROS_LLM_BACKEND` ∈ {`openrouter`, `direct`, `claude_code_oauth`, `auto`}.
* In `auto` mode: if `CLAUDE_CODE_OAUTH_TOKEN` is set AND the requested model resolves to an Anthropic family member AND no `tools` arg → use the adapter; else fall back to the existing path.

**Pros:** Smallest blast radius. Captures the heavy tail (safety supervisor, validation S0/S9, reflection, summary, scope review) — all tool-less calls — on day one.
**Cons:** The hottest spender (main agent loop with tools) still bills per-token until phase 2.

### Option B — Full Migration via MCP-exposed Tools (phase 2)

Rewrite `ouroboros/loop.py` and `ouroboros/loop_tool_execution.py` to use `ClaudeSDKClient` as the agent runtime. Expose Ouroboros's 38 tools as **in-process MCP tools** using `claude_agent_sdk.create_sdk_mcp_server` and the `@tool` decorator. Claude (via the SDK) emits `tool_use` blocks, the SDK relays them to our in-process handlers, results come back, the SDK loops.

We keep all our existing surroundings (safety check, context builder, event emission, budget tracking, reflections) because they live around the loop, not inside it. The SDK replaces only the "send messages, get assistant turn" primitive plus tool dispatch.

**Pros:** Hot path moves to subscription. Caching/streaming/billing all become the SDK's problem.
**Cons:** Largest change. The current loop has accreted careful behavior around tool-result truncation, parallel/serial scheduling, partial-tool-call recovery, context compaction triggers, "seal_task_transcript" cache markers, etc. We have to validate each survives the migration.

### Option C — CLI-Subprocess Adapter

Same shape as Option A but call the `claude` binary with `-p "<prompt>" --output-format json` instead of importing the SDK. Mirrors what `claude_code_edit` already does in its fallback path. Slower (subprocess startup per call), no streaming, but trivially provider-agnostic.

**Pros:** Zero new Python deps; works even if `claude-agent-sdk` is missing.
**Cons:** Per-call overhead (subprocess fork + auth handshake). Bad for the safety supervisor that fires on every shell command.

### Recommendation

**Phase 1: Option A** for all tool-less call sites (immediate cost relief on safety/review/validation/reflection/summary/consciousness pings).
**Phase 2: Option B** for the main agent loop and consciousness (migrate the loop's tool dispatch onto SDK + MCP).
**Phase 3:** Targeted decisions on multi-model review, web search, vision (§6).
Option C remains as fallback when the SDK package is unavailable.

---

## 6. Per-Surface Decisions

### 6.1 Main agent loop (`loop.py` + `loop_llm_call.py` + `loop_tool_execution.py`)

* Phase 1: leave alone (still routes through `OUROBOROS_MODEL`, currently `anthropic/claude-opus-4.6` via OpenRouter).
* Phase 2: replace `LLMClient.chat()` inside `call_llm_with_retry()` with an SDK-based path when `OUROBOROS_LLM_BACKEND=claude_code_oauth`. Expose Ouroboros tools as MCP tools (one `@tool` per `ToolEntry.schema`). Translate SDK `tool_use` → existing `loop_tool_execution.execute_tool_calls` dispatch.
* Audit: the existing `seal_task_transcript`/`cache_control` machinery in `llm.py:407–430` is OpenRouter/Anthropic-specific — through the SDK, prompt caching is automatic and on-by-default. Strip the `cache_control` manipulation when running through SDK.

### 6.2 Safety supervisor (`safety.py`)

* Tool-less. Two calls per non-whitelisted shell command (light then heavy). High volume. **Top priority for Phase 1 redirect.**
* The supervisor needs the safety system prompt and conversation context — both translate cleanly to SDK `system_prompt` + user message.
* This module is 🔒🔄 (overwritten every launch from the bundle). The source edit must happen in the project tree AND the bundle, otherwise the launcher restore at startup wipes the change. See `launcher.py:99–103` (`sync_paths`) and `CLAUDE.md` §"Three files are both 🔒 and 🔄".

### 6.3 Multi-model review (`tools/review.py` + `config.get_review_models()`)

This is the constitutionally protected diversity gate. Choices, in order of preference:

* **Option R1 — Heterogeneous personas, single family.** Three Claude runs with distinct system prompts that simulate different reviewer perspectives (e.g., "strict P5/minimalism reviewer", "BIBLE.md compliance reviewer", "test-and-regression reviewer"). Easy to implement, weak diversity — three Claudes will tend to agree.
* **Option R2 — Hybrid: keep OpenRouter as an OPTIONAL review-only key.** If `OPENROUTER_API_KEY` is set, review goes through the existing three-family triad. If only `CLAUDE_CODE_OAUTH_TOKEN` is set, review falls back to R1. This preserves the constitutional mechanism when the user is willing to pay a tiny per-token bill (review uses ~4k tokens × 3 models × low frequency).
* **Option R3 — Drop multi-model review.** Bible-violating per `CLAUDE.md` §"Ten mechanisms" #6. Not acceptable without an explicit constitutional amendment recorded in `BIBLE.md`.

**Recommended:** R2. The user keeps a small OpenRouter or direct-Anthropic+OpenAI key just for review (and scope review, which is single-model and can go to either OAuth-Claude or the kept OpenRouter key). The cost is bounded by per-commit volume, not per-LLM-turn volume, so it stays cheap. If the user explicitly rejects keeping any per-token key, fall back to R1 and document it as a deliberate departure from the protected-mechanism list.

### 6.4 Scope review (`tools/scope_review.py`)

Single Claude opus call after the triad. Trivially redirects through the new adapter.

### 6.5 Web search (`tools/search.py`)

Currently calls **OpenAI Responses API** (`gpt-5.2`) directly. Burns OpenAI budget independently of `LLMClient`.

* **Option W1 — Replace with Claude's built-in WebSearch tool via SDK.** Make `_web_search()` call the SDK with `allowed_tools=["WebSearch"]`, `max_turns=2`, instructing Claude to search and return a synthesized answer. Bills against the subscription.
* **Option W2 — Keep OpenAI Responses path as optional.** If `OPENAI_API_KEY` is set, use the existing path; otherwise fall back to W1. Useful if the user values OpenAI's web search quality and is willing to keep that key.

**Recommended:** W1 with W2 as graceful degradation. Default to subscription-based search.

### 6.6 Vision (`tools/vision.py` → `LLMClient.vision_query()`)

Cleanly fits the SDK: pass `{"type": "image", "source": ...}` blocks in the input message. Phase 1 redirect.

### 6.7 Validation pipeline (`validation/*.py`)

S0 (comprehension), S9 (synthesis), report writer, methodology planner, model researcher, model improver, self-assessment — **all** tool-less LLM calls. Redirect via Phase 1 adapter.

The `model_improver` side-agent (`validation/model_improver.py:142`) is special: it implements hard recommendations *inside a sandbox*. If it benefits from Claude Code's `Edit`/`Bash` tools, we can let it run as a true SDK agent (Option B-shape) inside the sandbox — likely a quality win on its own merits.

### 6.8 Consciousness loop (`consciousness.py`)

Tool-using (whitelisted `_BG_TOOL_WHITELIST`). Falls under Phase 2 (Option B migration alongside the main loop).

In Phase 1 it continues to use OpenRouter/Anthropic API key — partially mitigated by lowering wakeup frequency (`OUROBOROS_BG_WAKEUP_MIN/MAX`) until Phase 2 lands.

### 6.9 Local-model fallback (`local_model.py`, `USE_LOCAL_*`)

Keep as-is. Becomes more important: when the subscription hits rate limits, falling back to a local llama-cpp model prevents total outage. Recommend adding a new env var `OUROBOROS_FALLBACK_ON_RATELIMIT=true` so that on a 429-style SDK error we automatically retry against the local model.

---

## 7. Module-by-Module Change Matrix

| File | Phase | Change |
|---|---|---|
| `ouroboros/gateways/claude_code_chat.py` | 1 | **NEW.** Chat-completions adapter wrapping `claude-agent-sdk` for tool-less calls. Implements `chat(messages, model, ...)` and `chat_async(...)`. Returns OpenAI-shaped dicts. Reports `cost=0` when running on subscription; populates `usage.cost_estimate` from notional pricing for visibility. |
| `ouroboros/llm.py` | 1 | Add `_resolve_backend()` that selects between `openrouter|direct|claude_code_oauth` based on env. In `chat()` / `chat_async()`, when the adapter is selected and no `tools` are supplied, delegate to the new gateway. Tools-present + Anthropic + `auto` → still use existing Anthropic-direct path until Phase 2. |
| `ouroboros/config.py` | 1 | Add `CLAUDE_CODE_OAUTH_TOKEN` and `OUROBOROS_LLM_BACKEND` to `SETTINGS_DEFAULTS`. Add to `apply_settings_to_env()` env-key list. In `_exclusive_direct_remote_provider_env()`, treat OAuth token as a fourth provider state so `get_review_models()` knows we have only Anthropic available. |
| `ouroboros/onboarding_wizard.py` | 1 | Add a "Claude Code Subscription" provider profile alongside `openrouter|openai|anthropic|local`. Surface a token field. Detect token via `claude /status` or env presence. Set default `OUROBOROS_LLM_BACKEND=claude_code_oauth` for this profile. |
| `ouroboros/server_runtime.py` | 1 | Acceptance check: treat `CLAUDE_CODE_OAUTH_TOKEN` as a valid auth source for "we have an LLM" gate (currently only checks API keys at lines 63–100). |
| `ouroboros/tools/shell.py:822` | 1 | Widen the gate: accept either `ANTHROPIC_API_KEY` OR `CLAUDE_CODE_OAUTH_TOKEN` for `claude_code_edit`. When OAuth is present, also clear `ANTHROPIC_API_KEY` from the subprocess env so the CLI prefers the subscription. |
| `ouroboros/tools/claude_advisory_review.py:211` | 1 | Same widening: allow OAuth token. |
| `ouroboros/safety.py:140,208` | 1 | After adapter is wired into `LLMClient`, no direct change needed here — calls auto-route. BUT this file is 🔒🔄: edits must land in the project source *and* the bundle so `launcher.py` doesn't overwrite them. If no source change is required (because routing happens in `llm.py`), this constraint is moot. Worth verifying once the adapter is built. |
| `ouroboros/tools/search.py` | 3 | Add Claude WebSearch path (Option W1). Make OpenAI Responses path optional. |
| `ouroboros/tools/vision.py` | 1 | No file change needed once `LLMClient.vision_query()` picks up the adapter; verify via test. |
| `ouroboros/tools/review.py` + `ouroboros/config.py:get_review_models` | 3 | Implement Option R2: when no OpenRouter/OpenAI keys are configured, fall back to a 3-persona single-family review. Add persona prompts to `prompts/` or inline. |
| `ouroboros/loop.py` + `ouroboros/loop_llm_call.py` + `ouroboros/loop_tool_execution.py` | 2 | Migrate hot path to `ClaudeSDKClient` with MCP-exposed Ouroboros tools. |
| `ouroboros/consciousness.py` | 2 | Same as loop; consciousness uses the same primitive. |
| `ouroboros/validation/model_improver.py:142` | 2 (opt) | Consider giving the improver true SDK-agent access (`Edit`/`Bash`/`Read` within sandbox) — clear win, but bigger change. |
| `ouroboros/pricing.py` | 1 | When `provider == "claude_code_oauth"`, return cost=0 from `estimate_cost()`. Still record token counts and notional cost. Add a separate "rate-limit-headroom" metric tracker that counts subscription messages used per 5-hour and weekly windows (best-effort). |
| `requirements.txt` | 1 | Uncomment / pin `claude-agent-sdk>=0.1.50` to make the gateway a first-class dep instead of optional. |
| `prompts/SYSTEM.md` | 1–3 | Update model identity copy: the agent should know "I run on Claude via subscription; when I evolve I am bounded by message rate limits, not dollar budget." Surface `OUROBOROS_LLM_BACKEND` in the dynamic Runtime context block in `ouroboros/context.py`. |
| `docs/ARCHITECTURE.md` | 1+ | Document the adapter, the backend switch, and the new auth flow. |
| `BIBLE.md` | 3 if R1 chosen | **Constitutional amendment** if multi-model review is reduced to single-family. Otherwise no change. |
| `launcher.py` | 1 | Verify `safety.py` is still byte-identical with the bundle after the change, otherwise the 🔄 sync will clobber the change on next launch. |
| `tests/` | 1–3 | New unit test `tests/test_claude_code_chat_adapter.py` mocking `claude_agent_sdk` (mirror `tests/test_claude_code_gateway.py:25–35` pattern). Update existing tests that hard-assert OpenRouter usage. |

---

## 8. Auth Wiring & Onboarding

### 8.1 Environment surface

Add to `SETTINGS_DEFAULTS` (`ouroboros/config.py:41`):

```python
"CLAUDE_CODE_OAUTH_TOKEN": "",
"OUROBOROS_LLM_BACKEND": "auto",     # auto | openrouter | direct | claude_code_oauth | local
"OUROBOROS_FALLBACK_ON_RATELIMIT": True,
"OUROBOROS_REVIEW_KEEP_PER_TOKEN_KEY": True,  # see §6.3 Option R2
```

Add these to `apply_settings_to_env()` env-key list (`ouroboros/config.py:320`).

### 8.2 Onboarding wizard

Replace the "API provider" radio with five options: OpenRouter / Direct OpenAI / Direct Anthropic / **Claude Code Subscription (OAuth)** / Local. The OAuth option needs:

* Token field (paste from `claude setup-token` output).
* "Test" button that calls a new server endpoint `/api/llm/probe` which sends a tiny `query()` and reports success + token count + tier (extractable from SDK metadata or `claude /status` parse).
* Warning copy: "Multi-model review will use Claude-only personas unless you also provide an OpenRouter key (recommended for diversity)."

### 8.3 Boot precedence

Top of `agent.py` / launcher startup:

1. If `OUROBOROS_LLM_BACKEND=claude_code_oauth` (explicit or via auto-detection): unset `ANTHROPIC_API_KEY` for child processes, ensure `CLAUDE_CODE_OAUTH_TOKEN` is present, ping `claude /status` (or its SDK equivalent) once at startup, record tier and headroom into the dynamic context block.
2. Verify `claude` binary is present (reuse `ensure_claude_code_cli`).
3. Log the chosen backend prominently in `events.jsonl` so any later "where did my money go?" investigation can answer instantly.

### 8.4 Why we explicitly unset `ANTHROPIC_API_KEY`

`claude` CLI uses the API key when both are set (as of the last documented behavior). The plan needs a sentinel test: spin up a fresh process with both, confirm which is used, document the result in the adapter. This must be verified before flipping any defaults — getting it wrong means the user keeps paying despite intending to switch.

---

## 9. Cost / Rate-Limit Tracking

### 9.1 Replace dollar gates with message-rate gates

Budget gates today (`TOTAL_BUDGET`, `OUROBOROS_PER_TASK_COST_USD`, `OUROBOROS_EVO_COST_THRESHOLD`) measure dollars. Under subscription they become irrelevant. Add parallel gates:

* `OUROBOROS_SUBSCRIPTION_MSG_BUDGET_5H` — soft cap on 5-hour-window subscription messages (default 60% of the user's tier limit).
* `OUROBOROS_SUBSCRIPTION_MSG_BUDGET_WEEK` — same, weekly.
* `OUROBOROS_FALLBACK_ON_RATELIMIT=true` — on 429 / quota-exceeded errors, switch the *current task* to the local model and continue; mark evolution paused until the next refill window.

### 9.2 Cost telemetry

Continue emitting `llm_usage` events. When the backend is `claude_code_oauth`, set `cost=0` but populate a new field `notional_cost` derived from `estimate_cost()` so the existing dashboards keep working. Surface a "subscription messages remaining" widget in the web UI's Costs tab.

### 9.3 Evolution throttling

`OUROBOROS_EVO_COST_THRESHOLD` (`$0.10`) currently fires the autonomous evolution loop. Replace it with a "min messages headroom" check: only evolve if we have e.g. >30% headroom in the current 5-hour window. Prevents runaway evolution from exhausting the subscription before the user gets back to the keyboard.

---

## 10. Compatibility & Rollback Strategy

* `OUROBOROS_LLM_BACKEND=auto` (the default) inspects env. With only `OPENROUTER_API_KEY` → behaves identically to today. With only `CLAUDE_CODE_OAUTH_TOKEN` → routes through the adapter. With both → adapter wins for tool-less calls; OpenRouter for tool calls (until Phase 2).
* Setting `OUROBOROS_LLM_BACKEND=openrouter` always forces the legacy path. This is the rollback switch: one settings change reverts behavior without code deploy.
* The new adapter is a self-contained module with one well-defined contract. If it produces buggy output for any single call site, point that site at the old path with a per-call override.
* All existing tests must continue to pass against `OUROBOROS_LLM_BACKEND=openrouter`. CI matrix should run them under both backends with the SDK mocked.

---

## 11. Phased Roadmap (with rough effort)

| Phase | Scope | Effort |
|---|---|---|
| **0. Auth verification** | Confirm CLI precedence when both `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY` are set. Document. Verify subscription rate limits empirically. | 0.5 day |
| **1. Chat adapter for tool-less calls** | New `gateways/claude_code_chat.py`, route safety/reflection/summary/scope-review/validation S0-S9/vision/consciousness-completions through it. Onboarding + settings + env wiring. Mocked unit tests. Document. | 3–5 days |
| **2. Hot-path migration (loop + consciousness)** | Expose Ouroboros tools as MCP tools; replace `LLMClient.chat()` inside `call_llm_with_retry` with SDK driver. Carry over caching/compaction/truncation logic. Heavy integration testing. | 7–10 days |
| **3. Review diversity + web search** | Implement R2 (hybrid review) and W1 (Claude WebSearch). Decide on `BIBLE.md` amendment if R1 is chosen instead. Update `prompts/` with persona reviewer prompts if needed. | 2–3 days |
| **4. Rate-limit-aware evolution gates** | Replace dollar gates with message-rate gates. Surface headroom in UI. Throttle consciousness. Wire local-model fallback on 429. | 2 days |
| **5. Cleanup** | Remove unused `OPENROUTER_API_KEY` / `OPENAI_API_KEY` paths IF the user wants to fully drop them. Otherwise leave as optional. Update `docs/ARCHITECTURE.md`, `README.md` changelog, `BIBLE.md` if amended. | 1 day |

Each phase is independently shippable. After Phase 1 the user already pays only for the residual tool-using loops; after Phase 2 the bill should approach zero except for review (if R2 with kept OpenRouter key) and any local-fallback compute.

---

## 12. Risks

1. **Auth surprise**: CLI silently prefers API key over OAuth → continued billing. *Mitigation:* startup probe + explicit `ANTHROPIC_API_KEY` unset in adapter; emit an `llm_backend_resolved` event so the truth is always loggable.
2. **Rate-limit clipping** during long evolution runs. *Mitigation:* Phase 4 message-budget gates + local fallback.
3. **Tool-call semantics divergence** when moving the hot path to SDK + MCP. SDK may serialize tool results differently, or fail differently on partial results. *Mitigation:* canary by enabling for one worker first; keep all observability events firing.
4. **Multi-model review weakening** (constitutional risk). *Mitigation:* R2 is the recommended default; explicit Bible amendment required if R1 is chosen.
5. **Bundle vs. repo drift** on 🔒🔄 files. If we end up needing edits to `safety.py` or `registry.py`, both project source and bundle source must update together, otherwise the next launch reverts the change. *Mitigation:* avoid edits to these files if possible; constrain the change to `llm.py`/`config.py`/gateway/new modules.
6. **SDK API drift**: `claude-agent-sdk` is on `0.1.x`. The interface is still moving. *Mitigation:* pin a minor version; treat the adapter as a single point of breakage with a clear test surface.
7. **Vision quality**: SDK image input flow is documented but less battle-tested than the chat-completions multi-part path. *Mitigation:* keep `vision_query()`'s old code reachable via the `openrouter` backend; add a regression test on a known screenshot.

---

## 13. Open Decisions for the User

Before implementation begins, answer these:

1. **OpenRouter key retention.** Are you willing to keep a small per-token key (OpenRouter OR direct OpenAI + Anthropic API key — small monthly bill) specifically to preserve multi-model review diversity (Option R2)? Or do you want subscription-only and accept the Bible amendment to single-family review (Option R1)?
2. **OpenAI key retention.** Web search through Claude WebSearch is good but not identical to OpenAI's Responses search. Keep OpenAI for `web_search` only, or fully replace?
3. **Subscription tier headroom.** Pro vs. Max. What are your actual rate limits? This sets the message-rate gates in Phase 4.
4. **Phase ordering.** Recommended Phase 1 → 2 → 3 → 4 → 5. Acceptable to ship Phase 1 alone and live with hot-path-on-OpenRouter for a while if you want a quick relief on the heavy-tail of small calls.
5. **Local fallback model.** Which one (`LOCAL_MODEL_SOURCE` / `LOCAL_MODEL_FILENAME`)? When subscription is exhausted, this is what keeps the agent online. If it's not chosen, the agent will stall on rate-limit errors.
6. **Consciousness frequency.** Today's `OUROBOROS_BG_WAKEUP_MIN=30s/MAX=7200s` will eat the subscription quota fast. Acceptable to widen the floor to e.g. 300s while Phase 2 is pending?

---

## 14. What This Plan Does NOT Cover

* Telegram bot LLM integration (not present in current code).
* GitHub Copilot or other third-party integrations.
* Migrating local-model server itself onto subscription (subscription is cloud-only).
* User-tier upgrade/downgrade UX.

---

*End of plan. No source files were modified by this document. To proceed, the user should answer §13 and pick a Phase 1 scope.*
