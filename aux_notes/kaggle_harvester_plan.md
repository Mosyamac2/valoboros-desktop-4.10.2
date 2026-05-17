# Plan: Kaggle ML Model Bundle Harvester for Valoboros

**Status:** Draft — for review. No code changes yet.
**Author:** Planning pass on 2026-05-17 (revised 2026-05-17 after user clarification).
**Goal:** Build a standalone CLI harvester that pulls Kaggle competition + kernel artifacts and drops them into Valoboros's inbox at `~/Ouroboros/data/ml-models-to-validate/`. The existing watcher → ingest → S0–S9 → improver → revalidation → effectiveness → reflection loop then runs unchanged.

Output of this plan goes to `aux_notes/` per Task Guardrails. No project source is modified by this document.

---

## 0. Constitutional Principle: the Harvester Stays Dumb

**This shapes everything below.** The harvester is acquisition + infrastructure-safety only. It does NOT pre-structure, normalize, canonicalize, or otherwise massage the artifacts into a fixed schema. The agent itself, when it picks up a new bundle, must explore it with its full Claude Code toolkit (`repo_read`, `code_search`, `run_shell`, Grep/Glob/Read via the SDK gateway), guided by **BIBLE.md, SYSTEM.md, CHECKLISTS.md, the knowledge base, dialogue memory, identity, and the patterns it has accumulated from past validations.** That exploration is the entire point of having a self-evolving validator (BIBLE P0/P2/P3) — short-circuiting it with a pre-normalizing harvester would replace agency with hardcoded behavior selection (BIBLE P3 violation) and replace minimalism with preprocessing scaffolding the agent could have done itself (BIBLE P5 violation).

**The harvester is allowed to:**
- Acquire the description, source code, and data from Kaggle.
- Apply *infrastructure-driven* transforms only: subsample data files that would crash the validation sandbox; strip notebook output cells that would inflate the bundle to GB-scale. These are runtime safety, not semantic preprocessing.
- Drop the result into a folder named after the model and zip it into the inbox.

**The harvester must NOT:**
- Impose canonical filenames (`model_description.txt`, `eval.py`, `train_sampled.csv`).
- Rewrite hardcoded paths inside the kernel source. The agent's S1 reproducibility stage is where path discovery lives — that's data for the validator to *find*, not for the harvester to *hide*.
- Synthesize an `eval.py` from competition metadata. Metric inference is S0 comprehension's job; pre-synthesizing it weakens the agent's most important learning signal.
- Pre-filter GPU-dependent or internet-dependent kernels. If the kernel won't run, the agent should encounter that fact during S1 and decide what to do (fall back, adapt, mark as a finding).
- Decide the train/test split. The agent owns holdout strategy.

**Bundle shape is variable, not fixed.** Number of files, naming, language of the description, presence of subfolders — all whatever Kaggle delivered. The two example bundles you shipped (`prepayment_consumer_loans_model.zip` and `Complaints_Classification_NLP_model.zip`) already show this: one has `to_archive/`, the other has `2-archive/`, one has flat data files, the other has `processed data/`, one has a single description file, the other has a different name. Both ingest successfully because Valoboros's S0 is built to roam, not to template-match.

This is recorded as a persistent memory: `feedback_harvester_stays_dumb.md`.

---

## 1. What's Already in Place (No New Work Here)

Verified by reading source. The user's mental model matches the runtime exactly:

| Concern | Where it lives | What it does |
|---|---|---|
| Inbox folder | `~/Ouroboros/data/ml-models-to-validate/` | Drop a ZIP, watcher picks it up |
| Watcher | `ouroboros/validation/watcher.py` | Polls inbox, ingests new ZIPs |
| Ingest | `ouroboros/tools/model_intake.py` → `_ingest_model_artifacts_impl` | Unpacks ZIP, lays out per-bundle workdir |
| Per-bundle workdir | `~/Ouroboros/data/validations/{bundle_id}/` | `raw/model_code/`, `inferred/`, `methodology/`, `results/`, `improvement/`, `validation.log` |
| Pipeline S0–S9 | `ouroboros/validation/pipeline.py` | Comprehension, deps, research, methodology, reproducibility, S2-S8 checks, synthesis |
| Improver (sandbox side-agent) | `ouroboros/validation/model_improver.py` | Implements `improvement/plan.json` hard recs inside sandbox |
| Revalidation | `RevalidationPipeline` in `pipeline.py` | Re-runs quantitative checks on improved model; records lift |
| Effectiveness | `ouroboros/validation/effectiveness.py` | Four-tier feedback tracker; quality of findings + recs |
| Reflection | `ouroboros/validation/reflection_engine.py` | Cross-validation pattern analysis; fuel for evolution |
| Methodology evolver | `ouroboros/validation/methodology_evolver.py` | Autonomous check evolution from accumulated traces |

**Bundle format** (verified against `prepayment_consumer_loans_model.zip` and `Complaints_Classification_NLP_model.zip`): a ZIP whose top-level folder contains:
- `*description*.txt` — model report (free-form prose; the agent's S0 LLM comprehension reads it)
- `model_code/` — `.ipynb` and/or `.py` files
- Data files at root or in a `processed data/` or `data/` subdirectory — CSV / pickle / parquet
- Optional metric outputs (`*-results.csv`)

This is the contract we must match.

---

## 2. Locked-In Design Decisions

From the prior Q&A:

| # | Decision | Value |
|---|---|---|
| 1 | Bundle granularity | One competition = one bundle, one kernel inside |
| 2 | Kernel quality target | **Moderate, not best.** Skip top 20% of kernels by votes; uniform pick from the next 30% band. Documented rationale in BIBLE memory: the agent needs real room to find issues |
| 3 | Runtime | Standalone CLI: `scripts/kaggle_harvester.py` |
| 4 | Data subsampling | Threshold 200 MB; stratified random subsample of training file to ~50 MB; keep test/holdout intact |
| 5 | Eval contract | ~~Hybrid eval.py~~ **DROPPED per §0.** No synthesized eval script; metric inference is S0 comprehension's job. The agent reads the competition description and decides the metric, holdout strategy, and how to measure improvement deltas itself. |
| 6 | First-batch size | 5 bundles |
| 7 | Domain filter | Tabular + NLP (no CV/image in the first batch) |
| 8 | Data-acceptance gate | **Tier-graceful:** (a) prefer auto-skip competitions whose data requires rules acceptance; (b) if too few bundles obtained, fall back to a user-supplied allow-list of pre-accepted competition slugs; (c) only as last resort, switch source from `Competitions` to `Datasets` (weaker leaderboard contract) |

Open data still needed at runtime: **Kaggle username** (paired with the `78d3c9b4...` 32-char hex key) for `~/.kaggle/kaggle.json` or `KAGGLE_USERNAME`/`KAGGLE_KEY` env vars.

---

## 3. Architecture

### 3.1 File layout

```
scripts/
└── kaggle_harvester/
    ├── __init__.py
    ├── __main__.py             # CLI entrypoint: python -m scripts.kaggle_harvester ...
    ├── auth.py                 # Reads KAGGLE_USERNAME/KAGGLE_KEY or ~/.kaggle/kaggle.json
    ├── discovery.py            # List + filter candidate competitions
    ├── kernel_picker.py        # Moderate-tier selection logic
    ├── data_subsampler.py      # Stratified subsample large files (infra safety)
    ├── notebook_size_guard.py  # Strip outputs ONLY when notebook exceeds size threshold
    ├── bundle_assembler.py     # Zip raw artifacts as-is into a folder, drop in inbox
    ├── allow_list.py           # Tier-2 fallback: user-curated competition slugs
    └── state.py                # Resumable manifest (~/.kaggle_harvester/state.json)
tests/
└── test_kaggle_harvester.py    # Unit tests with mocked Kaggle API responses
```

Rationale: a small package, each surface independently testable. No `eval_template.py`, no `notebook_normalizer.py` — those would have been preprocessing the harvester is not allowed to do (§0). What's left is purely acquisition, ranking, and infra-safety. No new dependency on the agent's runtime (`ouroboros/*` untouched).

### 3.2 CLI surface

```
python -m scripts.kaggle_harvester run \
    --count 5 \
    --domains tabular,nlp \
    --inbox ~/Ouroboros/data/ml-models-to-validate \
    --dry-run                       # build bundles but don't drop into inbox
    --resume                        # continue from state.json if interrupted
    --allow-list ~/.kaggle_harvester/allowed_competitions.txt   # tier-2 fallback
```

Subcommands:
- `run` — full pipeline
- `discover` — list candidate competitions matching filters; print to stdout
- `inspect <competition-slug>` — show what the harvester would assemble (description, picked kernel, data size, planned subsample); for sanity-check before commit
- `verify <bundle.zip>` — read a built bundle and print a one-page summary (file list, sizes, whether outputs were stripped, whether subsampling was applied). This is not a contract check (there is no contract per §0), just a human-readable sanity printout before dropping into the inbox

### 3.3 Data flow per competition

```
1. discovery.list_candidates(domains, exclude_seen)
        → kaggle competitions list --category {tabular,nlp} --csv
        → filter: closed, has-data-files, language=python kernels exist, exclude_seen
        → returns list of competition refs

2. For each candidate (until count reached):
   a. auth + acceptance probe:
        kaggle competitions files -c <comp>  → 200 OK / 403 ACCESS_DENIED
        on 403 → record skip reason in state.json, move on

   b. discovery.fetch_overview(comp)
        - kaggle competitions show -c <comp>     (description, evaluation metric)
        - Optional: scrape evaluation page (kaggle API doesn't expose it cleanly)

   c. kernel_picker.pick_moderate(comp)
        kaggle kernels list -c <comp> --language python --sort-by voteCount --page-size 100
        - drop the top 20% by votes
        - uniform random pick from the next 30%
        - kaggle kernels pull <kernel-ref> → notebook JSON

   d. data_subsampler.fetch_and_sample(comp)
        kaggle competitions download -c <comp> -p /tmp/<comp>/
        unzip
        if total size > 200 MB:
            identify train.* and test.* files heuristically
            if train target column inferable: stratified subsample to ~50 MB
            else: uniform random subsample
        keep test/holdout file intact
        record the subsample decision as a short paragraph appended to the
        description file (no separate SAMPLING.txt — see §0)

   e. notebook_size_guard.maybe_strip_outputs(notebook_path)
        if notebook bytes > 5 MB:
            clear outputs (infra-only; preserve cell code/markdown exactly)
            note the size-strip in the description file appendix
        else:
            leave the notebook entirely untouched (outputs may be informative)

   f. bundle_assembler.assemble(...)
        layout: whatever was acquired, in a folder named after the
        competition; structure is variable (§4). Typical shape:
            <comp_slug>_model/
                kaggle_overview.txt          # description + appendix
                <kernel-slug>.ipynb          # kernel, paths unchanged
                <whatever the competition's data archive contained,
                 possibly with the train file subsampled>
        zip it into ml-models-to-validate/<comp_slug>_kaggle_model.zip

   g. state.record(comp_slug, "ingested", bundle_path)

3. When state shows count bundles assembled:
   - print summary table
   - exit
```

### 3.4 Tier-graceful acceptance handling

Per locked decision #8:

```
attempt_tier_1:
    walk competition list, attempt each, skip on 403
    if >= count succeeded: done
    else: log "tier-1 collected N of count, falling back to tier-2"

attempt_tier_2:
    require --allow-list arg; abort with helpful message if absent
    walk the user-supplied slugs, attempt each
    if >= count total succeeded: done
    else: log "tier-2 collected M of count, falling back to tier-3"

attempt_tier_3:
    switch source: kaggle datasets list ... instead of competitions
    no evaluation page; the description appendix records "source: dataset
    (no leaderboard metric)" so the agent's S0 comprehension knows it has
    to infer the metric purely from the dataset description.
```

Default is tier-1 only; the CLI prints clear instructions for enabling tier-2 if needed.

---

## 4. Bundle Contents (Typical, Not Contractual)

Per §0, the harvester does not impose a schema. A bundle is just "what was found, in a folder, zipped." A typical bundle from this harvester will contain — but is not required to contain — the following:

- **A description file** — the competition overview text the harvester pulled from the Kaggle API (description + evaluation page + rules excerpt). The harvester saves this as a single `.txt` file inside the bundle. Filename is informational (e.g. `kaggle_overview.txt`); the agent finds it by reading whatever text files are present.
- **One or more source files** — the picked kernel's notebook(s) as Kaggle returned them, plus any `.py` files the kernel imports if those were retrievable. Paths inside the notebook are **left as-is**. If they reference `/kaggle/input/<slug>/`, that stays. The agent's S1 reproducibility stage discovers and reasons about these paths.
- **Data files** — whatever the competition shipped, possibly subsampled for the sandbox's sake (see §5). Naming follows the competition's own conventions (e.g., `train.csv`, `test.csv`, or stranger names — Kaggle is not consistent across competitions).

What is **deliberately not produced**:
- ❌ No synthesized `eval.py`. The metric is the agent's job to infer from the description text during S0 comprehension. If the agent later wants a scripted holdout evaluator, it can write one and commit it as part of its improvement plan.
- ❌ No reserved holdout CSV with truth labels. The Kaggle test file is shipped intact when available; if the agent wants a labelled holdout it should carve one from the training data itself as part of its methodology.
- ❌ No canonical `deps.txt`. `!pip install` lines stay inside the notebook where they were; `dependency_extractor.py` already scans notebooks AST-style.
- ❌ No `SAMPLING.txt`. If the harvester subsampled, it appends a short note to the description file (one paragraph, plain text) so the agent can read it like any other context — no special filename to detect.

**Filename and structure are variable.** Your two example bundles already demonstrate this — one wraps everything in `to_archive/`, the other in `2-archive/`. The agent's S0 stage is built to roam and discover, not to template-match.

---

## 5. Authentication

The user supplied a 32-char hex key. To complete auth we need `KAGGLE_USERNAME`. Two intake paths supported:

| Path | Setup |
|---|---|
| Standard Kaggle file | `~/.kaggle/kaggle.json` with `{"username":"<name>","key":"<32-char hex>"}`, mode `0600` |
| Env vars | `KAGGLE_USERNAME=<name>` + `KAGGLE_KEY=<32-char hex>` |

The harvester's `auth.py`:
- Reads either path
- Validates the key is 32-char hex
- On startup, runs `kaggle competitions list -s "test" --page 1` as a smoke probe
- On failure (401/403): prints clear instructions, exits non-zero

**The `KGAT_` prefix in the user's message is not part of the actual key.** The real value is the 32-char hex string. The `KGAT_` looks like a personal "Kaggle API Token" labelling convention.

---

## 6. Notebook Handling (Minimal, Infrastructure-Only)

Per §0, the harvester does not normalize, rewrite, or sanitize the notebook's *content*. The kernel is delivered to the agent in the same shape Kaggle returned it. The agent decides during S1 reproducibility what to do about paths, dependencies, GPU markers, magic cells, and `kaggle_secrets` imports — that's the exact kind of exploratory work the validator is supposed to be doing, and skipping it would erase the strongest learning signal in the loop.

The harvester applies exactly **one** operation to the notebook, and only for infrastructure reasons:

- **Optional output stripping if the notebook exceeds a size threshold** (default 5 MB). Some Kaggle notebooks embed megabytes of rendered images / progress bars / dataframes in their cell outputs, which inflates the bundle ZIP without adding semantic content the agent uses. When the notebook is below the threshold, outputs are preserved (sometimes informative — leaderboard scores, exploration plots, etc.). When above, outputs are cleared with `nbformat`. The decision and rationale are noted in the harvester's description-file appendix so the agent knows what was done.

Everything else — `/kaggle/input/...` paths, `!pip install`, `kaggle_secrets`, GPU calls, `%magic` cells, R/Julia/SQL cells — **stays in the notebook**. The validator meets the real-world artifact and reasons about it.

Kernel-selection failures (e.g., the picked kernel's metadata says it's R, not Python) are still handled: those are *acquisition* failures, not normalization failures. The harvester picks a different moderate-tier kernel and records the skip in `state.json`. The line between "we failed to acquire a Python notebook" and "we acquired a Python notebook with weird stuff in it" is the line between harvester responsibility and agent responsibility.

---

## 7. Dependencies

Adds to the harvester's own venv (not the agent runtime):

```
kaggle >= 1.6        # official Kaggle CLI + python lib
nbformat >= 5         # notebook IO
nbconvert >= 7        # for output stripping
pandas >= 2           # subsampling
pyarrow >= 14         # parquet support
scikit-learn >= 1.4   # stratified sampling
```

The harvester ships its own `scripts/kaggle_harvester/requirements.txt` so it can be installed independently of Valoboros's main `requirements.txt` (no pollution of agent runtime).

---

## 8. State + Resumability

`~/.kaggle_harvester/state.json`:

```json
{
  "version": 1,
  "harvested": [
    {"slug": "titanic", "bundle_path": "ml-models-to-validate/titanic_kaggle_model.zip", "kernel": "...", "ts": "2026-05-17T..."},
    ...
  ],
  "skipped": [
    {"slug": "google-quest", "reason": "rules_not_accepted", "ts": "..."},
    {"slug": "imet-2021-fgvc8", "reason": "no_moderate_python_kernel_after_3_attempts", "ts": "..."}
  ],
  "blocked_competitions": ["...slugs..."],
  "tier": 1
}
```

`--resume` reads this, skips anything already harvested or blocked, and continues until the count is met. Important because the network steps are flaky and re-running shouldn't double-fetch.

---

## 9. Storage Estimate (First Batch)

| Source | Before subsample | After subsample (200 MB threshold → ~50 MB) |
|---|---|---|
| 5 tabular comps | ~5 × 500 MB = 2.5 GB | ~5 × 70 MB = 350 MB |
| Notebooks (outputs stripped) | ~5 × 200 KB | ~5 × 200 KB |
| Bundle ZIPs | n/a | ~5 × 75 MB = 375 MB |

Worst case ~1 GB after first batch. The harvester refuses to start if `~/Ouroboros/data/` has less than 5 GB free.

---

## 10. Operational Concerns

- **Kaggle API rate limit**: ~60 requests/minute. The harvester paces itself at 2/sec.
- **Network flakiness**: each `kaggle ...` call wrapped with 3-retry exponential backoff.
- **Disk safety**: writes through a tempdir then atomic-moves to `ml-models-to-validate/` so the watcher never sees a half-formed ZIP.
- **License recording**: every bundle's description-text appendix (added by the harvester at the end of whatever description file Kaggle returned) lists the kernel author + Kaggle's per-kernel declared license. We do not republish — bundles stay on the user's local disk.
- **Idempotency**: re-running with the same `--count` produces no duplicates; already-harvested competitions are skipped via state.json.

---

## 11. Plan Of Work — Phases

| Phase | Scope | Effort |
|---|---|---|
| **0. Auth probe** | Kaggle username received (`Mosyamac`). Smoke-test with `kaggle competitions list`. | 5 min |
| **1. Skeleton** | Package layout, CLI entrypoint, state.json plumbing, `auth.py`, `--dry-run`. | 0.5 day |
| **2. Discovery + filtering** | `discovery.py`: list competitions, filter for tabular/NLP/closed/python-kernels-exist. | 0.5 day |
| **3. Kernel picker** | `kernel_picker.py`: moderate-tier selection (skip top 20%, sample next 30%). | 0.5 day |
| **4. Data subsampler** | `data_subsampler.py`: 200 MB threshold; stratified-or-uniform subsample. Test/holdout kept intact. | 1 day |
| **5. Notebook size guard** | `notebook_size_guard.py`: clear outputs *only* when notebook > 5 MB. No path rewriting, no GPU filtering — that's the agent's job. | 0.25 day |
| **6. Bundle assembler** | `bundle_assembler.py`: drop the raw artifacts into a folder; zip; place in inbox via atomic-move. | 0.5 day |
| **7. Tier fallback** | `allow_list.py`: tier-2 (user-curated competition slugs) + tier-3 (Datasets) graceful degradation. | 0.5 day |
| **8. Tests** | Mocked Kaggle responses; verify the subsample preserves stratification; verify tier fallback ordering; verify notebook-size-guard doesn't trip below the threshold. | 1 day |
| **9. End-to-end dry run** | Harvest 1 bundle with `--dry-run`, manually inspect ZIP, then 5-bundle batch into the live inbox, watch Valoboros pick it up. | 0.5 day |

**Total: ~4.75 working days** (down from ~6). The reduction comes from dropping the notebook-normalizer and eval-template phases per §0. Each phase is independently shippable; the harvester is usable after phase 7 even before tests are written.

---

## 12. Verification Checklist (Before First Real Run)

- [ ] `kaggle competitions list` works with the configured credentials
- [ ] `inspect titanic` (or another small open competition) prints expected description + kernel pick + data size
- [ ] `--dry-run run --count 1` produces one ZIP in a temp dir
- [ ] Manually unzip the produced bundle and confirm:
  - [ ] A description text file is present (any filename) and contains the harvester's appendix listing source kernel + license + subsampling-if-any
  - [ ] A `.ipynb` source file is present, untouched except for output stripping when the notebook exceeded the 5 MB threshold (record which case applied)
  - [ ] Data files are present in their original Kaggle-delivered shape; if subsampling triggered, the training file is reduced and the description appendix records it
  - [ ] No invented files (`eval.py`, `holdout_truth.csv`, `SAMPLING.txt`, etc.) — see §0
  - [ ] Total bundle size is reasonable (≤ ~200 MB)
- [ ] Drop the bundle into the inbox; confirm the watcher ingests it and S0 starts within 60s
- [ ] After full S0–S9, confirm `~/Ouroboros/data/validations/<bundle_id>/results/report.md` exists
- [ ] If `OUROBOROS_VALIDATION_AUTO_IMPROVE=True`, confirm `improvement/plan.json` and `improvement/revalidation/` populate
- [ ] Confirm `~/Ouroboros/data/validation_recommendations.jsonl` gains a new line with the lift measurement

---

## 13. Risks

1. **Kaggle ToS / kernel licenses** — bundles stay local to the user's machine. The harvester records the kernel author + Kaggle's declared kernel license inside the description text appendix so the agent (and any later reader) knows the provenance. We do not republish.
2. **Notebook execution failures** — a moderate-tier kernel may not run in the validation sandbox (missing libs, hardcoded Kaggle paths, custom Kaggle modules, GPU calls). **This is expected and informative**: the agent's S1 reproducibility stage discovers it, adapts where possible, and records the obstacle as a finding when it can't. A high *unrecoverable* failure rate would weaken the feedback signal — phase-9 end-to-end run measures the actual success rate. Mitigation: if too many S1 failures pile up, the agent's reflection engine will surface a methodology pattern ("kaggle bundles need a path-discovery sub-stage") — that's the right place to fix it, not the harvester.
3. **Subsampling distorts the problem** — a 10× shrink can collapse a minority class to a handful of examples. Mitigation: stratification preserves class proportions; the harvester refuses to subsample below 1000 rows of any minority class (instead falls back to the next competition).
4. **Acceptance-gate scarcity** — tier-1 may yield few harvestable competitions if most popular ones require rules acceptance. We expect to fall through to tier-2 quickly. Worth knowing before the first run.
5. **Variable bundle shapes confuse S0** — possible in theory, but the two example bundles you shipped already differ structurally and both work. The agent has had `repo_read`/`code_search`/`run_shell` plus the knowledge base since well before this migration; that exploration capability is the foundation we are explicitly leaning on per §0.

---

## 14. What This Plan Does NOT Cover

- **Pre-normalizing artifacts into a fixed schema** — intentionally rejected per §0 (LLM-First). This is the most important non-goal: the agent's exploration capability is what makes Valoboros a learning validator; we will not erase it with hardcoded preprocessing.
- Automatic re-harvest on a schedule (deferred until phase 9 reveals the actual quality bar).
- CV/image competitions (deferred until first batch's feedback is digested).
- Multi-kernel bundles (rejected per locked decision #1).
- Kaggle "Datasets" source (tier-3 fallback only; not the primary path).
- A web UI for harvest control (deferred — CLI is sufficient for now).

---

## 15. Single Remaining Input From User

Kaggle username. Once supplied, phase 0 takes ~5 minutes and phase 1 can start. The 32-char hex API key is already in hand.

*End of plan.*
