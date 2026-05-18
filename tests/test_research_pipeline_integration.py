"""Tests for per-model research integration into the pipeline."""
import asyncio
import pytest
import zipfile
from pathlib import Path
from unittest.mock import patch
from ouroboros.validation.types import ValidationConfig, ModelProfile
from ouroboros.validation.pipeline import ValidationPipeline


def _make_test_bundle(tmp_path):
    from ouroboros.tools.model_intake import _ingest_model_artifacts_impl
    code_zip = tmp_path / "code.zip"
    with zipfile.ZipFile(code_zip, "w") as zf:
        zf.writestr("train.py", "import pandas\nprint('hello')\n")
    val_dir = tmp_path / "validations"
    val_dir.mkdir()
    bundle_id = _ingest_model_artifacts_impl(val_dir, str(code_zip), "Test credit scoring model")
    return val_dir / bundle_id


def test_research_disabled_skips(tmp_path):
    """When pre_research=False, _research_model returns None without error."""
    bundle_dir = _make_test_bundle(tmp_path)
    config = ValidationConfig(pre_research=False, auto_self_assess=False, auto_improve=False)
    pipeline = ValidationPipeline(bundle_dir.name, bundle_dir, Path(__file__).parent.parent, config)
    profile = ModelProfile(
        bundle_id="test", task_description="test",
        model_type="classification", model_type_confidence=0.9,
        framework="sklearn", framework_confidence=0.9,
        algorithm="RF", data_format="tabular",
    )
    result = asyncio.run(pipeline._research_model(profile))
    assert result is None


def test_research_failure_is_nonblocking(tmp_path):
    """If arxiv/LLM fails, _research_model returns None without crashing."""
    bundle_dir = _make_test_bundle(tmp_path)
    config = ValidationConfig(pre_research=True, auto_self_assess=False, auto_improve=False)
    pipeline = ValidationPipeline(bundle_dir.name, bundle_dir, Path(__file__).parent.parent, config)
    profile = ModelProfile(
        bundle_id="test", task_description="test",
        model_type="regression", model_type_confidence=0.9,
        framework="catboost", framework_confidence=0.9,
        algorithm="CatBoost", data_format="tabular",
    )
    with patch("ouroboros.validation.model_researcher.ModelResearcher.research",
               side_effect=ConnectionError("arxiv down")):
        result = asyncio.run(pipeline._research_model(profile))
    assert result is None


def test_methodology_planner_reads_arxiv_recent(tmp_path):
    """Planner's _gather_knowledge includes arxiv_recent.md if it exists."""
    from ouroboros.validation.methodology_planner import MethodologyPlanner
    from ouroboros.validation.check_registry import CheckRegistry
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "arxiv_recent.md").write_text(
        "# Recent Papers\n- CatBoost validation technique from 2026\n"
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "methodology").mkdir()
    profile = ModelProfile(
        bundle_id="test", task_description="test",
        model_type="regression", model_type_confidence=0.9,
        framework="catboost", framework_confidence=0.9,
        algorithm="CatBoost", data_format="tabular",
    )
    repo_dir = Path(__file__).parent.parent
    planner = MethodologyPlanner(bundle_dir, profile, CheckRegistry(repo_dir), ValidationConfig(), knowledge_dir)
    kb = planner._gather_knowledge()
    assert "CatBoost validation technique" in kb
