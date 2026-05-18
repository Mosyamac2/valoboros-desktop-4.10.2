"""Bundle assembly + atomic delivery to the Valoboros inbox.

Per plan §0: the harvester does not impose a schema. We assemble the
acquired artifacts into a folder named after the competition (slugified
to be safe on all filesystems) and write a single description text file
whose first part is Kaggle's overview prose and whose appendix records
provenance (kernel author, license, votes), subsampling outcome, and
notebook-size-guard decision. We then zip the folder and atomic-move the
ZIP into the inbox so the validator's watcher never sees a partial write.
"""

from __future__ import annotations

import logging
import pathlib
import re
import shutil
import zipfile
from dataclasses import dataclass
from typing import Optional

from .data_subsampler import SubsampleResult
from .discovery import CompetitionCandidate
from .kernel_picker import PickedKernel

log = logging.getLogger(__name__)

_DESCRIPTION_FILENAME = "kaggle_overview.txt"


@dataclass
class AssembledBundle:
    competition_slug: str
    folder_name: str
    zip_path: pathlib.Path
    bytes_total: int


def _slugify(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return safe[:60] or "unnamed"


def _format_description(
    cand: CompetitionCandidate,
    kernel: PickedKernel,
    subsample: SubsampleResult,
    notebook_note: str,
) -> str:
    parts = [
        f"Kaggle Competition: {cand.title}".strip(),
        f"Slug: {cand.slug}",
        f"URL: {cand.url}",
        f"Organization: {cand.organization or '-'}",
        f"Category: {cand.category or '-'}",
        f"Deadline: {cand.deadline_iso or '-'}",
        f"Inferred domain: {cand.inferred_domain}",
        f"Kaggle-stated evaluation metric: {cand.evaluation_metric or '-'}",
        "",
        "Description (from Kaggle):",
        "----------------------------------------",
        cand.description.strip() or "(no description provided by Kaggle)",
        "",
        "----------------------------------------",
        "Harvester appendix (provenance + infrastructure notes)",
        "----------------------------------------",
        "Source kernel (picked by the harvester):",
        f"  ref:       {kernel.ref}",
        f"  title:     {kernel.title}",
        f"  author:    {kernel.author}",
        f"  votes:     {kernel.votes}",
        f"  language:  {kernel.language}",
        f"  enableGpu: {kernel.enable_gpu}",
        f"  enableInternet: {kernel.enable_internet}",
        f"  url:       {kernel.url}",
        "",
        "Selection rationale:",
        "  Picked a moderate-quality kernel (skipped the top 20% by vote count;",
        "  uniform-random pick from the next 30% band) so that Valoboros has",
        "  real room to find improvements rather than nitpicking a state-of-the-",
        "  art solution. See aux_notes/kaggle_harvester_plan.md §0 + locked",
        "  decision #2 for the constitutional rationale.",
        "",
        "Data subsampling:",
        f"  {subsample.note}",
        "",
        "Notebook size guard:",
        f"  {notebook_note}",
        "",
        "Licensing / attribution:",
        "  The bundle stays on the local machine. Kernel attribution is",
        "  preserved above; consult the kernel URL for its declared license",
        "  before any republication.",
        "",
        "Intentionally NOT produced (LLM-First, BIBLE v5.1):",
        "  - No synthesized eval.py. The validator infers the metric during S0",
        "    comprehension and writes any scripted evaluator it needs itself.",
        "  - No reserved labelled holdout split. The validator carves one from",
        "    the training data when its methodology calls for one.",
        "  - No canonical filenames or schema. Whatever Kaggle delivered, you",
        "    get.",
    ]
    return "\n".join(parts) + "\n"


def assemble(
    cand: CompetitionCandidate,
    kernel: PickedKernel,
    extracted_data_dir: pathlib.Path,
    subsample: SubsampleResult,
    notebook_note: str,
    *,
    inbox_dir: pathlib.Path,
    workdir: pathlib.Path,
    dry_run: bool = False,
) -> AssembledBundle:
    """Build the bundle ZIP and atomic-move it into the inbox.

    ``workdir`` is a temporary parent under which the bundle folder is
    materialized and zipped. The ZIP is moved into ``inbox_dir`` last;
    a partial-write is impossible from the watcher's point of view.
    """
    folder_name = f"{_slugify(cand.slug)}_kaggle_model"
    bundle_root = workdir / folder_name
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle_root.mkdir(parents=True)

    # 1. Description file (with appendix).
    description = _format_description(cand, kernel, subsample, notebook_note)
    (bundle_root / _DESCRIPTION_FILENAME).write_text(description, encoding="utf-8")

    # 2. Kernel source (filename preserved).
    src_target = bundle_root / kernel.source_path.name
    shutil.copy2(kernel.source_path, src_target)

    # 3. Data: copy everything Kaggle gave us, in its natural shape.
    for entry in extracted_data_dir.rglob("*"):
        if not entry.is_file():
            continue
        rel = entry.relative_to(extracted_data_dir)
        target = bundle_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, target)

    # 4. ZIP into the workdir, then atomic-move into the inbox.
    zip_tmp = workdir / f"{folder_name}.zip"
    if zip_tmp.exists():
        zip_tmp.unlink()
    with zipfile.ZipFile(zip_tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in bundle_root.rglob("*"):
            if entry.is_file():
                zf.write(entry, arcname=str(entry.relative_to(bundle_root.parent)))
    bytes_total = zip_tmp.stat().st_size

    if dry_run:
        log.info("dry-run: built %s (%d bytes), leaving in workdir", zip_tmp, bytes_total)
        return AssembledBundle(
            competition_slug=cand.slug,
            folder_name=folder_name,
            zip_path=zip_tmp,
            bytes_total=bytes_total,
        )

    inbox_dir.mkdir(parents=True, exist_ok=True)
    final = inbox_dir / zip_tmp.name
    # Atomic move (on POSIX: same FS guarantees rename atomicity)
    shutil.move(str(zip_tmp), str(final))
    return AssembledBundle(
        competition_slug=cand.slug,
        folder_name=folder_name,
        zip_path=final,
        bytes_total=bytes_total,
    )


def summarize(zip_path: pathlib.Path) -> str:
    """Human-readable summary of a built bundle (used by `verify` subcommand).

    Not a contract check — there is no contract per §0.
    """
    if not zip_path.exists():
        return f"missing: {zip_path}"
    lines = [
        f"Bundle: {zip_path.name}",
        f"Size: {zip_path.stat().st_size / 1024:.1f} KB",
        "Contents:",
    ]
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            lines.append(f"  {info.filename}  ({info.file_size / 1024:.1f} KB)")
    return "\n".join(lines)
