# Context Trimming: Remove README.md, Conditionally Load ARCHITECTURE.md and CHECKLISTS.md

**Date:** 2026-04-06
**Status:** PLAN + implementation prompts

---

## Goal

Reduce the static context loaded into every LLM call by removing documents
that are not needed for most tasks.

## Two Changes

### Change A: Remove README.md from context entirely. Load ARCHITECTURE.md only during evolution.

**Current** (`context.py` → `build_llm_messages()`):
- `README.md` (36K chars, ~9K tokens) loaded on every task
- `ARCHITECTURE.md` (67K chars, ~17K tokens) loaded on every task

**After:**
- `README.md` — never loaded into context (agent can still `repo_read` it)
- `ARCHITECTURE.md` — loaded ONLY when `task.type == "evolution"` (the agent
  needs the architecture map when modifying its own code, not when validating
  models or chatting)

**Savings:** ~26K tokens per non-evolution call.

### Change B: Load CHECKLISTS.md only for evolution and review tasks.

**Current:** `CHECKLISTS.md` (6.5K chars, ~1.6K tokens) loaded on every task.

**After:** Loaded ONLY when `task.type in ("evolution", "review")`.

**Savings:** ~1.6K tokens per non-evolution/review call.

---

## Where to change

**Single file:** `ouroboros/context.py` → `build_llm_messages()` function (~line 758).

Current code:

```python
static_text = (
    base_prompt + "\n\n"
    + "## BIBLE.md\n\n" + bible_md
)
if arch_md.strip():
    static_text += "\n\n## ARCHITECTURE.md\n\n" + arch_md
if dev_guide_md.strip():
    static_text += "\n\n## DEVELOPMENT.md\n\n" + dev_guide_md
if readme_md.strip():
    static_text += "\n\n## README.md\n\n" + readme_md
if checklists_md.strip():
    static_text += "\n\n## CHECKLISTS.md\n\n" + checklists_md
```

After:

```python
task_type = str(task.get("type") or "user")

static_text = (
    base_prompt + "\n\n"
    + "## BIBLE.md\n\n" + bible_md
)
# ARCHITECTURE.md — only for evolution (agent needs the body map when self-modifying)
if arch_md.strip() and task_type == "evolution":
    static_text += "\n\n## ARCHITECTURE.md\n\n" + arch_md
if dev_guide_md.strip():
    static_text += "\n\n## DEVELOPMENT.md\n\n" + dev_guide_md
# README.md — removed from context (available via repo_read if needed)
# CHECKLISTS.md — only for evolution and review
if checklists_md.strip() and task_type in ("evolution", "review"):
    static_text += "\n\n## CHECKLISTS.md\n\n" + checklists_md
```

**Total: ~10 lines changed in one file.**
