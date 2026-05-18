# Token Consumption Efficiency Analysis

**Date:** 2026-04-06
**Status:** Analysis and recommendations — do not implement yet

---

## 1. The Static Context Problem (THE BIGGEST COST)

Every single LLM call to the main agent (tasks, evolution, chat) loads the full static context:

| Document | Size (chars) | Est. tokens | Loaded for |
|----------|-------------|-------------|-----------|
| `SYSTEM.md` | 41,241 | ~10,300 | Every task |
| `BIBLE.md` | 25,143 | ~6,300 | Every task + every consciousness wakeup |
| `ARCHITECTURE.md` | 66,864 | ~16,700 | Every task |
| `README.md` | 36,292 | ~9,100 | Every task |
| `DEVELOPMENT.md` | 7,532 | ~1,900 | Every task |
| `CHECKLISTS.md` | 6,548 | ~1,600 | Every task |
| **Static total** | **183,620** | **~46,000** | **Every single task** |

Plus semi-stable (scratchpad, identity, dialogue blocks, knowledge index, patterns,
registry) — typically another 10,000-30,000 tokens.

**Total context per main agent call: ~60,000-80,000 input tokens minimum, before
the user even asks anything.**

At Opus pricing ($15/M input, $75/M output via OpenRouter), one task interaction
(even a simple chat reply) costs **$0.90-1.20 just in input context** — before the
model generates any output.

---

## 2. The Validation Pipeline LLM Calls

When the pipeline runs, it makes up to 7 separate LLM calls, each through the
validation modules (NOT through the main agent loop, so they don't carry the
46K static context):

| Call | Module | Model (default) | Max tokens | Effort | Est. input | Est. cost |
|------|--------|-----------------|-----------|--------|------------|-----------|
| S0 Comprehension | `artifact_comprehension.py` | Opus | 8,192 | **high** | ~20K | $0.30-0.90 |
| Arxiv Research | `model_researcher.py` | Opus | 2,048 | low | ~3K | $0.05 |
| Methodology Plan | `methodology_planner.py` | Opus | 4,096 | medium | ~8K | $0.12-0.40 |
| S9 Synthesis | `synthesis.py` | Opus | 8,192 | medium | ~10K | $0.15-0.50 |
| Report Narrative | `report.py` | Opus | 2,048 | low | ~5K | $0.08 |
| Self-Assessment | `self_assessment.py` | Opus | 4,096 | low | ~5K | $0.08 |
| Improvement (if run) | `model_improver.py` | Opus | 16,384 | medium | ~15K | $0.23-0.75 |

**Pipeline total: ~$1.00-3.50 per validation.**

**The problem:** ALL default to `anthropic/claude-opus-4.6` — the most expensive
model. Most of these calls don't need Opus-level reasoning.

---

## 3. The Evolution Phase

Each evolution cycle involves the main agent loop:

1. **Full context load** (~46K+ tokens static) — $0.90+
2. **Tool calls** — 10-30 tool calls per evolution. Each round-trip carries the
   growing conversation.
3. **Multi-model review** — 3 models review the diff (GPT-5.4 + Gemini 3.1 Pro +
   Opus). Three separate LLM calls with full diff + checklists.
4. **Scope review** — another Opus call after the triad review.

**Estimated cost per evolution cycle: $3-8.**

The 3 failed evolution cycles the agent ran consumed **$9-24** before producing
anything useful.

---

## 4. The Consciousness Loop

Every 5-minute wakeup:
- Loads BIBLE.md (25K chars) + memory + knowledge + health + runtime context
- 1 LLM call with the light model (Sonnet)
- Up to 5 tool-call rounds

**Cost per wakeup: ~$0.05-0.15** (Sonnet is much cheaper)
**Cost per hour: ~$0.60-1.80** (12 wakeups/hour at 5-min intervals)
**Cost per day: ~$14-43** just for consciousness running idle.

---

## 5. Safety Checks

Every `run_shell` command not in the whitelist triggers a 2-pass safety check:
- Pass 1: Light model (Sonnet) — cheap
- Pass 2 (if escalated): Heavy model (Opus) — expensive

The agent's `python3 -c` data analysis commands are long and unusual, triggering
Pass 2 more frequently.

---

## Recommendations

### Priority 1: Switch default models (HIGH IMPACT, ZERO CODE CHANGE)

Configure via Settings UI or `.env`:

| Setting | Current (Opus) | Recommended | Savings |
|---------|---------------|-------------|---------|
| `OUROBOROS_MODEL` | `anthropic/claude-opus-4.6` | `anthropic/claude-sonnet-4.6` | **~85%** on main agent |
| `OUROBOROS_MODEL_CODE` | `anthropic/claude-opus-4.6` | `anthropic/claude-sonnet-4.6` | ~85% |
| `OUROBOROS_VALIDATION_COMPREHENSION_MODEL` | `anthropic/claude-opus-4.6` | `anthropic/claude-sonnet-4.6` | ~85% on S0 |
| `OUROBOROS_VALIDATION_SYNTHESIS_MODEL` | `anthropic/claude-opus-4.6` | `anthropic/claude-sonnet-4.6` | ~85% on S9 |
| `OUROBOROS_VALIDATION_REPORT_MODEL` | `anthropic/claude-opus-4.6` | `anthropic/claude-sonnet-4.6` | ~85% on report |
| `OUROBOROS_VALIDATION_IMPROVEMENT_MODEL` | `anthropic/claude-opus-4.6` | `anthropic/claude-sonnet-4.6` | ~85% on improver |
| `OUROBOROS_REVIEW_MODELS` | 3 models (GPT+Gemini+Opus) | 2 models (Sonnet + Gemini) | ~50% on review |
| `OUROBOROS_SCOPE_REVIEW_MODEL` | Opus | Sonnet | ~85% |

**Sonnet 4.6 is ~10x cheaper than Opus 4.6** ($3/M input vs $15/M, $15/M output
vs $75/M). For validation tasks, Sonnet quality is sufficient.

**Keep Opus ONLY for:** `OUROBOROS_EFFORT_EVOLUTION: high` — the one place where
deep reasoning matters most.

### Priority 2: Reduce static context size (HIGH IMPACT, CODE CHANGE)

| Document | Current | Proposed |
|----------|---------|---------|
| `README.md` | Full 36K chars loaded every task | **Remove from context entirely.** Agent doesn't need changelog to validate models. |
| `ARCHITECTURE.md` | Full 67K chars loaded every task | **Truncate to first 200 lines** (~10K chars). Full doc available via `repo_read`. |
| `CHECKLISTS.md` | Full 6.5K loaded every task | Only load during **evolution and review** tasks, not validation. |

**Implementation:** In `context.py` → `build_llm_messages()`, conditionally skip
README and truncate ARCHITECTURE based on task type.

**Savings: ~25,000 tokens per call = ~$0.37 per task at Opus, ~$0.07 at Sonnet.**

### Priority 3: Reduce consciousness wakeup frequency (MEDIUM IMPACT, CONFIG CHANGE)

**Current:** 300 seconds (5 min) = 288 wakeups/day.
**Recommended:** 900 seconds (15 min) = 96 wakeups/day.

Set: `OUROBOROS_BG_WAKEUP_MIN=120`, `OUROBOROS_BG_WAKEUP_MAX=3600`

**Savings: ~66% reduction in consciousness cost** ($14→$5/day with Sonnet).

### Priority 4: Reduce reasoning effort for non-critical calls (MEDIUM IMPACT, CODE CHANGE)

| Call | Current effort | Recommended | Why |
|------|---------------|-------------|-----|
| S0 Comprehension | **high** | **medium** | Profile inference doesn't need deep reasoning |
| Methodology Planner | medium | **low** | Selecting from existing checks |
| Self-Assessment | low | low | Already correct |
| Report Narrative | low | low | Already correct |
| Arxiv Research | low | low | Already correct |

**Implementation:** Change `OUROBOROS_VALIDATION_COMPREHENSION_EFFORT` default
from `"high"` to `"medium"` in `config.py`.

### Priority 5: Skip unnecessary context in pipeline calls (LOW IMPACT, ALREADY PARTIALLY DONE)

The validation pipeline LLM calls are independent of the main context — good.
Ensure they stay lean and don't accidentally inherit the 46K static context.

### Priority 6: Content-hash cache for model comprehension (LOW IMPACT, CODE CHANGE)

If the same model ZIP is uploaded again, skip re-comprehension based on a content
hash. Saves ~$0.30-0.90 per re-validation.

---

## Cost Projection: Before vs After

**Scenario: Validate one model + 1 hour of idle consciousness**

| Component | Current (all Opus) | After recommendations |
|-----------|-------------------|----------------------|
| Pipeline (7 LLM calls) | $2.50 | $0.30 (Sonnet) |
| Main agent task overhead | $1.20 | $0.15 (Sonnet + trimmed context) |
| Consciousness (12 wakeups) | $1.80 | $0.20 (Sonnet, 4 wakeups at 15-min) |
| Evolution (if triggered) | $5.00 | $2.00 (Sonnet for most, Opus for review) |
| Safety checks (~5 commands) | $0.30 | $0.10 (Sonnet) |
| **Total per model + 1 hour** | **~$10.80** | **~$2.75** |

**~75% cost reduction** while maintaining validation quality and self-evolution.

---

## Implementation Priority

| # | Action | Type | Impact | Effort |
|---|--------|------|--------|--------|
| 1 | Switch all models to Sonnet except evolution | Config change | **75% cost reduction** | Zero code |
| 2 | Remove README.md from context, truncate ARCHITECTURE.md | Code in `context.py` | ~25K tokens/call | ~20 LOC |
| 3 | Increase consciousness wakeup to 15 min | Config change | ~66% consciousness savings | Zero code |
| 4 | Lower comprehension effort to medium | Config change | ~20% on S0 | Zero code |
| 5 | Load CHECKLISTS.md only for evolution/review | Code in `context.py` | ~1.6K tokens/call | ~5 LOC |
| 6 | Content-hash cache for comprehension | Code | ~$0.30 on re-validations | ~30 LOC |

**Do #1 and #3 immediately via Settings UI — zero code, immediate savings.
Then implement #2 and #5 as a code change.**
