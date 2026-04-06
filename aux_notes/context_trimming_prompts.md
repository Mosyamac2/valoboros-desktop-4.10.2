# Context Trimming — Implementation Prompt

**Only 1 prompt needed** — the change is ~10 lines in one file.

**Start the session by saying:**
> Read `aux_notes/context_trimming_plan.md` — this is the plan.
> Then execute the prompt below.

---

## Prompt 1 of 1: Trim Static Context in context.py

```
Read the context trimming plan in aux_notes/context_trimming_plan.md.

Reduce the static context loaded into LLM calls by conditionally skipping
documents that aren't needed for most tasks.

### File to modify:

ouroboros/context.py — In build_llm_messages() function (~line 758),
find where the static_text is assembled from the document variables.

Make these changes:

1. The task_type variable already exists earlier in the function (line ~757:
   task_type = str(task.get("type") or "user")). Use it.

2. ARCHITECTURE.md — load ONLY when task_type == "evolution":
   Change:
     if arch_md.strip():
         static_text += "\n\n## ARCHITECTURE.md\n\n" + arch_md
   To:
     if arch_md.strip() and task_type == "evolution":
         static_text += "\n\n## ARCHITECTURE.md\n\n" + arch_md

3. README.md — remove entirely from context:
   Change:
     if readme_md.strip():
         static_text += "\n\n## README.md\n\n" + readme_md
   To:
     # README.md removed from context — available via repo_read if needed

4. CHECKLISTS.md — load ONLY for evolution and review:
   Change:
     if checklists_md.strip():
         static_text += "\n\n## CHECKLISTS.md\n\n" + checklists_md
   To:
     if checklists_md.strip() and task_type in ("evolution", "review"):
         static_text += "\n\n## CHECKLISTS.md\n\n" + checklists_md

5. OPTIONAL CLEANUP: The readme_md variable is still read from disk
   (line ~765: readme_md = safe_read(env.repo_path("README.md"))).
   You can remove that line too since it's no longer used. Or leave it —
   it's harmless, just a wasted disk read.

### Verify

```bash
# 1. Verify README.md is no longer added to context
grep -n "README.md" ouroboros/context.py
# Should show the safe_read line (if kept) but NOT a static_text += line

# 2. Verify ARCHITECTURE.md is conditional on evolution
grep -A1 "ARCHITECTURE.md" ouroboros/context.py | grep -q "evolution" && echo "ARCH conditional: OK" || echo "ARCH conditional: MISSING"

# 3. Verify CHECKLISTS.md is conditional on evolution/review
grep -A1 "CHECKLISTS.md" ouroboros/context.py | grep -q "evolution.*review\|review.*evolution" && echo "CHECKLISTS conditional: OK" || echo "CHECKLISTS conditional: MISSING"

# 4. Run tests to confirm nothing broke
.venv/bin/python -m pytest tests/test_context.py tests/test_validation_types.py tests/test_integration.py --tb=short -q

# 5. Verify context.py still has task_type variable
grep -q "task_type" ouroboros/context.py && echo "task_type exists: OK" || echo "task_type: MISSING"
```

All checks must pass.
```

---

## Summary

| What | Change | Tokens saved |
|------|--------|-------------|
| Remove README.md from context | Delete the `static_text +=` line | ~9,100 per call |
| ARCHITECTURE.md only for evolution | Add `and task_type == "evolution"` | ~16,700 per non-evolution call |
| CHECKLISTS.md only for evolution/review | Add `and task_type in ("evolution", "review")` | ~1,600 per regular call |
| **Total** | **~10 lines changed** | **~27,400 tokens per regular task** |
