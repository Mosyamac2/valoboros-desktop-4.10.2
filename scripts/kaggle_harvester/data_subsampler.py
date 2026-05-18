"""Data subsampling (infrastructure safety only).

Per plan §0: we subsample large training files so the validation sandbox
doesn't OOM. We do NOT canonicalize filenames, train/test layouts, or
inject any holdout split. The Kaggle test/holdout file is kept intact.

Threshold: 200 MB total extracted size triggers subsampling. Target ~50 MB
on the training file. Stratified by an identifiable target column when
possible; uniform random otherwise. Refuses to shrink any minority class
below 1000 rows.
"""

from __future__ import annotations

import logging
import pathlib
import random
import re
import shutil
import zipfile
from dataclasses import dataclass
from typing import Optional  # noqa: F401  — used by extract_archive

log = logging.getLogger(__name__)

_THRESHOLD_BYTES = 200 * 1024 * 1024
_TARGET_BYTES = 50 * 1024 * 1024
_MIN_MINORITY_ROWS = 1000

# Heuristic patterns for identifying the train vs test files in a typical
# Kaggle data dump. Match case-insensitively against the base filename.
_TRAIN_PATTERNS = [
    re.compile(r"^train(?:[._-].*)?\.csv$", re.IGNORECASE),
    re.compile(r"^training(?:[._-].*)?\.csv$", re.IGNORECASE),
    re.compile(r"^.*train.*\.csv$", re.IGNORECASE),
]
_TEST_PATTERNS = [
    re.compile(r"^test(?:[._-].*)?\.csv$", re.IGNORECASE),
    re.compile(r"^.*test.*\.csv$", re.IGNORECASE),
    re.compile(r"^holdout.*\.csv$", re.IGNORECASE),
]
# Common Kaggle target column names; the agent can override later.
_TARGET_NAME_HINTS = [
    "target", "label", "class", "y", "outcome",
    "survived", "default", "is_attributed", "TARGET",
]


@dataclass
class SubsampleResult:
    applied: bool
    reason: str
    original_bytes: int
    final_bytes: int
    train_file: Optional[pathlib.Path]
    test_file: Optional[pathlib.Path]
    minority_class_rows: Optional[int]  # None if not stratified
    note: str  # one-paragraph human-readable for the description appendix


class ArchiveTooLargeError(RuntimeError):
    """Raised when a Kaggle archive's uncompressed size exceeds the cap."""

    def __init__(self, uncompressed_mb: int, cap_mb: int):
        super().__init__(
            f"archive uncompressed size {uncompressed_mb} MB > cap {cap_mb} MB"
        )
        self.uncompressed_mb = uncompressed_mb
        self.cap_mb = cap_mb


def extract_archive(
    archive: pathlib.Path,
    dest: pathlib.Path,
    *,
    max_uncompressed_mb: Optional[int] = None,
    delete_archive_after: bool = True,
) -> pathlib.Path:
    """Unzip a Kaggle data archive into ``dest``. Returns ``dest``.

    If ``archive`` is already a single non-zip file, copies it instead.

    Pre-extract size guard: when ``max_uncompressed_mb`` is set, the zip
    directory is inspected and the total uncompressed size summed. If it
    exceeds the cap, ``ArchiveTooLargeError`` is raised BEFORE extraction
    starts — protects the filesystem from competitions like amex-default
    whose archives explode 10x+ on disk.

    After a successful extract, the raw archive is unlinked unless
    ``delete_archive_after=False`` — keeps peak disk usage close to the
    extracted size, not 2x it.
    """
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            if max_uncompressed_mb is not None:
                total_uncompressed = sum(zi.file_size for zi in zf.infolist())
                uncompressed_mb = int(total_uncompressed / (1024 * 1024))
                if uncompressed_mb > max_uncompressed_mb:
                    raise ArchiveTooLargeError(uncompressed_mb, max_uncompressed_mb)
            zf.extractall(dest)
        if delete_archive_after:
            try:
                archive.unlink()
            except OSError:
                pass
    else:
        shutil.copy2(archive, dest / archive.name)
    return dest


def _total_size(root: pathlib.Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def _match(name: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.match(name) for p in patterns)


def _find_train_test(root: pathlib.Path) -> tuple[Optional[pathlib.Path], Optional[pathlib.Path]]:
    csvs = [p for p in root.rglob("*.csv") if p.is_file()]
    train = next((p for p in csvs if _match(p.name, _TRAIN_PATTERNS)), None)
    test = next((p for p in csvs if _match(p.name, _TEST_PATTERNS)), None)
    return train, test


def _detect_target_column(df_columns: list[str]) -> Optional[str]:
    lower_to_orig = {c.lower(): c for c in df_columns}
    for hint in _TARGET_NAME_HINTS:
        if hint.lower() in lower_to_orig:
            return lower_to_orig[hint.lower()]
    return None


def _stratified_subsample(train_path: pathlib.Path, target_col: str,
                          target_bytes: int) -> tuple[bool, int, str]:
    """Stratified subsample the training CSV in-place. Returns (ok, minority_rows, note)."""
    import pandas as pd
    df = pd.read_csv(train_path)
    original_rows = len(df)
    if original_rows == 0:
        return False, 0, "training file empty"
    size = train_path.stat().st_size
    fraction = min(1.0, max(0.01, target_bytes / max(size, 1)))
    # Per-class subsample to preserve proportions. include_groups=False is
    # the pandas-2 way to keep the target column as a regular column inside
    # each group instead of letting it become an index.
    parts: list[pd.DataFrame] = []
    for _, group_df in df.groupby(target_col, sort=False):
        n_sample = max(1, int(round(len(group_df) * fraction)))
        parts.append(group_df.sample(n=min(n_sample, len(group_df)),
                                     random_state=42))
    sampled = pd.concat(parts, ignore_index=True)
    minority_rows = int(sampled.groupby(target_col).size().min())
    if minority_rows < _MIN_MINORITY_ROWS:
        return False, minority_rows, (
            f"would shrink minority class to {minority_rows} rows (< {_MIN_MINORITY_ROWS})"
        )
    sampled.to_csv(train_path, index=False)
    return True, minority_rows, (
        f"stratified subsample on '{target_col}': "
        f"{original_rows:,} → {len(sampled):,} rows "
        f"(fraction={fraction:.3f}); minority-class rows preserved at {minority_rows:,}."
    )


def _uniform_subsample(train_path: pathlib.Path, target_bytes: int) -> tuple[bool, str]:
    """Uniform random subsample the training CSV in-place."""
    import pandas as pd
    df = pd.read_csv(train_path)
    original_rows = len(df)
    if original_rows == 0:
        return False, "training file empty"
    size = train_path.stat().st_size
    fraction = min(1.0, max(0.01, target_bytes / max(size, 1)))
    n = max(100, int(round(original_rows * fraction)))
    sampled = df.sample(n=min(n, original_rows), random_state=42)
    sampled.to_csv(train_path, index=False)
    return True, (
        f"uniform random subsample (no target column inferred): "
        f"{original_rows:,} → {len(sampled):,} rows (fraction={fraction:.3f})."
    )


def maybe_subsample(extract_root: pathlib.Path) -> SubsampleResult:
    """Subsample only if the extracted data exceeds the size threshold.

    The training file is rewritten in place inside ``extract_root``. The
    test/holdout file is left untouched. All other files are left untouched
    as well — naming and layout follow Kaggle's own conventions.
    """
    original_bytes = _total_size(extract_root)
    train, test = _find_train_test(extract_root)
    if original_bytes <= _THRESHOLD_BYTES:
        return SubsampleResult(
            applied=False,
            reason="below_threshold",
            original_bytes=original_bytes,
            final_bytes=original_bytes,
            train_file=train, test_file=test,
            minority_class_rows=None,
            note=(
                f"Data total {original_bytes / 1024 / 1024:.1f} MB "
                f"≤ threshold {_THRESHOLD_BYTES / 1024 / 1024:.0f} MB; "
                "no subsampling applied."
            ),
        )
    if train is None:
        return SubsampleResult(
            applied=False,
            reason="no_train_file_identified",
            original_bytes=original_bytes,
            final_bytes=original_bytes,
            train_file=None, test_file=test,
            minority_class_rows=None,
            note=(
                f"Data total {original_bytes / 1024 / 1024:.1f} MB exceeds "
                f"threshold but no recognisable training CSV was found; left as-is."
            ),
        )
    import pandas as pd
    try:
        head = pd.read_csv(train, nrows=10)
    except Exception as e:
        return SubsampleResult(
            applied=False,
            reason=f"read_failed:{type(e).__name__}",
            original_bytes=original_bytes,
            final_bytes=original_bytes,
            train_file=train, test_file=test,
            minority_class_rows=None,
            note=f"Could not peek at {train.name} to plan subsampling: {e}",
        )
    target_col = _detect_target_column(list(head.columns))

    if target_col:
        # Sniff whether the "target" looks like a classification label
        # (low cardinality, stratifiable) or a regression target (high
        # cardinality, continuous — stratification is meaningless and the
        # minority-floor check would always trip).
        try:
            sample = pd.read_csv(train, usecols=[target_col], nrows=20000)
            n_unique = sample[target_col].nunique(dropna=True)
            n_rows = len(sample)
        except Exception:
            n_unique, n_rows = 0, 0
        is_classification_target = (
            n_unique > 0 and n_unique <= 50
            and (n_rows == 0 or n_unique / max(n_rows, 1) <= 0.05)
        )
        if not is_classification_target:
            log.info(
                "subsample: target '%s' has %d unique values in sample of %d rows; "
                "treating as regression / continuous and using uniform subsample.",
                target_col, n_unique, n_rows,
            )
            target_col = None  # fall through to uniform branch below

    if target_col:
        ok, minority, note = _stratified_subsample(train, target_col, _TARGET_BYTES)
        if not ok:
            return SubsampleResult(
                applied=False,
                reason="minority_floor_protection",
                original_bytes=original_bytes,
                final_bytes=original_bytes,
                train_file=train, test_file=test,
                minority_class_rows=minority,
                note=f"Refused stratified subsample on '{target_col}': {note}",
            )
        final_bytes = _total_size(extract_root)
        return SubsampleResult(
            applied=True, reason="stratified",
            original_bytes=original_bytes, final_bytes=final_bytes,
            train_file=train, test_file=test,
            minority_class_rows=minority,
            note=note,
        )

    ok, note = _uniform_subsample(train, _TARGET_BYTES)
    final_bytes = _total_size(extract_root)
    return SubsampleResult(
        applied=ok, reason="uniform" if ok else "uniform_failed",
        original_bytes=original_bytes, final_bytes=final_bytes,
        train_file=train, test_file=test,
        minority_class_rows=None,
        note=note,
    )
