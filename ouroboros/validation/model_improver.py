"""
Ouroboros validation platform — model improver (side agent).

Implements hard recommendations by modifying model code via LLM,
then runs the modified code in the sandbox.  Only hard recommendations
enter the improvement cycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from ouroboros.validation.sandbox import ModelSandbox
from ouroboros.validation.types import (
    ImproverResult,
    ImprovementRecommendation,
    SandboxResult,
    ValidationConfig,
)

log = logging.getLogger(__name__)

_IMPROVE_PROMPT = """\
You are a code modification agent. Apply the following improvement to the model code.

## Improvement
Problem: {problem}
Recommendation: {recommendation}
Implementation sketch: {sketch}

## Original code ({filename})
```python
{code}
```

## Instructions
Return ONLY the complete modified file content. No explanations, no markdown fences.
Apply the improvement while preserving the rest of the code.
"""


class ModelImprover:
    """LLM-based agent that implements improvement recommendations."""

    def __init__(
        self,
        bundle_dir: Path,
        recommendations: list[ImprovementRecommendation],
        sandbox: ModelSandbox,
        config: ValidationConfig,
    ) -> None:
        self._bundle_dir = Path(bundle_dir)
        self._original_dir = self._bundle_dir / "raw" / "model_code"
        self._improved_dir = self._bundle_dir / "improvement" / "implementation"
        # Filter to hard recommendations only, sorted by priority
        self._plan = sorted(
            [r for r in recommendations if r.kind == "hard"],
            key=lambda r: r.priority,
        )
        self._sandbox = sandbox
        self._config = config

    async def implement(self) -> ImproverResult:
        """Apply hard recommendations one by one."""
        if not self._plan:
            return ImproverResult(
                recommendations_applied=[],
                recommendations_skipped=[],
                modified_files=[],
            )

        # Copy original code to improvement directory
        self._improved_dir.mkdir(parents=True, exist_ok=True)
        if self._original_dir.exists():
            for f in self._original_dir.rglob("*"):
                if f.is_file():
                    dest = self._improved_dir / f.relative_to(self._original_dir)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)

        applied: list[str] = []
        skipped: list[tuple[str, str]] = []
        modified_files: list[str] = []
        last_sandbox: SandboxResult | None = None

        for rec in self._plan:
            try:
                modified = await self._apply_recommendation(rec)
                if modified:
                    modified_files.extend(modified)
                    # Test that modified code still runs
                    test_result = self._test_modified_code()
                    last_sandbox = test_result
                    if test_result.returncode == 0:
                        applied.append(rec.finding_check_id)
                    else:
                        # Revert this change — restore from original
                        skipped.append((rec.finding_check_id, f"Sandbox failed: {test_result.stderr[:200]}"))
                        self._revert_files(modified)
                else:
                    skipped.append((rec.finding_check_id, "LLM produced no modifications"))
            except Exception as exc:
                skipped.append((rec.finding_check_id, f"Error: {exc}"))

        # Extract metrics from final sandbox output
        new_metrics = self._parse_metrics(last_sandbox) if last_sandbox else None

        return ImproverResult(
            recommendations_applied=applied,
            recommendations_skipped=skipped,
            modified_files=list(set(modified_files)),
            sandbox_output=last_sandbox,
            new_metrics=new_metrics,
        )

    async def _apply_recommendation(self, rec: ImprovementRecommendation) -> list[str]:
        """Use LLM to modify code files based on a recommendation.

        Targets ``.py`` files first; falls back to ``.ipynb`` notebooks
        (Kaggle bundles arrive as notebooks). For notebooks the code cells
        are concatenated into a single virtual script for the LLM, then
        the modified text is written back into one consolidated code cell
        — losing the original cell partition is the price of keeping this
        path simple, but the resulting notebook still executes top-to-bottom.
        """
        py_files = sorted(self._improved_dir.rglob("*.py"))
        ipynb_files = sorted(self._improved_dir.rglob("*.ipynb"))
        if not py_files and not ipynb_files:
            return []

        if py_files:
            target_file = py_files[0]
            original_code = target_file.read_text(encoding="utf-8", errors="replace")
            is_notebook = False
            nb_obj = None
        else:
            target_file = ipynb_files[0]
            try:
                import nbformat
                nb_obj = nbformat.read(str(target_file), as_version=4)
            except Exception as exc:
                log.warning("Could not parse notebook %s: %s", target_file, exc)
                return []
            code_cells = [c for c in nb_obj.cells if c.get("cell_type") == "code"]
            original_code = "\n\n# ---- next cell ----\n".join(
                "".join(c.get("source", "")) if isinstance(c.get("source"), list)
                else str(c.get("source", ""))
                for c in code_cells
            )
            is_notebook = True

        prompt = _IMPROVE_PROMPT.format(
            problem=rec.problem,
            recommendation=rec.recommendation,
            sketch=rec.implementation_sketch or "(no sketch provided)",
            filename=target_file.name,
            code=original_code[:50000],
        )

        try:
            from ouroboros.llm import LLMClient
            client = LLMClient()
            response, _usage = await asyncio.to_thread(
                client.chat,
                messages=[
                    {"role": "system",
                     "content": "You modify Python code. Return only the modified code."},
                    {"role": "user", "content": prompt},
                ],
                model=self._config.improvement_model,
                reasoning_effort="medium",
                max_tokens=16384,
            )
            text = response.get("content", "")
            if isinstance(text, list):
                text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            if not text or text == original_code:
                return []

            if is_notebook and nb_obj is not None:
                import nbformat
                # Drop existing code cells, replace with one consolidated cell
                # carrying the LLM-modified code. Preserve any markdown cells
                # in original order — keeps narration around the new code.
                preserved: list = []
                code_inserted = False
                for cell in nb_obj.cells:
                    if cell.get("cell_type") == "code":
                        if not code_inserted:
                            preserved.append(nbformat.v4.new_code_cell(source=text))
                            code_inserted = True
                        # else: drop subsequent code cells
                    else:
                        preserved.append(cell)
                if not code_inserted:
                    preserved.append(nbformat.v4.new_code_cell(source=text))
                nb_obj.cells = preserved
                nbformat.write(nb_obj, str(target_file))
            else:
                target_file.write_text(text, encoding="utf-8")
            return [str(target_file.relative_to(self._improved_dir))]
        except Exception as exc:
            log.warning("LLM code modification failed: %s", exc)

        return []

    def _test_modified_code(self) -> SandboxResult:
        """Regression check on the modified code: must still parse as Python.

        We deliberately do NOT execute the full kernel in the hermetic
        validation sandbox — real ML kernels require numpy/pandas/sklearn/
        torch/etc. and the hermetic sandbox is intentionally empty. Whether
        the modified model still produces sensible metrics is the
        revalidation pipeline's job (S2–S8 with deps installed). At this
        layer the right gate is "does the modified code still parse as
        valid Python after stripping jupyter magics".
        """
        def _strip_jupyter_magics(text: str) -> str:
            out_lines: list[str] = []
            in_cell_magic = False
            for line in text.splitlines():
                s = line.lstrip()
                if s.startswith("%%"):
                    in_cell_magic = True
                    continue
                if in_cell_magic and (not line.strip() or line[0] in " \t"):
                    continue
                in_cell_magic = False
                if s.startswith("%") or s.startswith("!"):
                    continue
                out_lines.append(line)
            return "\n".join(out_lines)

        py_files = sorted(self._improved_dir.rglob("*.py"))
        ipynb_files = sorted(self._improved_dir.rglob("*.ipynb"))

        if py_files:
            source = py_files[0].read_text(encoding="utf-8", errors="replace")
            stripped = _strip_jupyter_magics(source)
            filename = py_files[0].name
        elif ipynb_files:
            try:
                import nbformat
                nb = nbformat.read(str(ipynb_files[0]), as_version=4)
            except Exception as exc:
                return SandboxResult(
                    returncode=-1, stdout="", stderr=f"Could not parse notebook: {exc}",
                    duration_sec=0, oom_killed=False, timeout_killed=False,
                )
            stripped = "\n\n".join(
                _strip_jupyter_magics(
                    "".join(c.get("source", "")) if isinstance(c.get("source"), list)
                    else str(c.get("source", ""))
                )
                for c in nb.cells if c.get("cell_type") == "code"
            )
            filename = ipynb_files[0].name
        else:
            return SandboxResult(
                returncode=-1, stdout="", stderr="No Python or notebook files found",
                duration_sec=0, oom_killed=False, timeout_killed=False,
            )

        try:
            compile(stripped, filename, mode="exec")
            return SandboxResult(
                returncode=0, stdout="syntax OK", stderr="",
                duration_sec=0, oom_killed=False, timeout_killed=False,
            )
        except SyntaxError as exc:
            return SandboxResult(
                returncode=1, stdout="",
                stderr=f"SyntaxError after modification: {exc.msg} at line {exc.lineno}",
                duration_sec=0, oom_killed=False, timeout_killed=False,
            )

    def _revert_files(self, modified: list[str]) -> None:
        """Revert modified files back to originals."""
        for rel_path in modified:
            orig = self._original_dir / rel_path
            dest = self._improved_dir / rel_path
            if orig.exists():
                shutil.copy2(orig, dest)

    @staticmethod
    def _parse_metrics(result: SandboxResult) -> dict[str, float] | None:
        """Try to extract metrics from sandbox stdout (JSON on last line)."""
        if not result.stdout.strip():
            return None
        for line in reversed(result.stdout.strip().splitlines()):
            try:
                data = json.loads(line)
                if isinstance(data, dict) and all(isinstance(v, (int, float)) for v in data.values()):
                    return {k: float(v) for k, v in data.items()}
            except (json.JSONDecodeError, ValueError):
                continue
        return None
