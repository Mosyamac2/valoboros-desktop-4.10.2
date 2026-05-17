"""
Ouroboros — Shared configuration (single source of truth).

Paths, settings defaults, load/save with file locking.
Only imports ouroboros.compat (platform abstraction, no circular deps).
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from typing import Optional

from ouroboros.compat import pid_lock_acquire as _compat_pid_lock_acquire
from ouroboros.compat import pid_lock_release as _compat_pid_lock_release


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HOME = pathlib.Path.home()
APP_ROOT = pathlib.Path(os.environ.get("OUROBOROS_APP_ROOT", HOME / "Ouroboros"))
REPO_DIR = pathlib.Path(os.environ.get("OUROBOROS_REPO_DIR", APP_ROOT / "repo"))
DATA_DIR = pathlib.Path(os.environ.get("OUROBOROS_DATA_DIR", APP_ROOT / "data"))
SETTINGS_PATH = pathlib.Path(os.environ.get("OUROBOROS_SETTINGS_PATH", DATA_DIR / "settings.json"))
PID_FILE = pathlib.Path(os.environ.get("OUROBOROS_PID_FILE", APP_ROOT / "ouroboros.pid"))
PORT_FILE = pathlib.Path(os.environ.get("OUROBOROS_PORT_FILE", DATA_DIR / "state" / "server_port"))

RESTART_EXIT_CODE = 42
PANIC_EXIT_CODE = 99
AGENT_SERVER_PORT = 8765


# ---------------------------------------------------------------------------
# Settings defaults
# ---------------------------------------------------------------------------
SETTINGS_DEFAULTS = {
    # --- Subscription auth (single cloud backend) ---
    "CLAUDE_CODE_OAUTH_TOKEN": "",
    "OUROBOROS_LLM_BACKEND": "claude_code_oauth",  # claude_code_oauth | local
    # Legacy Anthropic API key kept for backwards-compat only (per-token billing).
    # When CLAUDE_CODE_OAUTH_TOKEN is set, this is unset for child processes.
    "ANTHROPIC_API_KEY": "",

    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",

    "OUROBOROS_NETWORK_PASSWORD": "",
    "OUROBOROS_MODEL": "anthropic/claude-opus-4.7",
    "OUROBOROS_MODEL_CODE": "anthropic/claude-opus-4.7",
    "OUROBOROS_MODEL_LIGHT": "anthropic/claude-sonnet-4.6",
    "OUROBOROS_MODEL_FALLBACK": "anthropic/claude-sonnet-4.6",
    "CLAUDE_CODE_MODEL": "opus",
    "OUROBOROS_MAX_WORKERS": 5,
    # Budget knobs kept for compatibility — under OAuth, cost is always 0
    # and the effective gate is subscription rate limits, not dollars.
    "TOTAL_BUDGET": 10.0,
    "OUROBOROS_PER_TASK_COST_USD": 20.0,
    "OUROBOROS_SOFT_TIMEOUT_SEC": 600,
    "OUROBOROS_HARD_TIMEOUT_SEC": 1800,
    "OUROBOROS_TOOL_TIMEOUT_SEC": 600,
    "OUROBOROS_BG_MAX_ROUNDS": 5,
    "OUROBOROS_BG_WAKEUP_MIN": 30,
    "OUROBOROS_BG_WAKEUP_MAX": 7200,
    "OUROBOROS_EVO_COST_THRESHOLD": 0.10,
    "OUROBOROS_WEBSEARCH_MODEL": "anthropic/claude-sonnet-4.6",
    # Pre-commit review is now single-model (subscription-only — no diversity).
    "OUROBOROS_REVIEW_MODELS": "anthropic/claude-opus-4.7",
    # Pre-commit review enforcement: advisory | blocking
    "OUROBOROS_REVIEW_ENFORCEMENT": "advisory",
    # Scope review: single-model blocking reviewer (runs after triad review)
    "OUROBOROS_SCOPE_REVIEW_MODEL": "anthropic/claude-opus-4.7",
    # Reasoning effort per task type: none | low | medium | high
    # OUROBOROS_INITIAL_REASONING_EFFORT remains a legacy alias for task/chat.
    "OUROBOROS_EFFORT_TASK": "medium",
    "OUROBOROS_EFFORT_EVOLUTION": "high",
    "OUROBOROS_EFFORT_REVIEW": "medium",
    "OUROBOROS_EFFORT_SCOPE_REVIEW": "high",
    "OUROBOROS_EFFORT_CONSCIOUSNESS": "low",
    "GITHUB_TOKEN": "",
    "GITHUB_REPO": "",
    # Local model (llama-cpp-python server)
    "LOCAL_MODEL_SOURCE": "",
    "LOCAL_MODEL_FILENAME": "",
    "LOCAL_MODEL_PORT": 8766,
    "LOCAL_MODEL_N_GPU_LAYERS": 0,
    "LOCAL_MODEL_CONTEXT_LENGTH": 16384,
    "LOCAL_MODEL_CHAT_FORMAT": "",
    "USE_LOCAL_MAIN": False,
    "USE_LOCAL_CODE": False,
    "USE_LOCAL_LIGHT": False,
    "USE_LOCAL_FALLBACK": False,
    "OUROBOROS_FILE_BROWSER_DEFAULT": "",

    # --- Validation platform ---
    "OUROBOROS_VALIDATION_DIR": "validations",
    "OUROBOROS_VALIDATION_TIMEOUT_SEC": 3600,
    "OUROBOROS_VALIDATION_STAGE_TIMEOUT_SEC": 600,
    "OUROBOROS_VALIDATION_SANDBOX_MEM_MB": 4096,
    "OUROBOROS_VALIDATION_SANDBOX_CPU_SEC": 120,
    "OUROBOROS_VALIDATION_COMPREHENSION_MODEL": "anthropic/claude-opus-4.7",
    "OUROBOROS_VALIDATION_COMPREHENSION_EFFORT": "high",
    "OUROBOROS_VALIDATION_SYNTHESIS_MODEL": "anthropic/claude-opus-4.7",
    "OUROBOROS_VALIDATION_IMPROVEMENT_MODEL": "anthropic/claude-opus-4.7",
    "OUROBOROS_VALIDATION_MATURITY_THRESHOLD": 20,
    "OUROBOROS_VALIDATION_EVO_MIN_BUNDLES_EARLY": 1,
    "OUROBOROS_VALIDATION_EVO_MIN_BUNDLES_MATURE": 3,
    "OUROBOROS_VALIDATION_AUTO_EVOLVE": True,
    "OUROBOROS_VALIDATION_AUTO_IMPROVE": True,
    "OUROBOROS_VALIDATION_AUTO_SELF_ASSESS": True,
    "OUROBOROS_VALIDATION_REPORT_MODEL": "anthropic/claude-opus-4.7",
    "OUROBOROS_VALIDATION_METHODOLOGY_VERSION": "0.1.0",
    "OUROBOROS_VALIDATION_IMPROVEMENT_LIFT_THRESHOLD": 0.01,
    "OUROBOROS_VALIDATION_MAX_HARD_RECOMMENDATIONS": 10,
    "OUROBOROS_VALIDATION_MAX_SOFT_RECOMMENDATIONS": 10,
    "OUROBOROS_VALIDATION_INBOX_DIR": "ml-models-to-validate",
    "OUROBOROS_VALIDATION_AUTO_INGEST": True,
    "OUROBOROS_VALIDATION_PRE_RESEARCH": True,
    "OUROBOROS_VALIDATION_RESEARCH_MAX_QUERIES": 3,
    "OUROBOROS_VALIDATION_RESEARCH_MAX_PAPERS": 5,
}

_VALID_EFFORTS = ("none", "low", "medium", "high")


def _parse_model_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def have_cloud_auth() -> bool:
    """True when an Anthropic subscription token or legacy API key is set."""
    if str(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip():
        return True
    if str(os.environ.get("ANTHROPIC_API_KEY", "") or "").strip():
        return True
    return False


def resolve_effort(task_type: str) -> str:
    """Return the configured reasoning effort for the given task type."""
    t = (task_type or "").lower().strip()

    if t == "evolution":
        key = "OUROBOROS_EFFORT_EVOLUTION"
        default = "high"
    elif t == "review":
        key = "OUROBOROS_EFFORT_REVIEW"
        default = "medium"
    elif t in ("scope_review", "scope-review"):
        key = "OUROBOROS_EFFORT_SCOPE_REVIEW"
        default = "high"
    elif t == "consciousness":
        key = "OUROBOROS_EFFORT_CONSCIOUSNESS"
        default = "low"
    else:
        legacy = os.environ.get("OUROBOROS_INITIAL_REASONING_EFFORT", "")
        key = "OUROBOROS_EFFORT_TASK"
        default = legacy if legacy in _VALID_EFFORTS else "medium"

    raw = os.environ.get(key, default)
    return raw if raw in _VALID_EFFORTS else default


def get_review_models() -> list[str]:
    """Return the configured pre-commit review model list.

    Single-model review (subscription-only). Returns one Claude model id.
    Kept as a list for compatibility with `multi_model_review` callers that
    iterate over the result.
    """
    default_str = SETTINGS_DEFAULTS["OUROBOROS_REVIEW_MODELS"]
    models_str = os.environ.get("OUROBOROS_REVIEW_MODELS", default_str) or default_str
    models = _parse_model_list(models_str)
    if not models:
        return [str(SETTINGS_DEFAULTS["OUROBOROS_REVIEW_MODELS"])]
    return [models[0]]


def get_review_enforcement() -> str:
    """Return the configured pre-commit review enforcement mode."""
    default_val = str(SETTINGS_DEFAULTS["OUROBOROS_REVIEW_ENFORCEMENT"])
    raw = (os.environ.get("OUROBOROS_REVIEW_ENFORCEMENT", default_val) or default_val).strip().lower()
    return raw if raw in {"advisory", "blocking"} else default_val


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
def read_version() -> str:
    try:
        if getattr(sys, "frozen", False):
            vp = pathlib.Path(sys._MEIPASS) / "VERSION"
        else:
            vp = pathlib.Path(__file__).parent.parent / "VERSION"
        return vp.read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


# ---------------------------------------------------------------------------
# Settings file locking
# ---------------------------------------------------------------------------
_SETTINGS_LOCK = pathlib.Path(str(SETTINGS_PATH) + ".lock")


def _acquire_settings_lock(timeout: float = 2.0) -> Optional[int]:
    start = time.time()
    while time.time() - start < timeout:
        try:
            fd = os.open(str(_SETTINGS_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            return fd
        except FileExistsError:
            try:
                if time.time() - _SETTINGS_LOCK.stat().st_mtime > 10:
                    _SETTINGS_LOCK.unlink()
                    continue
            except Exception:
                pass
            time.sleep(0.01)
        except Exception:
            break
    return None


def _release_settings_lock(fd: Optional[int]) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except Exception:
            pass
    try:
        _SETTINGS_LOCK.unlink()
    except Exception:
        pass


def _coerce_setting_value(key: str, value):
    default = SETTINGS_DEFAULTS.get(key)
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return str(value or "")


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------
def load_settings() -> dict:
    fd = _acquire_settings_lock()
    try:
        loaded: dict = {}
        if SETTINGS_PATH.exists():
            try:
                raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    loaded = {
                        key: _coerce_setting_value(key, value) if key in SETTINGS_DEFAULTS else value
                        for key, value in raw.items()
                    }
            except Exception:
                pass
        settings = dict(SETTINGS_DEFAULTS)
        settings.update(loaded)
        for key in SETTINGS_DEFAULTS:
            raw_env = os.environ.get(key)
            if raw_env is None or raw_env == "":
                continue
            if key in loaded and settings.get(key) not in {None, ""}:
                continue
            settings[key] = _coerce_setting_value(key, raw_env)
        return settings
    finally:
        _release_settings_lock(fd)


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = _acquire_settings_lock()
    try:
        try:
            tmp = SETTINGS_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            os.replace(str(tmp), str(SETTINGS_PATH))
        except OSError:
            SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    finally:
        _release_settings_lock(fd)


def apply_settings_to_env(settings: dict) -> None:
    """Push settings into environment variables for supervisor modules."""
    env_keys = [
        "CLAUDE_CODE_OAUTH_TOKEN", "OUROBOROS_LLM_BACKEND",
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "OUROBOROS_NETWORK_PASSWORD",
        "OUROBOROS_MODEL", "OUROBOROS_MODEL_CODE", "OUROBOROS_MODEL_LIGHT",
        "OUROBOROS_MODEL_FALLBACK", "CLAUDE_CODE_MODEL",
        "TOTAL_BUDGET", "OUROBOROS_PER_TASK_COST_USD", "GITHUB_TOKEN", "GITHUB_REPO",
        "OUROBOROS_TOOL_TIMEOUT_SEC",
        "OUROBOROS_BG_MAX_ROUNDS", "OUROBOROS_BG_WAKEUP_MIN", "OUROBOROS_BG_WAKEUP_MAX",
        "OUROBOROS_EVO_COST_THRESHOLD", "OUROBOROS_WEBSEARCH_MODEL",
        "OUROBOROS_REVIEW_MODELS", "OUROBOROS_REVIEW_ENFORCEMENT",
        "OUROBOROS_SCOPE_REVIEW_MODEL",
        "OUROBOROS_EFFORT_TASK", "OUROBOROS_EFFORT_EVOLUTION",
        "OUROBOROS_EFFORT_REVIEW", "OUROBOROS_EFFORT_SCOPE_REVIEW",
        "OUROBOROS_EFFORT_CONSCIOUSNESS",
        "LOCAL_MODEL_SOURCE", "LOCAL_MODEL_FILENAME",
        "LOCAL_MODEL_PORT", "LOCAL_MODEL_N_GPU_LAYERS", "LOCAL_MODEL_CONTEXT_LENGTH",
        "LOCAL_MODEL_CHAT_FORMAT",
        "USE_LOCAL_MAIN", "USE_LOCAL_CODE", "USE_LOCAL_LIGHT", "USE_LOCAL_FALLBACK",
        "OUROBOROS_FILE_BROWSER_DEFAULT",
    ]
    for k in env_keys:
        val = settings.get(k)
        if val is None or val == "":
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(val)
    if not os.environ.get("OUROBOROS_REVIEW_MODELS"):
        os.environ["OUROBOROS_REVIEW_MODELS"] = str(SETTINGS_DEFAULTS["OUROBOROS_REVIEW_MODELS"])
    if not os.environ.get("OUROBOROS_REVIEW_ENFORCEMENT"):
        os.environ["OUROBOROS_REVIEW_ENFORCEMENT"] = str(SETTINGS_DEFAULTS["OUROBOROS_REVIEW_ENFORCEMENT"])
    # When OAuth subscription is the auth source, clear ANTHROPIC_API_KEY so
    # the Claude CLI/SDK uses subscription billing instead of API-key billing.
    if (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip():
        os.environ.pop("ANTHROPIC_API_KEY", None)


# ---------------------------------------------------------------------------
# PID lock (single instance) — crash-proof locking via ouroboros.compat.
# On Unix the OS releases flock automatically when the process dies
# (even SIGKILL), so stale lock files can never block future launches.
# On Windows msvcrt.locking provides equivalent semantics.
# ---------------------------------------------------------------------------

def acquire_pid_lock() -> bool:
    APP_ROOT.mkdir(parents=True, exist_ok=True)
    return _compat_pid_lock_acquire(str(PID_FILE))


def release_pid_lock() -> None:
    _compat_pid_lock_release(str(PID_FILE))
