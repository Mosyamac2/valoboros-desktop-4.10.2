"""
Ouroboros validation platform — secure model execution sandbox.

Runs untrusted model code in isolated subprocesses with:
- Resource limits (memory via RLIMIT_AS, CPU via RLIMIT_CPU)
- Network isolation (unshare --net on Linux)
- Stdout/stderr capture with truncation (max 1 MB)
- Hard timeout with process kill
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path
from typing import Optional

from ouroboros.validation.types import SandboxResult, ValidationConfig

log = logging.getLogger(__name__)

_MAX_OUTPUT_BYTES = 1_048_576  # 1 MB


class ModelSandbox:
    """Execute untrusted model code in an isolated subprocess with resource limits."""

    def __init__(self, bundle_dir: Path, config: ValidationConfig) -> None:
        self._bundle_dir = Path(bundle_dir).resolve()
        self._mem_limit = config.sandbox_mem_mb * 1024 * 1024
        self._cpu_limit = config.sandbox_cpu_sec
        self._venv_dir = self._bundle_dir / ".sandbox_venv"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def install_dependencies(self, packages: list[str]) -> str:
        """Create a venv and pip-install *packages* into it.  Max 5 min."""
        if not packages:
            return "No packages to install."

        # Create venv if it doesn't exist
        if not self._venv_dir.exists():
            log.info("Creating sandbox venv at %s", self._venv_dir)
            venv.create(str(self._venv_dir), with_pip=True, clear=False)

        pip = self._pip_path()
        if pip is None:
            return "ERROR: pip not found in sandbox venv."

        cmd = [str(pip), "install", "--no-cache-dir"] + packages
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self._bundle_dir),
            )
            if result.returncode == 0:
                return f"Installed {len(packages)} package(s): {', '.join(packages)}"
            return f"pip install failed (rc={result.returncode}):\n{result.stderr[:2000]}"
        except subprocess.TimeoutExpired:
            return "ERROR: pip install timed out after 300 s."
        except Exception as exc:
            return f"ERROR: {exc}"

    def run(self, script: str, timeout: int = 120) -> SandboxResult:
        """Execute a Python script string inside the sandbox."""
        script_path: Optional[Path] = None
        try:
            # Write script to a temp file *inside* bundle_dir
            fd, tmp_name = tempfile.mkstemp(
                suffix=".py", prefix="_sandbox_", dir=str(self._bundle_dir),
            )
            script_path = Path(tmp_name)
            with os.fdopen(fd, "w") as f:
                f.write(script)

            python = self._python_path()
            cmd = self._build_cmd(python, script_path)

            start = time.monotonic()
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self._bundle_dir),
                preexec_fn=self._make_preexec_fn(),
            )

            timeout_killed = False
            oom_killed = False
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_bytes, stderr_bytes = proc.communicate(timeout=5)
                timeout_killed = True

            duration = time.monotonic() - start

            stdout = self._truncate(stdout_bytes)
            stderr = self._truncate(stderr_bytes)

            # Heuristic OOM detection: killed by signal 9 without our timeout
            if not timeout_killed and proc.returncode == -9:
                oom_killed = True

            return SandboxResult(
                returncode=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_sec=round(duration, 3),
                oom_killed=oom_killed,
                timeout_killed=timeout_killed,
            )
        except Exception as exc:
            return SandboxResult(
                returncode=-1,
                stdout="",
                stderr=f"Sandbox internal error: {exc}",
                duration_sec=0.0,
                oom_killed=False,
                timeout_killed=False,
            )
        finally:
            if script_path is not None:
                try:
                    script_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def run_notebook(self, notebook_path: str, timeout: int = 300) -> SandboxResult:
        """Execute a Jupyter notebook by converting to .py first, then running."""
        nb_path = (self._bundle_dir / notebook_path).resolve()
        if not str(nb_path).startswith(str(self._bundle_dir)):
            return SandboxResult(
                returncode=-1, stdout="", stderr="Notebook path escapes bundle_dir.",
                duration_sec=0.0, oom_killed=False, timeout_killed=False,
            )
        if not nb_path.exists():
            return SandboxResult(
                returncode=-1, stdout="", stderr=f"Notebook not found: {notebook_path}",
                duration_sec=0.0, oom_killed=False, timeout_killed=False,
            )

        try:
            import nbformat
            from nbconvert import PythonExporter
        except ImportError:
            return SandboxResult(
                returncode=-1, stdout="",
                stderr="nbformat/nbconvert not installed — cannot execute notebooks.",
                duration_sec=0.0, oom_killed=False, timeout_killed=False,
            )

        try:
            nb = nbformat.read(str(nb_path), as_version=4)
            exporter = PythonExporter()
            script, _ = exporter.from_notebook_node(nb)
        except Exception as exc:
            return SandboxResult(
                returncode=-1, stdout="",
                stderr=f"Failed to convert notebook to script: {exc}",
                duration_sec=0.0, oom_killed=False, timeout_killed=False,
            )

        return self.run(script, timeout=timeout)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _python_path(self) -> str:
        """Return the Python interpreter to use: venv if available, else system."""
        venv_python = self._venv_dir / "bin" / "python"
        if venv_python.exists():
            return str(venv_python)
        return sys.executable

    def _pip_path(self) -> Optional[str]:
        pip = self._venv_dir / "bin" / "pip"
        if pip.exists():
            return str(pip)
        return None

    def _build_cmd(self, python: str, script_path: Path) -> list[str]:
        """Build the command list, prepending unshare --net on Linux if available."""
        base = [python, str(script_path)]
        if platform.system() != "Linux" or not shutil.which("unshare"):
            if platform.system() == "Linux":
                log.warning("unshare not found — sandbox has no network isolation")
            return base

        if self._unshare_net_works is None:
            # Probe once whether unshare --net works on this system.
            ModelSandbox._unshare_net_works = self._probe_unshare()

        if self._unshare_net_works:
            return ["unshare", "--net"] + base
        log.warning("unshare --net not permitted — sandbox has no network isolation")
        return base

    # Class-level cache: None = not tested yet, True/False = result.
    _unshare_net_works: Optional[bool] = None

    @staticmethod
    def _probe_unshare() -> bool:
        """Test whether unshare --net works on this system."""
        try:
            result = subprocess.run(
                ["unshare", "--net", "true"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _make_preexec_fn(self):
        """Return a preexec_fn that sets resource limits (Linux/macOS only)."""
        mem_limit = self._mem_limit
        cpu_limit = self._cpu_limit

        def _set_limits():
            try:
                import resource
                # Memory limit (RLIMIT_AS = virtual address space)
                if mem_limit > 0:
                    resource.setrlimit(resource.RLIMIT_AS, (mem_limit, mem_limit))
                # CPU time limit
                if cpu_limit > 0:
                    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
            except Exception:
                pass  # resource module may not be available on all platforms

        return _set_limits

    @staticmethod
    def _truncate(data: bytes) -> str:
        """Decode bytes to str, truncating to _MAX_OUTPUT_BYTES."""
        if len(data) > _MAX_OUTPUT_BYTES:
            data = data[:_MAX_OUTPUT_BYTES]
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return data.decode("latin-1", errors="replace")
