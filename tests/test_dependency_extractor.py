"""Tests for deterministic dependency extraction."""
import json
import pytest
from pathlib import Path
from ouroboros.validation.dependency_extractor import DependencyExtractor, DependencyReport


@pytest.fixture
def code_dir(tmp_path):
    d = tmp_path / "code"
    d.mkdir()
    return d


def test_extracts_simple_imports(code_dir):
    """Extracts imports from a simple .py file."""
    (code_dir / "train.py").write_text(
        "import pandas as pd\nimport numpy as np\nfrom sklearn.ensemble import RandomForestClassifier\n"
    )
    ext = DependencyExtractor(code_dir)
    report = ext.extract()
    assert "pandas" in report.imports_found
    assert "numpy" in report.imports_found
    assert "sklearn" in report.imports_found
    # sklearn should be mapped to scikit-learn
    assert "scikit-learn" in report.pip_packages


def test_extracts_from_notebook(code_dir):
    """Extracts imports from .ipynb notebook cells."""
    nb = {
        "cells": [
            {"cell_type": "code", "source": ["import lightgbm as lgb\n", "import catboost\n"]},
            {"cell_type": "markdown", "source": ["# This is markdown"]},
            {"cell_type": "code", "source": ["from polars import DataFrame\n"]},
        ],
        "metadata": {},
        "nbformat": 4,
    }
    (code_dir / "model.ipynb").write_text(json.dumps(nb))
    ext = DependencyExtractor(code_dir)
    report = ext.extract()
    assert "lightgbm" in report.imports_found
    assert "catboost" in report.imports_found
    assert "polars" in report.imports_found
    assert "lightgbm" in report.pip_packages
    assert "polars" in report.pip_packages


def test_filters_stdlib(code_dir):
    """Stdlib modules (os, sys, json) are filtered out."""
    (code_dir / "utils.py").write_text(
        "import os\nimport sys\nimport json\nimport pandas\n"
    )
    ext = DependencyExtractor(code_dir)
    report = ext.extract()
    assert "os" not in report.imports_found
    assert "sys" not in report.imports_found
    assert "json" not in report.imports_found
    assert "pandas" in report.imports_found
    assert "os" in report.stdlib


def test_filters_local_modules(code_dir):
    """Local .py files in code_dir are not treated as pip packages."""
    (code_dir / "utils.py").write_text("def helper(): pass\n")
    (code_dir / "train.py").write_text("from utils import helper\nimport pandas\n")
    ext = DependencyExtractor(code_dir)
    report = ext.extract()
    assert "pandas" in report.imports_found
    assert "utils" not in report.imports_found  # local module, filtered


def test_import_to_pip_mapping(code_dir):
    """Import names are correctly mapped to pip package names."""
    (code_dir / "model.py").write_text(
        "import sklearn\nimport cv2\nimport PIL\nimport yaml\n"
    )
    ext = DependencyExtractor(code_dir)
    report = ext.extract()
    assert "scikit-learn" in report.pip_packages
    assert "opencv-python" in report.pip_packages
    assert "pillow" in report.pip_packages
    assert "pyyaml" in report.pip_packages


def test_pip_magic_from_notebook(code_dir):
    """Detects %pip install and !pip install in notebooks."""
    nb = {
        "cells": [
            {"cell_type": "code", "source": ["%pip install lightgbm catboost\n"]},
            {"cell_type": "code", "source": ["!pip install xgboost\n"]},
        ],
        "metadata": {},
        "nbformat": 4,
    }
    (code_dir / "setup.ipynb").write_text(json.dumps(nb))
    ext = DependencyExtractor(code_dir)
    report = ext.extract()
    assert "lightgbm" in report.pip_magic
    assert "catboost" in report.pip_magic
    assert "xgboost" in report.pip_magic


def test_requirements_txt(code_dir):
    """Parses requirements.txt if present."""
    (code_dir / "requirements.txt").write_text(
        "pandas>=2.0\nnumpy\nscikit-learn==1.5.0\n# comment\n\nlightgbm\n"
    )
    (code_dir / "train.py").write_text("import pandas\n")
    ext = DependencyExtractor(code_dir)
    report = ext.extract()
    assert "pandas>=2.0" in report.requirements_txt
    assert "lightgbm" in report.requirements_txt
    assert len(report.requirements_txt) == 4


def test_all_packages_merges_and_deduplicates(code_dir):
    """all_packages() merges requirements.txt + pip magic + AST, deduped."""
    (code_dir / "requirements.txt").write_text("pandas>=2.0\nlightgbm\n")
    nb = {
        "cells": [
            {"cell_type": "code", "source": ["!pip install catboost\n"]},
            {"cell_type": "code", "source": ["import pandas\nimport lightgbm\nimport numpy\n"]},
        ],
        "metadata": {},
        "nbformat": 4,
    }
    (code_dir / "nb.ipynb").write_text(json.dumps(nb))
    ext = DependencyExtractor(code_dir)
    report = ext.extract()
    all_pkgs = report.all_packages()
    # pandas should appear once (from requirements.txt, not duplicated)
    pandas_count = sum(1 for p in all_pkgs if p.startswith("pandas"))
    assert pandas_count == 1
    assert any("lightgbm" in p for p in all_pkgs)
    assert "catboost" in all_pkgs
    assert "numpy" in all_pkgs


def test_handles_syntax_errors_gracefully(code_dir):
    """Files with syntax errors don't crash the extractor."""
    (code_dir / "good.py").write_text("import pandas\n")
    (code_dir / "broken.py").write_text("def foo(\n")  # syntax error
    ext = DependencyExtractor(code_dir)
    report = ext.extract()
    assert "pandas" in report.imports_found  # good file still extracted


def test_real_ear_model_notebooks():
    """Test on the actual EAR CL model notebooks if available."""
    code_dir = Path("validation_data/validations/d6ac58b2-44a/raw/model_code")
    if not code_dir.exists():
        pytest.skip("EAR CL bundle not ingested")
    ext = DependencyExtractor(code_dir)
    report = ext.extract()
    # These should definitely be found in the EAR CL notebooks
    assert "lightgbm" in report.imports_found or "lightgbm" in report.pip_magic
    assert "catboost" in report.imports_found
    assert len(report.all_packages()) >= 5
    print(f"EAR CL deps: {report.all_packages()}")
