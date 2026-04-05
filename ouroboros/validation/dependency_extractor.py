"""
Valoboros — deterministic dependency extraction.

AST-based scanner that extracts all third-party imports from .py and .ipynb files.
Runs before LLM comprehension — fast, reliable, no hallucination.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import name → pip package name mapping (common mismatches)
# ---------------------------------------------------------------------------

_IMPORT_TO_PIP: dict[str, str] = {
    # ML / Data Science
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "lightgbm": "lightgbm",
    "catboost": "catboost",
    "xgboost": "xgboost",
    "tf": "tensorflow",
    "tensorflow": "tensorflow",
    "torch": "torch",
    "torchvision": "torchvision",
    "transformers": "transformers",
    # Data
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "attr": "attrs",
    "dotenv": "python-dotenv",
    "sqlalchemy": "sqlalchemy",
    "psycopg2": "psycopg2-binary",
    "polars": "polars",
    "pyarrow": "pyarrow",
    "openpyxl": "openpyxl",
    # Viz
    "mpl_toolkits": "matplotlib",
    "matplotlib": "matplotlib",
    "plotly": "plotly",
    "seaborn": "seaborn",
    # Utils
    "tqdm": "tqdm",
    "joblib": "joblib",
    "requests": "requests",
    "scipy": "scipy",
    "statsmodels": "statsmodels",
    "shap": "shap",
    "optuna": "optuna",
    "hyperopt": "hyperopt",
    "imblearn": "imbalanced-learn",
    "numba": "numba",
}

# Stdlib module names (Python 3.10+)
try:
    _STDLIB = sys.stdlib_module_names
except AttributeError:
    # Fallback for Python < 3.10
    _STDLIB = frozenset({
        "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
        "asyncore", "atexit", "audioop", "base64", "bdb", "binascii",
        "binhex", "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb",
        "chunk", "cmath", "cmd", "code", "codecs", "codeop", "collections",
        "colorsys", "compileall", "concurrent", "configparser", "contextlib",
        "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
        "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal",
        "difflib", "dis", "distutils", "doctest", "email", "encodings",
        "enum", "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
        "fnmatch", "formatter", "fractions", "ftplib", "functools", "gc",
        "getopt", "getpass", "gettext", "glob", "grp", "gzip", "hashlib",
        "heapq", "hmac", "html", "http", "idlelib", "imaplib", "imghdr",
        "imp", "importlib", "inspect", "io", "ipaddress", "itertools",
        "json", "keyword", "lib2to3", "linecache", "locale", "logging",
        "lzma", "mailbox", "mailcap", "marshal", "math", "mimetypes",
        "mmap", "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
        "numbers", "operator", "optparse", "os", "ossaudiodev", "parser",
        "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
        "platform", "plistlib", "poplib", "posix", "posixpath", "pprint",
        "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr",
        "pydoc", "queue", "quopri", "random", "re", "readline", "reprlib",
        "resource", "rlcompleter", "runpy", "sched", "secrets", "select",
        "selectors", "shelve", "shlex", "shutil", "signal", "site",
        "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "spwd",
        "sqlite3", "sre_compile", "sre_constants", "sre_parse", "ssl",
        "stat", "statistics", "string", "stringprep", "struct", "subprocess",
        "sunau", "symtable", "sys", "sysconfig", "syslog", "tabnanny",
        "tarfile", "telnetlib", "tempfile", "termios", "test", "textwrap",
        "threading", "time", "timeit", "tkinter", "token", "tokenize",
        "tomllib", "trace", "traceback", "tracemalloc", "tty", "turtle",
        "turtledemo", "types", "typing", "unicodedata", "unittest", "urllib",
        "uu", "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
        "winreg", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc",
        "zipapp", "zipfile", "zipimport", "zlib", "_thread",
    })

# Regex for notebook pip magic commands
_PIP_MAGIC_RE = re.compile(r'(?:%pip|!pip)\s+install\s+(.+)', re.MULTILINE)


# ---------------------------------------------------------------------------
# DependencyReport
# ---------------------------------------------------------------------------

@dataclass
class DependencyReport:
    imports_found: list[str] = field(default_factory=list)     # raw import names
    pip_packages: list[str] = field(default_factory=list)      # mapped to pip names
    unmapped: list[str] = field(default_factory=list)          # couldn't map, may be local
    stdlib: list[str] = field(default_factory=list)            # filtered out
    pip_magic: list[str] = field(default_factory=list)         # from %pip / !pip lines
    requirements_txt: list[str] = field(default_factory=list)  # from requirements.txt
    source_files: dict[str, list[str]] = field(default_factory=dict)

    def all_packages(self) -> list[str]:
        """Merged, deduplicated list of all pip packages to install."""
        seen: set[str] = set()
        result: list[str] = []
        # Priority: requirements.txt > pip magic > AST-extracted
        for pkg in self.requirements_txt + self.pip_magic + self.pip_packages:
            name = pkg.strip().split("=")[0].split(">")[0].split("<")[0].split("[")[0].strip()
            if name.lower() not in seen and name:
                seen.add(name.lower())
                result.append(pkg.strip())
        return result


# ---------------------------------------------------------------------------
# DependencyExtractor
# ---------------------------------------------------------------------------

class DependencyExtractor:
    """Extract all third-party imports from .py and .ipynb files using AST parsing."""

    def __init__(self, code_dir: Path):
        self._code_dir = Path(code_dir)

    def extract(self) -> DependencyReport:
        report = DependencyReport()

        if not self._code_dir.exists():
            return report

        # Collect local module names (files in code_dir) to filter them out
        local_modules = self._get_local_module_names()

        all_imports: set[str] = set()
        all_stdlib: set[str] = set()

        # Scan .py files
        for f in sorted(self._code_dir.rglob("*.py")):
            imports = self._extract_imports_from_file(f)
            if imports:
                report.source_files[f.name] = sorted(imports)
            all_imports.update(imports)

        # Scan .ipynb files
        for f in sorted(self._code_dir.rglob("*.ipynb")):
            imports, pip_cmds = self._extract_from_notebook(f)
            if imports:
                report.source_files[f.name] = sorted(imports)
            all_imports.update(imports)
            report.pip_magic.extend(pip_cmds)

        # Check for requirements.txt
        req_file = self._code_dir / "requirements.txt"
        if req_file.exists():
            report.requirements_txt = self._parse_requirements(req_file)

        # Classify imports
        for imp in sorted(all_imports):
            if imp in _STDLIB:
                all_stdlib.add(imp)
            elif imp in local_modules:
                continue  # local module, skip
            else:
                report.imports_found.append(imp)
                pip_name = _IMPORT_TO_PIP.get(imp, imp)
                report.pip_packages.append(pip_name)
                if imp not in _IMPORT_TO_PIP and imp != pip_name:
                    report.unmapped.append(imp)

        report.stdlib = sorted(all_stdlib)
        return report

    def _get_local_module_names(self) -> set[str]:
        """Names of .py files in code_dir (these are local, not pip packages)."""
        names: set[str] = set()
        for f in self._code_dir.rglob("*.py"):
            names.add(f.stem)
        return names

    def _extract_imports_from_file(self, path: Path) -> set[str]:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            return self._extract_imports_from_source(source)
        except Exception:
            return set()

    @staticmethod
    def _extract_imports_from_source(source: str) -> set[str]:
        """Parse Python source and return top-level import names."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return set()

        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
        return imports

    def _extract_from_notebook(self, path: Path) -> tuple[set[str], list[str]]:
        """Extract imports and pip magic commands from a notebook."""
        imports: set[str] = set()
        pip_cmds: list[str] = []
        try:
            nb_data = json.loads(path.read_text(encoding="utf-8"))
            for cell in nb_data.get("cells", []):
                if cell.get("cell_type") != "code":
                    continue
                source = "".join(cell.get("source", []))
                # AST imports
                imports.update(self._extract_imports_from_source(source))
                # Pip magic
                for m in _PIP_MAGIC_RE.finditer(source):
                    pkgs = m.group(1).strip()
                    # Split "pkg1 pkg2 -q" → filter flags
                    for token in pkgs.split():
                        if not token.startswith("-"):
                            pip_cmds.append(token)
        except Exception as exc:
            log.debug("Failed to parse notebook %s: %s", path.name, exc)
        return imports, pip_cmds

    @staticmethod
    def _parse_requirements(path: Path) -> list[str]:
        """Parse requirements.txt, ignoring comments and blank lines."""
        lines: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                lines.append(line)
        return lines
