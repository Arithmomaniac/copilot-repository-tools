"""Check PyPI for newer versions of copilot-session-tools.

Results are cached for 24 hours in ~/.copilot-session-tools/.version-cache.
"""

import json
import time
from pathlib import Path

from copilot_session_tools import __version__

_CACHE_DIR = Path.home() / ".copilot-session-tools"
_CACHE_FILE = _CACHE_DIR / ".version-cache"
_CACHE_TTL = 86400  # 24 hours
_PYPI_URL = "https://pypi.org/pypi/copilot-session-tools/json"


def _read_cache() -> dict | None:
    """Read cached version check result if still valid."""
    try:
        if not _CACHE_FILE.exists():
            return None
        data = json.loads(_CACHE_FILE.read_text())
        if time.time() - data.get("checked_at", 0) < _CACHE_TTL:
            return data
    except Exception:  # noqa: S110
        pass
    return None


def _write_cache(latest_version: str) -> None:
    """Write version check result to cache."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"latest_version": latest_version, "checked_at": time.time()}))
    except Exception:  # noqa: S110
        pass


def get_latest_version() -> str | None:
    """Check PyPI for the latest version, using 24-hour cache.

    Returns the latest version string, or None if the check fails.
    """
    cached = _read_cache()
    if cached:
        return cached.get("latest_version")

    try:
        import urllib.request

        with urllib.request.urlopen(_PYPI_URL, timeout=3) as resp:  # noqa: S310
            data = json.loads(resp.read())
            latest = data["info"]["version"]
            _write_cache(latest)
            return latest
    except Exception:
        return None


def check_for_upgrade() -> str | None:
    """Return the latest version if it's newer than current, else None."""
    latest = get_latest_version()
    if latest is None:
        return None
    try:
        from packaging.version import Version

        if Version(latest) > Version(__version__):
            return latest
    except Exception:  # noqa: S110
        pass
    return None
