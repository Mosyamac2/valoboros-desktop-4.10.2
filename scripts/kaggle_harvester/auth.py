"""Kaggle credential intake (new-format Bearer token).

Kaggle's modern API tokens look like ``KGAT_<32-hex>`` and are used as
HTTP Bearer tokens (``Authorization: Bearer KGAT_...``) — not as
username/password pairs with HTTP Basic Auth. The legacy ``kaggle`` Python
SDK still uses the old Basic-Auth ``kaggle.json`` flow, which is why it
returns 401 against modern tokens. We bypass the SDK and call Kaggle's
REST API directly.

Intake paths (first match wins):
- ``KAGGLE_TOKEN`` env var (preferred for CI / containers).
- ``~/.kaggle/kaggle.json`` with ``{"token": "KGAT_..."}`` (mode 0600).
- Legacy ``~/.kaggle/kaggle.json`` with ``{"username": "...", "key": "..."}``
  — accepted for backwards compatibility; reformatted into a Bearer
  payload at call time (Kaggle accepts hex-only keys via Basic Auth on
  some endpoints, so we keep that route working when it works).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

_KAGGLE_DIR = pathlib.Path.home() / ".kaggle"
_KAGGLE_JSON = _KAGGLE_DIR / "kaggle.json"

_BEARER_RE = re.compile(r"^KGAT_[0-9a-fA-F]{32}$")
_LEGACY_HEX_RE = re.compile(r"^[0-9a-fA-F]{32}$")


class KaggleAuthError(RuntimeError):
    """Raised when credentials are missing, malformed, or rejected."""


@dataclass
class KaggleAuth:
    """Either a modern Bearer token or a legacy (username, hex-key) pair."""

    bearer_token: Optional[str] = None
    legacy_username: Optional[str] = None
    legacy_key: Optional[str] = None

    @property
    def is_bearer(self) -> bool:
        return bool(self.bearer_token)

    @property
    def is_legacy_basic(self) -> bool:
        return bool(self.legacy_username and self.legacy_key)

    def is_valid_shape(self) -> bool:
        if self.is_bearer:
            return bool(_BEARER_RE.match(self.bearer_token or ""))
        if self.is_legacy_basic:
            return bool(self.legacy_username) and bool(_LEGACY_HEX_RE.match(self.legacy_key or ""))
        return False

    def auth_header(self) -> dict[str, str]:
        """Return the HTTP Authorization header for the chosen auth mode."""
        if self.is_bearer:
            return {"Authorization": f"Bearer {self.bearer_token}"}
        if self.is_legacy_basic:
            import base64
            raw = f"{self.legacy_username}:{self.legacy_key}".encode("utf-8")
            return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
        raise KaggleAuthError("No usable credentials in this KaggleAuth.")

    def redacted(self) -> str:
        if self.is_bearer:
            return f"Bearer KGAT_{'*' * 32}"
        if self.is_legacy_basic:
            return f"Basic {self.legacy_username}:{'*' * 32}"
        return "(none)"


def _read_env_creds() -> Optional[KaggleAuth]:
    bearer = (os.environ.get("KAGGLE_TOKEN") or "").strip()
    if bearer:
        return KaggleAuth(bearer_token=bearer)
    user = (os.environ.get("KAGGLE_USERNAME") or "").strip()
    key = (os.environ.get("KAGGLE_KEY") or "").strip()
    if user and key:
        if _BEARER_RE.match(key):
            return KaggleAuth(bearer_token=key)
        return KaggleAuth(legacy_username=user, legacy_key=key)
    return None


def _read_json_creds() -> Optional[KaggleAuth]:
    if not _KAGGLE_JSON.exists():
        return None
    try:
        data = json.loads(_KAGGLE_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not parse %s: %s", _KAGGLE_JSON, e)
        return None
    token = str(data.get("token") or "").strip()
    if token:
        return KaggleAuth(bearer_token=token)
    user = str(data.get("username") or "").strip()
    key = str(data.get("key") or "").strip()
    if user and key:
        if _BEARER_RE.match(key):
            return KaggleAuth(bearer_token=key)
        return KaggleAuth(legacy_username=user, legacy_key=key)
    return None


def load_credentials() -> KaggleAuth:
    """Return Kaggle credentials. Raises ``KaggleAuthError`` if absent or malformed."""
    auth = _read_env_creds() or _read_json_creds()
    if auth is None:
        raise KaggleAuthError(
            "No Kaggle credentials found. Either:\n"
            "  - export KAGGLE_TOKEN=KGAT_<32-hex>, or\n"
            "  - write ~/.kaggle/kaggle.json with {\"token\": \"KGAT_...\"} (mode 0600)."
        )
    if not auth.is_valid_shape():
        if auth.is_bearer:
            raise KaggleAuthError(
                "Kaggle bearer token has wrong shape. Expected KGAT_<32 hex chars>."
            )
        raise KaggleAuthError(
            "Kaggle legacy key has wrong shape. Expected 32-char hex paired with a username."
        )
    return auth


def smoke_probe(auth: Optional[KaggleAuth] = None, *, timeout: float = 15.0) -> tuple[bool, str]:
    """Issue one cheap API call. Returns ``(ok, message)``.

    Uses the HTTP client directly so the result is independent of the legacy
    kaggle SDK. A 401 means the token is rejected; the harvester should not
    proceed.
    """
    import requests

    if auth is None:
        auth = load_credentials()
    try:
        resp = requests.get(
            "https://www.kaggle.com/api/v1/competitions/list",
            params={"search": "titanic", "page": 1},
            headers=auth.auth_header(),
            timeout=timeout,
        )
    except requests.RequestException as e:
        return False, f"Network error contacting Kaggle: {type(e).__name__}: {e}"
    if resp.status_code == 200:
        return True, f"OK: authenticated ({auth.redacted()})"
    if resp.status_code == 401:
        return False, (
            "Credentials rejected by Kaggle (401). The token may be invalid, "
            "rotated, or used with the wrong auth mode. Generate a fresh one "
            "at https://www.kaggle.com/settings/account → 'Create New Token'."
        )
    return False, f"Smoke probe failed: HTTP {resp.status_code} {resp.text[:200]}"
