"""Kaggle harvester for Valoboros.

Standalone CLI that pulls Kaggle competition + kernel artifacts and drops
them into Valoboros's inbox (``~/Ouroboros/data/ml-models-to-validate/``).

**Constitutional principle (BIBLE v5.1, P3 LLM-First + P5 Minimalism):**
the harvester is *acquisition + infrastructure-safety only*. It does NOT
pre-structure, canonicalize, or normalize artifacts into a fixed schema.
Bundle layout is whatever Kaggle delivered. The validator agent
discovers the structure itself with its Claude Code toolkit. See
``aux_notes/kaggle_harvester_plan.md`` §0 for the full rationale.
"""

__version__ = "0.1.0"
