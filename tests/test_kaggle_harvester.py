"""Unit tests for the Kaggle harvester package.

Tests are mock-based — no live Kaggle calls. Verify:
- auth shape parsing (bearer vs legacy)
- moderate-tier kernel band math
- subsample stratification + minority-floor protection
- notebook-size-guard threshold behavior
- bundle assembler produces a zip with the expected files
- state.json roundtrip + seen() dedup
- allow-list parser
"""

from __future__ import annotations

import json
import pathlib
import sys
import zipfile

import pytest


def _strip_kaggle_env(monkeypatch):
    """Wipe Kaggle env vars + ~/.kaggle/kaggle.json from the harvester's view.

    We point HOME at a temp dir per test so creds-on-disk can't leak in.
    """
    for key in ("KAGGLE_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY"):
        monkeypatch.delenv(key, raising=False)


REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


# ----------------------------------------------------------------------
# auth.py
# ----------------------------------------------------------------------

class TestAuth:
    def test_bearer_token_from_env(self, monkeypatch, tmp_path):
        _strip_kaggle_env(monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("KAGGLE_TOKEN", "KGAT_" + "a" * 32)
        # Reload module so the home-path constant picks up the new HOME.
        import importlib
        from scripts.kaggle_harvester import auth as auth_mod
        importlib.reload(auth_mod)
        auth = auth_mod.load_credentials()
        assert auth.is_bearer
        assert auth.is_valid_shape()
        assert auth.auth_header() == {"Authorization": "Bearer KGAT_" + "a" * 32}

    def test_legacy_basic_from_json(self, monkeypatch, tmp_path):
        _strip_kaggle_env(monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path))
        kdir = tmp_path / ".kaggle"
        kdir.mkdir()
        (kdir / "kaggle.json").write_text(
            json.dumps({"username": "alice", "key": "b" * 32}),
            encoding="utf-8",
        )
        import importlib
        from scripts.kaggle_harvester import auth as auth_mod
        importlib.reload(auth_mod)
        auth = auth_mod.load_credentials()
        assert auth.is_legacy_basic
        assert auth.is_valid_shape()
        header = auth.auth_header()
        assert header["Authorization"].startswith("Basic ")

    def test_missing_credentials_raises(self, monkeypatch, tmp_path):
        _strip_kaggle_env(monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path))
        import importlib
        from scripts.kaggle_harvester import auth as auth_mod
        importlib.reload(auth_mod)
        with pytest.raises(auth_mod.KaggleAuthError):
            auth_mod.load_credentials()

    def test_malformed_bearer_rejected(self, monkeypatch, tmp_path):
        _strip_kaggle_env(monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("KAGGLE_TOKEN", "not_a_valid_token")
        import importlib
        from scripts.kaggle_harvester import auth as auth_mod
        importlib.reload(auth_mod)
        with pytest.raises(auth_mod.KaggleAuthError, match="wrong shape"):
            auth_mod.load_credentials()


# ----------------------------------------------------------------------
# kernel_picker.py: moderate-tier band math
# ----------------------------------------------------------------------

class TestKernelPicker:
    def test_moderate_band_skips_top_20_takes_next_30(self):
        from scripts.kaggle_harvester.kernel_picker import _moderate_band
        kernels = [{"ref": f"u/k{i}", "totalVotes": 100 - i} for i in range(20)]
        band = _moderate_band(kernels)
        # Skip first 4 (20% of 20), take next 6 (30% of 20).
        assert [k["ref"] for k in band] == [f"u/k{i}" for i in range(4, 10)]

    def test_moderate_band_tiny_pool_returns_below_top(self):
        from scripts.kaggle_harvester.kernel_picker import _moderate_band
        kernels = [{"ref": f"u/k{i}", "totalVotes": 10 - i} for i in range(3)]
        band = _moderate_band(kernels)
        # Tiny pool — exclude the absolute top.
        assert band[0]["ref"] == "u/k1"

    def test_moderate_band_empty_input(self):
        from scripts.kaggle_harvester.kernel_picker import _moderate_band
        assert _moderate_band([]) == []


# ----------------------------------------------------------------------
# data_subsampler.py
# ----------------------------------------------------------------------

class TestSubsampler:
    def _write_train_csv(self, path: pathlib.Path, n_rows: int, target_values=(0, 1)):
        import pandas as pd
        import numpy as np
        rng = np.random.default_rng(seed=42)
        df = pd.DataFrame({
            "feat_a": rng.normal(size=n_rows),
            "feat_b": rng.integers(0, 100, size=n_rows),
            "target": rng.choice(target_values, size=n_rows),
        })
        df.to_csv(path, index=False)

    def test_below_threshold_no_subsample(self, tmp_path):
        from scripts.kaggle_harvester.data_subsampler import maybe_subsample
        self._write_train_csv(tmp_path / "train.csv", n_rows=1000)
        result = maybe_subsample(tmp_path)
        assert not result.applied
        assert result.reason == "below_threshold"

    def test_stratified_when_target_inferred(self, tmp_path, monkeypatch):
        from scripts.kaggle_harvester import data_subsampler as ds
        # Lower the threshold so a moderately-large file triggers subsample.
        # With 20k rows of balanced 0/1 and fraction=0.5, each class keeps ~5000.
        monkeypatch.setattr(ds, "_THRESHOLD_BYTES", 50_000)
        monkeypatch.setattr(ds, "_TARGET_BYTES", 100_000)
        monkeypatch.setattr(ds, "_MIN_MINORITY_ROWS", 100)
        self._write_train_csv(tmp_path / "train.csv", n_rows=20000)
        result = ds.maybe_subsample(tmp_path)
        assert result.applied, f"got: {result}"
        assert result.reason == "stratified"
        assert result.minority_class_rows is not None
        assert result.minority_class_rows >= 100

    def test_refuses_when_minority_would_collapse(self, tmp_path, monkeypatch):
        import pandas as pd
        from scripts.kaggle_harvester import data_subsampler as ds
        monkeypatch.setattr(ds, "_THRESHOLD_BYTES", 1_000)
        monkeypatch.setattr(ds, "_TARGET_BYTES", 500)
        monkeypatch.setattr(ds, "_MIN_MINORITY_ROWS", 1000)
        # Tiny minority class
        df = pd.DataFrame({
            "feat": list(range(1000)),
            "target": [0] * 980 + [1] * 20,
        })
        (tmp_path / "train.csv").write_text(df.to_csv(index=False))
        # Make sure file exceeds threshold
        result = ds.maybe_subsample(tmp_path)
        assert not result.applied
        assert result.reason == "minority_floor_protection"


# ----------------------------------------------------------------------
# notebook_size_guard.py
# ----------------------------------------------------------------------

class TestNotebookSizeGuard:
    def _write_nb(self, path: pathlib.Path, n_output_cells: int, output_size_per_cell: int):
        import nbformat
        nb = nbformat.v4.new_notebook()
        for i in range(n_output_cells):
            cell = nbformat.v4.new_code_cell(source=f"print({i})")
            cell["outputs"] = [
                nbformat.v4.new_output(
                    output_type="stream",
                    name="stdout",
                    text="X" * output_size_per_cell,
                )
            ]
            nb.cells.append(cell)
        nbformat.write(nb, str(path))

    def test_below_threshold_preserves_outputs(self, tmp_path):
        from scripts.kaggle_harvester.notebook_size_guard import maybe_strip_outputs
        nb = tmp_path / "small.ipynb"
        self._write_nb(nb, n_output_cells=2, output_size_per_cell=100)
        stripped, note = maybe_strip_outputs(nb, threshold_bytes=5 * 1024 * 1024)
        assert stripped is False
        assert "preserved" in note

    def test_above_threshold_clears_outputs(self, tmp_path):
        import nbformat
        from scripts.kaggle_harvester.notebook_size_guard import maybe_strip_outputs
        nb = tmp_path / "huge.ipynb"
        self._write_nb(nb, n_output_cells=20, output_size_per_cell=20000)
        size_before = nb.stat().st_size
        stripped, note = maybe_strip_outputs(nb, threshold_bytes=10 * 1024)
        assert stripped is True
        assert "cleared" in note
        nb_after = nbformat.read(str(nb), as_version=4)
        for cell in nb_after.cells:
            if cell.cell_type == "code":
                assert cell.outputs == []
        assert nb.stat().st_size < size_before


# ----------------------------------------------------------------------
# state.py
# ----------------------------------------------------------------------

class TestState:
    def test_roundtrip(self, tmp_path):
        from scripts.kaggle_harvester import state as state_mod
        path = tmp_path / "state.json"
        s = state_mod.HarvesterState()
        s.record_harvest("titanic", tmp_path / "titanic.zip", "alice/foo")
        s.record_skip("blocked-comp", "rules_not_accepted")
        s.block("permanently-broken")
        state_mod.save(s, path)
        loaded = state_mod.load(path)
        assert len(loaded.harvested) == 1
        assert loaded.harvested[0].slug == "titanic"
        assert "blocked-comp" in loaded.skipped_slugs()
        assert "permanently-broken" in loaded.blocked_set()

    def test_seen_dedup(self, tmp_path):
        from scripts.kaggle_harvester.state import HarvesterState
        s = HarvesterState()
        s.record_harvest("a", tmp_path / "a.zip", "u/k")
        s.record_skip("b", "reason")
        s.block("c")
        assert s.seen() == {"a", "b", "c"}


# ----------------------------------------------------------------------
# allow_list.py
# ----------------------------------------------------------------------

class TestAllowList:
    def test_parse_file_with_comments(self, tmp_path):
        from scripts.kaggle_harvester.allow_list import AllowList
        path = tmp_path / "allow.txt"
        path.write_text(
            "# comment\n"
            "titanic\n"
            "\n"
            "  house-prices-advanced-regression-techniques  \n"
            "# another comment\n"
            "spaceship-titanic\n",
            encoding="utf-8",
        )
        allow = AllowList.load(path)
        assert allow.slugs == [
            "titanic",
            "house-prices-advanced-regression-techniques",
            "spaceship-titanic",
        ]

    def test_missing_file_is_empty(self, tmp_path):
        from scripts.kaggle_harvester.allow_list import AllowList
        allow = AllowList.load(tmp_path / "nope.txt")
        assert allow.slugs == []
        assert not bool(allow)


# ----------------------------------------------------------------------
# bundle_assembler.py
# ----------------------------------------------------------------------

class TestBundleAssembler:
    def _make_inputs(self, tmp_path: pathlib.Path):
        from scripts.kaggle_harvester.data_subsampler import SubsampleResult
        from scripts.kaggle_harvester.discovery import CompetitionCandidate
        from scripts.kaggle_harvester.kernel_picker import PickedKernel

        cand = CompetitionCandidate(
            slug="ulsu-titanic-comp",
            title="Test Competition",
            description="Predict the test target.",
            category="playground",
            evaluation_metric="AUC",
            deadline_iso="2025-01-01T00:00:00Z",
            organization="Test Org",
            url="https://www.kaggle.com/competitions/ulsu-titanic-comp",
            inferred_domain="tabular",
        )
        kernel_dir = tmp_path / "kernel"
        kernel_dir.mkdir()
        kernel_src = kernel_dir / "alice-baseline.ipynb"
        kernel_src.write_text(
            '{"cells":[],"metadata":{},"nbformat":4,"nbformat_minor":5}',
            encoding="utf-8",
        )
        kernel = PickedKernel(
            ref="alice/baseline",
            title="Baseline notebook",
            author="alice",
            votes=42,
            enable_gpu=False,
            enable_internet=False,
            language="python",
            url="https://www.kaggle.com/code/alice/baseline",
            source_path=kernel_src,
            metadata={},
        )
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "train.csv").write_text("a,b,target\n1,2,0\n3,4,1\n", encoding="utf-8")
        (data_dir / "test.csv").write_text("a,b\n5,6\n", encoding="utf-8")

        subsample = SubsampleResult(
            applied=False, reason="below_threshold",
            original_bytes=200, final_bytes=200,
            train_file=data_dir / "train.csv",
            test_file=data_dir / "test.csv",
            minority_class_rows=None,
            note="below threshold; no subsampling applied.",
        )
        return cand, kernel, data_dir, subsample

    def test_assemble_dry_run_keeps_zip_in_workdir(self, tmp_path):
        from scripts.kaggle_harvester.bundle_assembler import assemble
        cand, kernel, data_dir, subsample = self._make_inputs(tmp_path)
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        inbox = tmp_path / "inbox"
        bundle = assemble(
            cand=cand, kernel=kernel,
            extracted_data_dir=data_dir, subsample=subsample,
            notebook_note="outputs preserved.",
            inbox_dir=inbox, workdir=workdir,
            dry_run=True,
        )
        assert bundle.zip_path.exists()
        assert bundle.zip_path.parent == workdir
        assert not inbox.exists()  # nothing delivered

    def test_assemble_real_run_atomic_move_to_inbox(self, tmp_path):
        from scripts.kaggle_harvester.bundle_assembler import assemble
        cand, kernel, data_dir, subsample = self._make_inputs(tmp_path)
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        inbox = tmp_path / "inbox"
        bundle = assemble(
            cand=cand, kernel=kernel,
            extracted_data_dir=data_dir, subsample=subsample,
            notebook_note="outputs preserved.",
            inbox_dir=inbox, workdir=workdir,
            dry_run=False,
        )
        assert bundle.zip_path.exists()
        assert bundle.zip_path.parent == inbox

    def test_bundle_contents_match_expectations(self, tmp_path):
        from scripts.kaggle_harvester.bundle_assembler import assemble, summarize
        cand, kernel, data_dir, subsample = self._make_inputs(tmp_path)
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        inbox = tmp_path / "inbox"
        bundle = assemble(
            cand=cand, kernel=kernel,
            extracted_data_dir=data_dir, subsample=subsample,
            notebook_note="outputs preserved.",
            inbox_dir=inbox, workdir=workdir,
            dry_run=False,
        )
        with zipfile.ZipFile(bundle.zip_path) as zf:
            names = sorted(zf.namelist())
        # Top-level folder named after the competition.
        assert any("ulsu-titanic-comp_kaggle_model/" in n for n in names)
        # Description file is present.
        assert any(n.endswith("kaggle_overview.txt") for n in names)
        # Kernel source preserved by name.
        assert any(n.endswith("alice-baseline.ipynb") for n in names)
        # Data files preserved.
        assert any(n.endswith("train.csv") for n in names)
        assert any(n.endswith("test.csv") for n in names)
        # No invented files per §0.
        for forbidden in ("eval.py", "holdout_truth.csv", "SAMPLING.txt", "deps.txt"):
            assert not any(n.endswith(forbidden) for n in names), (
                f"Harvester invented a forbidden filename: {forbidden}"
            )

    def test_summarize_output(self, tmp_path):
        from scripts.kaggle_harvester.bundle_assembler import assemble, summarize
        cand, kernel, data_dir, subsample = self._make_inputs(tmp_path)
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        inbox = tmp_path / "inbox"
        bundle = assemble(
            cand=cand, kernel=kernel,
            extracted_data_dir=data_dir, subsample=subsample,
            notebook_note="outputs preserved.",
            inbox_dir=inbox, workdir=workdir,
            dry_run=False,
        )
        summary = summarize(bundle.zip_path)
        assert "Bundle:" in summary
        assert "Contents:" in summary
        assert "kaggle_overview.txt" in summary


# ----------------------------------------------------------------------
# discovery.py
# ----------------------------------------------------------------------

class TestDiscovery:
    def test_domain_inference_keywords(self):
        from scripts.kaggle_harvester.discovery import _infer_domain
        assert _infer_domain("Loan Default Prediction",
                             "Predict consumer credit defaults from tabular features.") == "tabular"
        assert _infer_domain("Toxic Comment Classification",
                             "Classify text comments by toxicity.") == "nlp"
        assert _infer_domain("Whale Identification",
                             "Recognize whales in images.") == "cv"
        assert _infer_domain("Generic Stuff",
                             "Some words without specific signal.") == "other"

    def test_candidate_parsing_from_kaggle_response(self):
        from scripts.kaggle_harvester.discovery import _to_candidate
        raw = {
            "titleNullable": "Test Competition",
            "urlNullable": "https://www.kaggle.com/competitions/test-slug",
            "descriptionNullable": "Predict the regression target.",
            "categoryNullable": "playground",
            "evaluationMetricNullable": "RMSE",
            "deadlineNullable": "2020-01-01T00:00:00Z",
            "organizationNameNullable": "Test Org",
        }
        cand = _to_candidate(raw)
        assert cand is not None
        assert cand.slug == "test-slug"
        assert cand.evaluation_metric == "RMSE"
        assert cand.is_closed is True  # 2020 is in the past

    def test_candidate_skipped_when_no_url(self):
        from scripts.kaggle_harvester.discovery import _to_candidate
        assert _to_candidate({"titleNullable": "x"}) is None
