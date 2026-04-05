"""
Ouroboros validation platform — S0: LLM-powered artifact comprehension.

Reads raw model code, notebooks, data samples, and task descriptions,
then uses an LLM call to infer a structured ModelProfile.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ouroboros.validation.types import ModelProfile, ValidationConfig

log = logging.getLogger(__name__)

_MAX_CODE_CHARS = 80_000
_MAX_DATA_ROWS = 100

_COMPREHENSION_PROMPT = """\
You are an ML model analyst. You receive raw artifacts from an ML model development project.
Analyze them and produce a structured JSON profile.

## Task description
{task}

## Data description
{data_description}

## Code files
{code_summary}

## Data sample summaries
{data_summary}

## Instructions
Produce a JSON object with EXACTLY these fields (use null for unknown, empty lists for none):
{{
  "bundle_id": "{bundle_id}",
  "task_description": "<enriched task description>",
  "model_type": "classification|regression|ranking|clustering|generative|other",
  "model_type_confidence": <0.0-1.0>,
  "framework": "sklearn|pytorch|tensorflow|xgboost|lightgbm|catboost|statsmodels|other",
  "framework_confidence": <0.0-1.0>,
  "algorithm": "<specific algorithm name>",
  "data_format": "tabular|image|text|timeseries|mixed",
  "target_column": "<name or null>",
  "target_column_confidence": <0.0-1.0>,
  "feature_columns": ["<list of feature column names>"],
  "protected_attributes_candidates": ["<columns that might be sensitive>"],
  "temporal_column": "<name or null>",
  "data_files": [{{ "path": "<relative>", "role": "train|test|raw|lookup|unknown", "rows_sample": <int>, "columns": <int>, "format": "<ext>" }}],
  "code_files": [{{ "path": "<relative>", "role": "training|inference|preprocessing|utils|unknown", "language": "python" }}],
  "preprocessing_steps": ["<ordered list of transformations detected>"],
  "data_join_logic": "<how data files are combined, or null>",
  "train_test_split_method": "<detected split method, or null>",
  "hyperparameters": {{ "<param>": "<value>" }},
  "metrics_mentioned_in_code": {{ "<metric>": <value_or_null> }},
  "dependencies_detected": ["<package names>"],
  "known_limitations_from_comments": ["<limitations from code comments>"],
  "llm_warnings": ["<anything suspicious or unclear>"],
  "comprehension_confidence": <0.0-1.0>,
  "comprehension_gaps": ["<things you couldn't determine>"]
}}

Return ONLY the JSON. No markdown fences, no explanation.
"""


class ArtifactComprehension:
    """Analyze raw model artifacts using LLM to produce a ModelProfile."""

    def __init__(self, bundle_dir: Path, config: ValidationConfig, bundle_id: str = ""):
        self._bundle_dir = Path(bundle_dir)
        self._config = config
        self._bundle_id = bundle_id or self._bundle_dir.name

    async def analyze(self) -> ModelProfile:
        """Run LLM comprehension and return a ModelProfile."""
        task = self._read_text("inputs/task.txt")
        data_description = self._read_text("inputs/data_description.txt")
        code_summary = self._collect_code()
        data_summary = self._collect_data_summaries()

        prompt = _COMPREHENSION_PROMPT.format(
            task=task or "(not provided)",
            data_description=data_description or "(not provided)",
            code_summary=code_summary or "(no code files found)",
            data_summary=data_summary or "(no data files found)",
            bundle_id=self._bundle_id,
        )

        try:
            profile_dict = await self._call_llm(prompt)
        except Exception as exc:
            log.error("LLM comprehension failed: %s", exc)
            profile_dict = self._fallback_profile()

        # Ensure bundle_id is set
        profile_dict["bundle_id"] = self._bundle_id

        profile = ModelProfile.from_dict(profile_dict)

        # Write outputs to inferred/
        inferred = self._bundle_dir / "inferred"
        inferred.mkdir(parents=True, exist_ok=True)
        (inferred / "model_profile.json").write_text(
            json.dumps(profile.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return profile

    # ------------------------------------------------------------------
    # File readers
    # ------------------------------------------------------------------

    def _read_text(self, rel_path: str) -> str:
        p = self._bundle_dir / rel_path
        if p.exists():
            try:
                return p.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                pass
        return ""

    def _collect_code(self) -> str:
        code_dir = self._bundle_dir / "raw" / "model_code"
        if not code_dir.exists():
            return ""
        parts: list[str] = []
        total_chars = 0
        for f in sorted(code_dir.rglob("*")):
            if total_chars >= _MAX_CODE_CHARS:
                parts.append("\n... (truncated, code too large) ...")
                break
            if f.suffix == ".py":
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    chunk = f"### {f.name}\n```python\n{content}\n```\n"
                    parts.append(chunk)
                    total_chars += len(chunk)
                except Exception:
                    pass
            elif f.suffix == ".ipynb":
                try:
                    nb_data = json.loads(f.read_text(encoding="utf-8"))
                    cells: list[str] = []
                    for cell in nb_data.get("cells", []):
                        src = "".join(cell.get("source", []))
                        ct = cell.get("cell_type", "code")
                        cells.append(f"[{ct}]\n{src}")
                    chunk = f"### {f.name} (notebook)\n" + "\n---\n".join(cells) + "\n"
                    parts.append(chunk)
                    total_chars += len(chunk)
                except Exception:
                    pass
        return "\n".join(parts)

    def _collect_data_summaries(self) -> str:
        data_dir = self._bundle_dir / "raw" / "data_samples"
        if not data_dir.exists():
            return ""
        try:
            import pandas as pd
        except ImportError:
            return "(pandas not installed — cannot summarize data)"

        parts: list[str] = []
        loaders = {
            ".csv": ("read_csv", {}),
            ".tsv": ("read_csv", {"sep": "\t"}),
            ".parquet": ("read_parquet", {}),
            ".xlsx": ("read_excel", {}),
            ".json": ("read_json", {}),
            ".jsonl": ("read_json", {"lines": True}),
        }
        for f in sorted(data_dir.rglob("*")):
            if f.is_dir():
                continue
            loader_info = loaders.get(f.suffix.lower())
            if not loader_info:
                continue
            method_name, kwargs = loader_info
            try:
                df = getattr(pd, method_name)(str(f), nrows=_MAX_DATA_ROWS, **kwargs)
                desc = (
                    f"### {f.name}\n"
                    f"Shape: {df.shape[0]} rows x {df.shape[1]} columns\n"
                    f"Columns: {list(df.columns)}\n"
                    f"Dtypes:\n{df.dtypes.to_string()}\n"
                    f"First 5 rows:\n{df.head().to_string()}\n"
                )
                parts.append(desc)
            except Exception as exc:
                parts.append(f"### {f.name}\nFailed to load: {exc}\n")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    async def _call_llm(self, prompt: str) -> dict[str, Any]:
        from ouroboros.llm import LLMClient
        client = LLMClient()
        messages = [
            {"role": "system", "content": "You are an ML model analyst. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ]
        response, _usage = await asyncio.to_thread(
            client.chat,
            messages=messages,
            model=self._config.comprehension_model,
            reasoning_effort=self._config.comprehension_effort,
            max_tokens=8192,
        )
        text = response.get("content", "")
        if isinstance(text, list):
            text = " ".join(
                block.get("text", "") for block in text if isinstance(block, dict)
            )
        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        return json.loads(text.strip())

    def _fallback_profile(self) -> dict[str, Any]:
        """Minimal profile when LLM call fails."""
        return {
            "bundle_id": self._bundle_id,
            "task_description": self._read_text("inputs/task.txt") or "unknown",
            "model_type": "other",
            "model_type_confidence": 0.0,
            "framework": "other",
            "framework_confidence": 0.0,
            "algorithm": "unknown",
            "data_format": "tabular",
            "comprehension_confidence": 0.0,
            "comprehension_gaps": ["LLM comprehension failed — using fallback profile"],
        }
