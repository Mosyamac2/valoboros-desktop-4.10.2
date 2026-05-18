# Plan v2: Agentic Claude-Code Validation + Closed Feedback Loop

**Status:** Draft v2 — supersedes v1. No code changes yet.
**Author:** Planning pass 2026-05-17, second iteration after the user clarified that validation must launch Claude Code CLI as a real agent, not a series of narrow single-turn LLM calls.
**Goal:** Replace the current S0–S9 narrow-prompt pipeline with an **agentic Claude Code SDK session per bundle**, give it the full constitutional + accumulated-knowledge context it needs to actually think like a senior validator, drive the user's **four-phase task structure** (methodology → implementation → execution → report), and close the feedback loop so that **self-evolution produces commits to Valoboros's own source code**.

Output goes to `aux_notes/` per Task Guardrails. No source files are modified by this document.

---

## 0. Why v1 Wasn't Enough

The first version of this plan kept the existing six narrow per-step prompts (`_COMPREHENSION_PROMPT`, `_METHODOLOGY_PROMPT`, `_SYNTHESIS_PROMPT`, `_SELF_ASSESS_PROMPT`, report prompt, `_IMPROVE_PROMPT`) and added four pieces around them (real lift / tracker writes / evolver wiring / source-evolution).

That fixes the **loop** but leaves the **brain shallow**: each LLM call gets a templated narrow user prompt and no constitutional context. The validation pipeline is structurally less LLM-driven than the agent loop. It cannot say "I've seen this pattern in 19/21 bundles — it's a harvester artifact, not a finding." It can only ever do "given this one failed check, write one recommendation."

**v2 abandons the narrow pipeline.** Each bundle is now validated by a **real Claude Code SDK session** — with tool access, multi-turn iteration, and the entire BIBLE / SYSTEM / CHECKLISTS / accumulated-knowledge / cross-bundle-pattern context loaded up front. The user's three example prompts (methodology design / Python-project implementation / report prettification) become the **four phases** Claude Code drives end to end, with full agentic capability instead of single-turn templates.

This honors BIBLE v5.1 P3 (LLM-First) properly: where v1 simulated expertise with templates, v2 invokes actual expertise.

---

## 1. The New Per-Bundle Flow

```
bundle ZIP lands in ~/Ouroboros/data/ml-models-to-validate/
        ↓
watcher ingests → ~/Ouroboros/data/validations/{bundle_id}/raw/...
        ↓
optional fast deterministic pre-check (existing S0-S8 checks) → pre_check_summary.json
        ↓
┌──────────────────────────────────────────────────────────────────┐
│ AGENTIC VALIDATION SESSION                                       │
│   ClaudeSDKClient(                                               │
│     cwd = bundle_dir,                                            │
│     model = "opus",   # → claude-opus-4.7                        │
│     system_prompt = built from rich context (§3),                │
│     allowed_tools = [Read, Edit, Write, Glob, Grep, Bash],       │
│     PreToolUse hook = path_guard(cwd)                            │
│   )                                                              │
│                                                                  │
│   Phase A: methodology design        → methodology.md            │
│   Phase B: Python project authoring  → validation_project/       │
│   Phase C: execution + interpretation → results.json + interpretation.md │
│   Phase D: report prettification     → report.md                 │
└──────────────────────────────────────────────────────────────────┘
        ↓
parser → legacy ValidationReport for backwards-compat with the rest of the system
        ↓
findings + hard recs → improvement cycle (existing, agentic Claude Code modifies the kernel)
        ↓
re-run THE SAME validation_project on the improved kernel → real metric lift
        ↓
EffectivenessTracker.record_recommendation_result(metric_before, metric_after)
        ↓
[batch end / cron]
reflection_engine on results.json across bundles → validation_patterns.md
        ↓
methodology_evolver → EvolutionProposal event
        ↓
consciousness picks up event → 7-step evolution protocol task
        ↓
commit on ~/Ouroboros/repo/ouroboros branch → restart
        ↓
next batch validates with evolved prompts / evolved helper library
```

---

## 2. What Stays, What Goes, What's New

### Stays (current code, unchanged in v2)

| Module | Why it stays |
|---|---|
| `ouroboros/validation/watcher.py` | Inbox watcher — bundle ingestion is fine as-is |
| `ouroboros/tools/model_intake.py::_ingest_model_artifacts_impl` | Creates per-bundle workdir; no change needed |
| `ouroboros/validation/sandbox.py` | Used by Phase C to execute the generated validation_project safely |
| `ouroboros/validation/effectiveness.py` | The `EffectivenessTracker` JSONL contract is fine; we just need to actually call `record_recommendation_result` |
| `ouroboros/validation/model_improver.py` (after recent patches) | Claude Code rewriting the kernel — already works |
| `~/Ouroboros/data/memory/knowledge/` directory | Accumulated knowledge files keep their format; just read by the new system-prompt builder |

### Goes (deprecated, kept only as an optional fast pre-check)

| Module | Why deprecated |
|---|---|
| `ouroboros/validation/artifact_comprehension.py` (`_COMPREHENSION_PROMPT`) | Claude Code does this in-situ during Phase A using its Read/Grep/Glob tools |
| `ouroboros/validation/methodology_planner.py` (`_METHODOLOGY_PROMPT`) | Replaced by Phase A directly |
| `ouroboros/validation/synthesis.py` (`_SYNTHESIS_PROMPT`) | Replaced by Phase C interpretation + Phase D report |
| `ouroboros/validation/self_assessment.py` (`_SELF_ASSESS_PROMPT`) | Claude Code's agentic loop can self-critique inline; if we want explicit Tier-0 signal, add it as a tail step of Phase C |
| `ouroboros/validation/report.py` (narrative report prompt) | Replaced by Phase D |
| Stage modules `intake_check.py`, `performance.py`, `fit_quality.py`, `leakage.py`, `fairness.py`, `sensitivity.py`, `robustness.py`, `code_quality.py` | Kept available as a fast pre-check, but no longer the primary validation. Each is still a Python function that runs in <1 s and produces a summary for Claude Code to consume in Phase A |
| `ouroboros/validation/checks/check_manifest.json` | Becomes an inventory of pre-check helpers, not the source of truth for methodology |

The deprecated modules are NOT deleted — they become **advisory pre-check helpers** that the agentic runner can optionally summarize and feed into Phase A's system prompt. This preserves the deterministic safety net (something measurable happens even if Claude Code crashes mid-session) while moving the *intelligence* into the agentic session.

### New (modules to be added in v2)

| Module | Job |
|---|---|
| `ouroboros/validation/agentic_runner.py` | The Claude Code SDK driver for the four phases per bundle |
| `ouroboros/validation/agentic_system_prompt.py` | Builds the rich system prompt loaded into the Claude Code session |
| `ouroboros/validation/agentic_prompts/phase_a_methodology.md` | Phase A user prompt template (adapted from user's PROMPT #1) |
| `ouroboros/validation/agentic_prompts/phase_b_implement.md` | Phase B user prompt template (adapted from user's PROMPT #2) |
| `ouroboros/validation/agentic_prompts/phase_c_execute.md` | Phase C user prompt template (run + interpret) |
| `ouroboros/validation/agentic_prompts/phase_d_report.md` | Phase D user prompt template (adapted from user's PROMPT #4) |
| `ouroboros/validation/agentic_results_parser.py` | Parse Claude-Code-produced `results.json` into legacy `ValidationReport` for backwards-compat |
| `scripts/run_agentic_validation.py` | One-bundle and one-batch entrypoints |

---

## 3. The Rich System Prompt Claude Code Gets

Built fresh per bundle by `agentic_system_prompt.build(bundle_dir, model_type, pre_check_summary)`. Structure:

```
# Identity & Constitution
[full BIBLE.md inlined]

# Your role for this session
[from prompts/SYSTEM.md — the agent-identity section, edited for validator mode:
 "I am Ouroboros-V, validator manifestation. My mission is to find real model
 problems, never hallucinate, give actionable recommendations whose
 implementation improves model metrics in measurable ways."]

# Validation playbook
[docs/CHECKLISTS.md sections relevant to validation; the qualitative-before-
 quantitative principle from BIBLE; the no-false-positives commitment]

# What I have learned across past validations
## Cross-bundle patterns
[~/Ouroboros/data/memory/knowledge/validation_patterns.md]
## Per-model-type knowledge
[~/Ouroboros/data/memory/knowledge/model_type_{model_type}.md]
## General error patterns
[~/Ouroboros/data/memory/knowledge/patterns.md]

# What the fast deterministic pre-check found
[pre_check_summary.json formatted as a short bullet list — e.g.
 "S8.CODE_SMELLS already detected: 4× hardcoded /kaggle/input/ paths.
  S1.REPRODUCIBILITY status: unknown (notebook didn't run cleanly in
  hermetic sandbox because deps weren't installed)."]
NOTE: the pre-check ran without context. Treat its findings as raw signals,
not as ground truth. Your Phase A should reason about whether each pre-check
finding is a real issue or a structural artifact.

# Tools available
You have: Read, Edit, Write, Glob, Grep, Bash. Use Bash to run `pip install`
inside the workdir's venv when the bundle needs deps. Stay inside cwd.

# Bundle location
You are running with cwd = ~/Ouroboros/data/validations/{bundle_id}/.
Bundle data is at ./raw/. Work output goes to ./methodology/ and ./results/.
```

Two consequences worth flagging:

1. **The system prompt is long** (~30-50 KB once BIBLE + per-type knowledge + patterns are inlined). With prompt caching (the SDK does it automatically against the subscription) this is paid once per session and amortized across all turns. Cost-wise it's free under the OAuth subscription.

2. **It's bundle-aware** — `model_type_classification.md` is inlined only for classification bundles, etc. The first time a never-seen model_type arrives, the file may be empty / minimal; Claude Code still has BIBLE + general patterns to lean on.

---

## 4. The Four Phases In Detail

### Phase A — Methodology design

**System prompt:** §3 (full agentic context).
**User prompt:** adapted directly from the user's PROMPT #1:

```
You are a senior expert in both Machine Learning and the domain represented
by this bundle. The model code is in ./raw/model_code/, the data samples are
in ./raw/data/ (or wherever the bundle delivered them). Read everything in
./raw/. Understand the task. Understand the data structure. Understand the
model architecture.

Then create a validation methodology with two blocks:

## Block 1: Qualitative analysis
List specific, justifiable questions to inspect — drawbacks and improvement
zones of the model's architecture or approach. No general ideas. Specific
issues anchored to THIS model: target formulation, dataset preparation, data
leakage, feature engineering, algorithmic choices, etc. For each item,
describe HOW you'd inspect it. Each item should be sufficient AND necessary
— no padding.

## Block 2: Quantitative analysis
List specific tests with metrics, drill-downs, sensitivity analyses, and
their interpretation. For each test, describe HOW it should be carried out
and calculated, methodologically detailed enough that a developer could
implement it as code. Include metric thresholds for "PASS / FAIL / WARN".

Critical: this methodology will be implemented as Python code in the next
phase, so every test must be implementable from the bundle's data alone.

Write the methodology to ./methodology/methodology.md.
```

**Tool budget:** ~10 turns. Most are Read/Glob/Grep on the bundle contents.
**Output:** `./methodology/methodology.md` — Claude Code's design document.

### Phase B — Python project implementation

**System prompt:** continue Phase A's session (so Claude has the methodology fresh in memory) OR fresh session with `./methodology/methodology.md` re-loaded explicitly.
**User prompt:** adapted from the user's PROMPT #2:

```
Now you are a senior developer with deep statistics background. You have:
- The methodology you just wrote at ./methodology/methodology.md
- The model code at ./raw/model_code/
- The data samples at ./raw/data/

Create a comprehensive Python project at ./methodology/validation_project/
that implements every test from the methodology. Organize it so:

  validation_project/
    common/
      helpers.py         # data loading, metric helpers, splitting helpers
      io.py              # result JSON schema, IO utils
    qualitative/
      q1_<short_name>.py
      q2_<short_name>.py
      ...
    quantitative/
      quant1_<short_name>.py
      quant2_<short_name>.py
      ...
    run_all.py           # CLI: python run_all.py --tests all --output results.json
    requirements.txt     # pinned deps the project needs
    README.md            # how to run + what each test does

run_all.py must:
- Accept a list of test ids (e.g. "q1,quant3") or "all"
- Execute each test, capturing pass/fail/warn + numeric metric output
- Combine results into a structured JSON: {tests: [{id, name, block,
  verdict, metrics, evidence, error_if_any}], summary: {n_pass, n_fail,
  n_warn, n_error}}
- Print one JSON line to stdout on completion (parseable downstream)

Use only the data and code already present in ./raw/. Do not download
anything from the internet. If a test cannot be implemented from the
available data, mark it as "deferred" with the reason — do not fabricate.

Install any project deps with `pip install -r requirements.txt` in the
local venv when you set up. Use Bash for that. Verify the project at
least imports cleanly via `python -c "import run_all"` before signaling
you're done.
```

**Tool budget:** ~20 turns (Write per file, Bash to verify imports).
**Output:** `./methodology/validation_project/` complete and importable.

### Phase C — Execution + interpretation

**System prompt:** fresh session — Phase B's project is now self-contained.
**User prompt:**

```
Run the validation project at ./methodology/validation_project/ end to end:
1. Verify deps: `cd methodology/validation_project && pip install -r requirements.txt`
2. Execute: `python run_all.py --tests all --output ../../results/results.json`
3. Read results.json
4. Write a brief interpretation to ./results/interpretation.md describing:
   - which tests revealed real issues
   - which passed cleanly
   - which errored (and why — usually a setup or data issue)
   - the model's overall validation verdict in one sentence
   - any tests where the result was ambiguous and needs a follow-up

Constraints: stay in the cwd. The validation_project's tests may take some
time; allow up to 20 minutes total. If a single test hangs, kill it (use
`timeout 300 python ...`) and mark it as "timeout" in the interpretation.
```

**Tool budget:** ~10 turns. Mostly Bash invocations.
**Outputs:**
- `./results/results.json` — structured test results
- `./results/interpretation.md` — Claude's interpretation

### Phase D — Report prettification

**System prompt:** fresh session, minimal context — just the JSON + interpretation.
**User prompt:** adapted from PROMPT #4:

```
Prettify the validation results below into a human-readable report:

results.json:
{contents of ./results/results.json}

interpretation.md:
{contents of ./results/interpretation.md}

Constraints:
- Use markdown with tables, headers, bullet lists.
- Be concise and specific.
- Do NOT add any new information not supported by the JSON results.
- Recommendations must be feasible and measurable.
- If a finding is based purely on code review with no quantitative evidence,
  explicitly label it: "qualitative finding, awaiting quantitative confirmation."

Write the report to ./results/report.md.
```

**Tool budget:** ~3 turns.
**Output:** `./results/report.md` — the polished, human-readable deliverable.

---

## 5. Bridging To Existing Components

The agentic flow produces `results/results.json`. The rest of the system (improver, effectiveness tracker, reflection) currently expects `report.json` with the `ValidationReport` dataclass shape (`stages`, `hard_recommendations`, `soft_recommendations`, `critical_findings`, etc.).

`agentic_results_parser.py` is the bridge:

```python
def parse_agentic_results(
    bundle_id: str,
    bundle_dir: Path,
    pre_check_summary: dict,
) -> ValidationReport:
    """Read methodology/methodology.md + results/results.json + results/
    interpretation.md and produce a legacy ValidationReport.
    """
    results = json.load(open(bundle_dir / "results" / "results.json"))
    # Map agentic tests onto pseudo-stages so existing reflection code works:
    #   - "qualitative" block tests → pseudo-stage "QUAL"
    #   - "quantitative" block tests → pseudo-stage "QUANT"
    # Each test's verdict (pass/fail/warn) → ValidationCheckResult.passed
    # Each test's metric → check.score
    # Failed tests → hard_recommendations (parsed from interpretation.md)
    ...
```

This keeps the downstream code (improver, tracker, reflection engine, evolver) **unchanged** in v2. The agentic session replaces the *production* of validation outcomes; the *consumption* path is preserved.

---

## 6. How the Original Four Pieces Land in v2

### Piece 1 — Real lift measurement (REFRAMED)

**v1**: re-run S2-S8 checks on the improved bundle.
**v2**: **re-run THE SAME `validation_project/` (Phase B's output) on the improved bundle**. This is a much stronger lift signal because the tests were authored *for this specific bundle's model* and produce actual numerical metrics (AUC / RMSE / F1 / per-group disparities etc.) that the methodology declared upfront.

Implementation: `RevalidationPipeline.run()` becomes:
1. Locate `./methodology/validation_project/`.
2. Bash: `cd improvement/implementation/methodology/validation_project && python run_all.py --tests all --output ../../../results/results_improved.json`.
3. Compute lift as: per-test verdict diff (failed-before-passes-after), per-test metric diff for numeric scores.
4. Aggregate into a `categorical_lift` (verdict changes) AND a `numeric_lift` (metric deltas weighted by methodology priority).

This is what "real lift" looks like: the bundle has tests that emit numbers; the improvement runs the same tests; the deltas tell you what worked.

### Piece 2 — Effectiveness tracker writes

Same as v1. After Piece 1 produces `categorical_lift` and `numeric_lift`, the revalidation step calls:

```python
tracker.record_recommendation_result(
    check_id=rec_id,
    bundle_id=self._bundle_id,
    metric_before=results_original["summary"],
    metric_after=results_improved["summary"],
)
```

Plus a self-assessment hook: after Phase C, if `OUROBOROS_VALIDATION_AUTO_SELF_ASSESS=True`, Claude Code adds a brief Phase C.5 self-critique step ("of these findings, which do you think are most likely to be true positives?") that calls `tracker.record_self_assessment`.

### Piece 3 — Methodology evolver wiring

Methodology evolver's role changes substantially in v2:

**v1**: add / retire / fix entries in `check_manifest.json` (registry of deterministic checks).
**v2**: it now operates on **prompts and helpers**, not on a check registry. Its targets:

1. **Prompt evolution**: append a new directive to `agentic_prompts/phase_a_methodology.md` — e.g., "For NLP token-classification bundles, always include a probe for tokenizer-to-character offset misalignment as a quantitative test."
2. **Helper library evolution**: add a function to a shared `ouroboros/validation/agentic_helpers/` library (which Phase B's projects can import) — e.g., a reusable `stratified_holdout_with_group_check(df, target, group)` helper that captures "the right holdout strategy for tabular bundles with group structure."
3. **System-prompt evolution**: append to `agentic_system_prompt.py`'s built context — e.g., "Add this paragraph to the role section after N successful classification validations."
4. **Pre-check evolution**: still allowed — add / retire deterministic pre-checks as before.

Each is encoded as an `EvolutionProposal` envelope (see Piece 4) with a specific `target_kind` ("prompt", "helper", "system_prompt", "pre_check").

### Piece 4 — Source-code self-evolution + commit (UNCHANGED)

Same 7-step protocol as v1. The only change: the allow-list of target paths expands to include the new agentic-prompt files and helper library:

```python
ALLOW_LIST = [
    "ouroboros/validation/checks/",                # pre-check helpers
    "ouroboros/validation/agentic_prompts/",       # phase A-D prompt templates
    "ouroboros/validation/agentic_helpers/",       # reusable helpers Phase B can import
    "ouroboros/validation/agentic_system_prompt.py", # the system-prompt builder
]
DENY_LIST = [
    "BIBLE.md",
    "ouroboros/safety.py",
    "ouroboros/tools/registry.py",
    "prompts/SAFETY.md",
    "ouroboros/validation/sandbox.py",  # safety-critical, 🔒
]
```

The agent task `process_evolution_proposal(id)` runs the 7-step protocol with `claude_code_edit` against the chosen file. After commit + restart, the next batch validates with evolved prompts / helpers.

### Piece 5 — Rich context loading (SUBSUMED)

This becomes a non-piece. **Phase A's system prompt § 3 is the rich-context-loading mechanism.** No separate piece needed.

---

## 7. Module-by-Module Change Matrix

| File | v2 status | What changes |
|---|---|---|
| **NEW:** `ouroboros/validation/agentic_runner.py` | new | `run_agentic_validation(bundle_id, bundle_dir, config)` — the 4-phase orchestrator using `claude_agent_sdk.ClaudeSDKClient` |
| **NEW:** `ouroboros/validation/agentic_system_prompt.py` | new | `build_validator_system_prompt(bundle_dir, model_type, pre_check_summary)` |
| **NEW:** `ouroboros/validation/agentic_prompts/*.md` | new | Phase A / B / C / D user prompt templates |
| **NEW:** `ouroboros/validation/agentic_helpers/__init__.py` | new | Empty initially; populated by evolution proposals |
| **NEW:** `ouroboros/validation/agentic_results_parser.py` | new | Adapter: agentic results → legacy `ValidationReport` |
| **NEW:** `scripts/run_agentic_validation.py` | new | CLI: validate one bundle agentically; batch mode |
| **MOD:** `ouroboros/validation/pipeline.py::ValidationPipeline` | thin shim | `run()` becomes: optionally do fast pre-check → call agentic_runner → parse results → return legacy ValidationReport |
| **MOD:** `ouroboros/validation/pipeline.py::RevalidationPipeline` | rewrite | re-run the same Phase B project on the improved bundle; compute real categorical + numeric lift |
| **MOD:** `ouroboros/validation/methodology_evolver.py` | retarget | propose `prompt`/`helper`/`system_prompt`/`pre_check` changes, not check_registry mutations |
| **MOD:** `ouroboros/validation/types.py` | add | `AgenticValidationResult`, `EvolutionProposal` with `target_kind` enum |
| **MOD:** `ouroboros/consciousness.py` | hook | notice `evolution_proposal` events; enqueue `process_evolution_proposal` task |
| **MOD:** `ouroboros/agent_task_pipeline.py` | new handler | `process_evolution_proposal(id)` — 7-step protocol against allow-listed paths |
| **MOD:** `prompts/SYSTEM.md` | add | section on the validator-evolution task type |
| **KEPT:** `artifact_comprehension.py`, `methodology_planner.py`, `synthesis.py`, `self_assessment.py`, `report.py`, S0-S8 check modules | demoted | become advisory pre-check helpers; not deleted, not the source of truth |
| **NEW TESTS:** | | `test_agentic_runner.py`, `test_agentic_results_parser.py`, `test_revalidation_lift_v2.py`, `test_evolver_v2_targets.py`, `test_validator_evolution_task.py` |

---

## 8. Operational + Cost Concerns

### Subscription rate-limit impact
Each bundle's agentic session has roughly:
- Phase A: ~10 turns × ~3 KB messages = ~30 KB output + ~30 KB system prompt cached
- Phase B: ~20 turns × ~5 KB messages = ~100 KB
- Phase C: ~10 turns × ~2 KB messages = ~20 KB
- Phase D: ~3 turns × ~5 KB messages = ~15 KB

→ ~45 turns / bundle / Opus 4.7. Across 20 bundles: **~900 Opus turns per batch**, plus the improvement cycle re-runs Phase C for the improved bundle.

Anthropic Max subscription's 5-hour window typically holds 100-200 Opus 4.7 messages. **A full 20-bundle batch will exceed one 5-hour window.** Mitigations:
- **Phase splitting**: Run Phase A+B for all bundles first (one cluster), checkpoint; Phase C+D in a second cluster after the rate limit refreshes.
- **Sonnet 4.6 fallback for Phase B**: implementation work is mechanical; Sonnet handles it well. Cuts Opus turns roughly in half.
- **Pacing**: `agentic_runner` checks `EffectivenessTracker` rate-headroom metric (a new gate) and stops when below 30% headroom.

### Sandbox safety
Phase B writes Python files inside `bundle_dir/methodology/validation_project/`. Phase C executes them via `Bash`. The Claude Code SDK's `permission_mode="bypassPermissions"` allows writes inside `cwd`, and the `PreToolUse` `path_guard` hook blocks anything outside `bundle_dir`. We reuse the existing `ModelSandbox` resource limits (RLIMIT_AS / RLIMIT_CPU / unshare --net) for the Phase C runs.

### Variance
Claude Code is non-deterministic. Same bundle, different methodologies on different runs. Mitigations:
- Set `temperature=0` if exposed by the SDK.
- Cache the methodology for a given bundle — if the bundle hasn't changed, reuse the prior methodology.md and skip Phase A on re-runs.
- Cross-bundle reflection deliberately looks for patterns ACROSS many bundles, smoothing per-bundle variance.

### Auditability
Every phase's full transcript is persisted to `bundle_dir/_agentic_transcripts/phase_{a,b,c,d}.jsonl`. Anyone (including the user) can `cat` the transcripts and see exactly what Claude Code did + said. This is the audit-trail requirement satisfied without extra plumbing.

### Reversibility / Bible-protected paths
Same 🔒 protection as v1. `safety.py`, `registry.py`, `BIBLE.md`, `SAFETY.md`, `sandbox.py` are off-limits to evolution proposals. The validator-evolution allow-list is explicit (§6 Piece 4).

---

## 9. Phased Roadmap

| Phase | Scope | Effort | Independently shippable? |
|---|---|---|---|
| **1. Agentic runner skeleton** | `agentic_runner.py` + `agentic_system_prompt.py`. Single-phase smoke test: Phase A only, one bundle, system prompt loaded, methodology.md written. | 1 day | yes |
| **2. Phase B + project gen** | Full Phase A→B chain with file writes inside `methodology/validation_project/`. Verify `python -c "import run_all"` succeeds. | 1.5 days | yes |
| **3. Phase C + execution** | Phase C runs the generated project, captures results.json, writes interpretation.md. Confidence checkpoint: does the generated project actually produce numbers? | 1 day | yes |
| **4. Phase D + report** | Markdown report writer. Persistence of all artifacts. | 0.5 day | yes |
| **5. Results parser + legacy bridge** | `agentic_results_parser.py` produces legacy `ValidationReport`. Improver / reflection / tracker keep working unchanged. | 0.5 day | yes |
| **6. Revalidation v2** | RevalidationPipeline re-runs Phase B's `validation_project/` on improved bundle. Real categorical + numeric lift. (Piece 1) | 1 day | yes |
| **7. Effectiveness tracker writes** | Wire `record_recommendation_result` into the new RevalidationPipeline. Self-assessment hook in Phase C.5. (Piece 2) | 0.5 day | yes |
| **8. Reflection adaptation** | Cross-bundle reflection now scans `methodology.md`, `interpretation.md`, `results.json` (in addition to legacy report.json). Look for methodological patterns + recurring false positives. | 1 day | yes |
| **9. Methodology evolver retarget** | `EvolutionProposal` with `target_kind` ∈ {prompt, helper, system_prompt, pre_check}. (Piece 3) | 1 day | yes |
| **10. Source-evolution task** | `process_evolution_proposal` agent task; 7-step protocol; allow-list enforcement. (Piece 4) | 2 days | yes |
| **11. Live demo** | Drop a fresh bundle into the inbox; observe the full loop. Verify a real lift on a real metric. Verify a proposal emits, gets reviewed, gets committed, and the next batch uses the new prompt/helper. | 0.5 day | n/a — demo |

**Total: ~10-11 working days.** Each phase ships independently.

Phases 1-5 give us **agentic validation in place** — Phases 6-10 close the loop on top of it. Even halting after Phase 5 is a major improvement on what exists today.

---

## 10. Risks

1. **Cost / rate-limit on a 20-bundle batch.** Mitigations in §8. Sonnet fallback for Phase B is the cheapest, biggest win — most of Phase B's work is mechanical Python.
2. **Generated project quality.** Phase C catches gross failures (import errors / hangs). But subtle wrongness (a test that doesn't measure what its name says) is hard to detect. Mitigation: Phase D's report can note when tests' verdicts disagree with their named intent, and the cross-bundle reflection can flag pattern "all tests of type X always pass everywhere — possibly a fake test."
3. **Claude Code variance changes methodology between runs.** Real: same bundle, different methodologies. Mitigation: cache methodology.md per bundle (hash of bundle contents) so re-validations don't redesign.
4. **Validation project may pip-install heavy deps.** Mitigation: Phase B's prompt nudges toward "use what's already installed if possible"; Phase C's Bash sandbox has a 5-min install budget; over-budget → mark as deferred.
5. **Legacy parser drift.** As Phase B's `results.json` schema evolves under evolution proposals, the legacy parser may need updating. Mitigation: version the schema (`schema_version` field); parser handles versioned variations.
6. **Reflection across legacy + agentic reports.** Old reports are in `report.json` (legacy). New reports are in `results.json` + parser-output `report.json`. Reflection engine needs to read both formats. Mitigation: always emit a parser-output `report.json` alongside the new artifacts — reflection only needs to handle one canonical format.
7. **The methodology evolver might propose a prompt change that breaks Phase B's project generation.** Mitigation: every evolution proposal targeting a prompt must be exercised on a "canary bundle" before commit. Add this to the 7-step protocol's "smoke test" step.

---

## 11. End-to-End Acceptance Criteria (full closed loop)

After all 11 sub-phases land:

1. New bundle dropped into `~/Ouroboros/data/ml-models-to-validate/` is picked up by the watcher.
2. Fast deterministic pre-check runs → `pre_check_summary.json`.
3. **Phase A:** Claude Code reads the bundle, writes a methodology.md unique to this bundle, anchored to BIBLE + accumulated knowledge + cross-bundle patterns.
4. **Phase B:** Claude Code writes a working `validation_project/` with importable Python that runs each test.
5. **Phase C:** the project runs end-to-end, producing real numbers (AUC / RMSE / F1 / disparities / sensitivity scores) in `results.json`.
6. **Phase D:** a polished `report.md` lands.
7. **Improvement cycle:** Claude Code rewrites the kernel per hard recommendations.
8. **Revalidation:** the same `validation_project/` is re-run on the improved kernel; the numeric metric delta is recorded.
9. **Effectiveness tracker:** `validation_recommendations.jsonl` gains the `(rec_id, bundle_id, metric_before, metric_after)` row.
10. **Reflection:** after N≥3 validations of the same model type, cross-bundle patterns emerge in `validation_patterns.md` — and they include both "checks that fire everywhere" (per v1) and "methodological motifs that worked" (new in v2).
11. **Evolution proposal:** the evolver emits an `EvolutionProposal` event (e.g., "promote helper `stratified_holdout_with_group_check` from observation in 3 NBME-style bundles to a reusable function in `agentic_helpers/`").
12. **Source commit:** the 7-step protocol fires; `claude_code_edit` adds the helper; tests pass; review passes; Bible check passes; commit lands on the `ouroboros` branch with subject `validator-evolution: helper.stratified_holdout_with_group_check`.
13. **Restart:** the agent restarts (exit 42); the launcher picks up the new code.
14. **Next batch:** the next validation cycle's Phase B generations begin importing the new helper — completing the loop.

When steps 1-14 happen unattended after a single bundle drop, **the vision is realized**.

---

## 12. What This Plan Still Does NOT Cover

- **Multi-bundle parallel agentic sessions.** v2 assumes sequential validation. Parallel would multiply rate-limit risk; defer.
- **Cross-language bundles (R / Julia / SQL kernels).** v2 assumes Python kernels. The pre-check filter and Phase B's prompt should mention this constraint; non-Python kernels marked "skipped: unsupported language."
- **User-facing chat over a validation report.** Once the report is written, the user could @-mention a finding to ask follow-up. Out of scope here.
- **Multi-agent debate on evolution proposals.** Same as v1 Phase 6+. Optional future enhancement.
- **The bundle harvester evolving alongside the validator.** Theoretically the methodology evolver could propose changes to the harvester ("we keep getting `/kaggle/input/` smells — should the harvester pre-rewrite paths after all?"). Currently the harvester is intentionally dumb (BIBLE v5.1 §0); discussion of revisiting that is a separate constitutional matter, not a validator-evolution proposal.

---

*End of plan v2. No source files were modified by this document. To proceed, pick a sub-phase from §9 and confirm.*
