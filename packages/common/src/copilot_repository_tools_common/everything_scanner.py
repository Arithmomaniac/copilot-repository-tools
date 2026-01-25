"""Everything scanner module for optimized file discovery on Windows.

This module provides an interface to the Everything search utility
(https://www.voidtools.com/) for instant file/folder lookups on NTFS volumes.

Everything must be installed and running for this to work. When not available,
callers should fall back to standard directory traversal.

Requires the optional 'everytools' package:
    pip install copilot-repository-tools[everything]
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class _EverytoolsLoader:
    """Lazy loader for everytools module to avoid import errors when not installed."""

    _checked: bool = False
    _search_class: Any = None
    _service_available: bool | None = None  # Cache service availability

    @classmethod
    def get(cls):
        """Get everytools Search class, or None if not available."""
        if not cls._checked:
            cls._checked = True
            try:
                from everytools import Search

                cls._search_class = Search
            except ImportError:
                cls._search_class = None
        return cls._search_class

    @classmethod
    def is_service_running(cls) -> bool:
        """Check if Everything service is running. Result is cached."""
        if cls._service_available is not None:
            return cls._service_available

        Search = cls.get()
        if Search is None:
            cls._service_available = False
            return False

        try:
            # This is slow (~5s) on first call due to DLL loading
            search = Search("*")
            search.execute()
            cls._service_available = True
        except (OSError, RuntimeError, Exception):
            cls._service_available = False

        return cls._service_available

    @classmethod
    def reset(cls):
        """Reset cached state (for testing)."""
        cls._checked = False
        cls._search_class = None
        cls._service_available = None


def is_everything_available() -> bool:
    """Check if Everything search is available and running.

    Returns True only if:
    1. Running on Windows
    2. everytools package is installed
    3. Everything service is currently running
    4. COPILOT_USE_EVERYTHING env var is set to '1'

    Note: Everything is disabled by default because standard directory traversal
    is faster for typical workspaceStorage sizes. Set COPILOT_USE_EVERYTHING=1
    to enable it for very large directory structures.

    Returns:
        True if Everything can be used for searching, False otherwise.
    """
    # Must explicitly opt-in (Everything is slower for typical use cases)
    if os.environ.get("COPILOT_USE_EVERYTHING") != "1":
        return False

    # Only works on Windows
    if os.name != "nt":
        return False

    return _EverytoolsLoader.is_service_running()


def search_files(query: str, path: str | None = None) -> list[Path]:
    """Search for files using Everything.

    Args:
        query: Everything search query (uses Everything's query syntax).
               Examples: "*.json", "state.vscdb", "chatSessions folder:"
        path: Optional path constraint to limit search scope.

    Returns:
        List of Path objects for matching files/folders.

    Raises:
        RuntimeError: If Everything is not available.
    """
    Search = _EverytoolsLoader.get()
    if Search is None:
        raise RuntimeError("everytools package not installed")

    # Build query with path constraint if provided
    full_query = query
    if path:
        # Everything syntax: path:"C:\path\to\search"
        full_query = f'{query} path:"{path}"'

    # Execute search
    try:
        search = Search(full_query)
        search.execute()
        results = search.get_results()
        return [Path(r.full_path) for r in results]
    except (OSError, RuntimeError) as e:
        raise RuntimeError(f"Everything search failed: {e}") from e


def find_chat_session_files(storage_path: str) -> dict[str, list[Path]]:
    """Find Copilot chat session files in a VS Code storage path using Everything.

    Searches for:
    - chatSessions directories
    - state.vscdb files
    - workspace.json files

    Args:
        storage_path: Path to VS Code workspaceStorage directory.

    Returns:
        Dictionary with keys 'chat_sessions_dirs', 'state_vscdb', 'workspace_json'
        each containing list of matching Paths.

    Raises:
        RuntimeError: If Everything is not available.
    """
    if not is_everything_available():
        raise RuntimeError("Everything is not available")

    results: dict[str, list[Path]] = {
        "chat_sessions_dirs": [],
        "state_vscdb": [],
        "workspace_json": [],
    }

    # Find chatSessions directories
    chat_dirs = search_files("chatSessions folder:", storage_path)
    results["chat_sessions_dirs"] = [p for p in chat_dirs if p.is_dir()]

    # Find state.vscdb files (including .backup)
    vscdb_files = search_files("state.vscdb", storage_path)
    results["state_vscdb"] = [p for p in vscdb_files if p.is_file()]

    # Find workspace.json files
    workspace_files = search_files("workspace.json", storage_path)
    results["workspace_json"] = [p for p in workspace_files if p.is_file()]

    return results


def find_workspaces_with_chat_data(storage_path: str) -> set[Path]:
    """Find workspace directories that contain Copilot chat data.

    Uses Everything to quickly find all workspaces that have either:
    - chatSessions/ directory
    - state.vscdb file

    Args:
        storage_path: Path to VS Code workspaceStorage directory.

    Returns:
        Set of workspace directory Paths that contain chat data.

    Raises:
        RuntimeError: If Everything is not available.
    """
    if not is_everything_available():
        raise RuntimeError("Everything is not available")

    workspace_dirs: set[Path] = set()

    # Find chatSessions directories and get their parent workspace dirs
    chat_dirs = search_files("chatSessions folder:", storage_path)
    for chat_dir in chat_dirs:
        if chat_dir.is_dir():
            workspace_dirs.add(chat_dir.parent)

    # Find state.vscdb files and get their parent workspace dirs
    vscdb_files = search_files("state.vscdb", storage_path)
    for vscdb_file in vscdb_files:
        if vscdb_file.is_file():
            workspace_dirs.add(vscdb_file.parent)

    return workspace_dirs
