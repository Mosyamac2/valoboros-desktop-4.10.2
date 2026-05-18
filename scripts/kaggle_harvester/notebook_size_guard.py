"""Notebook size guard.

Per plan §0 + §6: the harvester does not touch notebook *content*. The
only operation applied to the notebook is *output stripping*, and only
when the notebook exceeds a size threshold (default 5 MB) that suggests
it is bloated with rendered images / progress bars / large dataframe
HTML. Below the threshold, outputs are kept (sometimes informative —
leaderboard scores, exploration plots).

Path remapping, GPU detection, dependency extraction, and other content
analyses live in the validator agent, not here.
"""

from __future__ import annotations

import logging
import pathlib

log = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 5 * 1024 * 1024


def maybe_strip_outputs(
    notebook_path: pathlib.Path,
    *,
    threshold_bytes: int = _DEFAULT_THRESHOLD,
) -> tuple[bool, str]:
    """If the notebook is larger than the threshold, clear output cells.

    Returns ``(stripped, note)`` where ``note`` is a human-readable
    description of what (if anything) was done, suitable for inclusion in
    the bundle's description appendix.

    Non-notebook files are left untouched and reported as such.
    """
    if not notebook_path.exists():
        return False, f"notebook not found: {notebook_path}"
    size = notebook_path.stat().st_size
    if notebook_path.suffix.lower() != ".ipynb":
        return False, (
            f"source is {notebook_path.name} ({size / 1024:.1f} KB), not an .ipynb; "
            "no output-stripping needed."
        )
    if size <= threshold_bytes:
        return False, (
            f"notebook size {size / 1024:.1f} KB "
            f"≤ threshold {threshold_bytes / 1024:.0f} KB; outputs preserved."
        )
    try:
        import nbformat
    except ImportError:  # pragma: no cover
        return False, "nbformat not installed; outputs left as-is"

    try:
        nb = nbformat.read(str(notebook_path), as_version=4)
    except Exception as e:
        return False, f"could not parse notebook: {type(e).__name__}: {e}"

    cleared = 0
    for cell in nb.cells:
        if cell.get("cell_type") == "code":
            if cell.get("outputs"):
                cleared += len(cell["outputs"])
                cell["outputs"] = []
            cell["execution_count"] = None

    nbformat.write(nb, str(notebook_path))
    new_size = notebook_path.stat().st_size
    return True, (
        f"notebook size {size / 1024 / 1024:.1f} MB exceeded threshold "
        f"{threshold_bytes / 1024 / 1024:.0f} MB; "
        f"cleared {cleared} output blocks ({new_size / 1024:.1f} KB after). "
        "Cell code and markdown unchanged."
    )
