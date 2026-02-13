"""Session file discovery, scanning, and parsing dispatch."""

import os
import platform
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

import orjson

from .cli import _parse_cli_jsonl_file
from .models import ChatSession, SessionFileInfo
from .vscode import _parse_chat_session_file, _parse_vscdb_file, _parse_vscode_jsonl_file


def get_vscode_storage_paths() -> list[tuple[str, str]]:
    """Get the paths to VS Code workspace storage directories.

    Returns a list of tuples: (path, edition) where edition is 'stable' or 'insider'.
    """
    system = platform.system()

    paths = []

    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            # VS Code Stable
            stable_path = Path(appdata) / "Code" / "User" / "workspaceStorage"
            paths.append((str(stable_path), "stable"))
            # VS Code Insiders
            insider_path = Path(appdata) / "Code - Insiders" / "User" / "workspaceStorage"
            paths.append((str(insider_path), "insider"))
    elif system == "Darwin":  # macOS
        home = Path.home()
        # VS Code Stable
        stable_path = home / "Library" / "Application Support" / "Code" / "User" / "workspaceStorage"
        paths.append((str(stable_path), "stable"))
        # VS Code Insiders
        insider_path = home / "Library" / "Application Support" / "Code - Insiders" / "User" / "workspaceStorage"
        paths.append((str(insider_path), "insider"))
    else:  # Linux and others
        home = Path.home()
        # VS Code Stable
        stable_path = home / ".config" / "Code" / "User" / "workspaceStorage"
        paths.append((str(stable_path), "stable"))
        # VS Code Insiders
        insider_path = home / ".config" / "Code - Insiders" / "User" / "workspaceStorage"
        paths.append((str(insider_path), "insider"))

    return paths


def find_copilot_chat_dirs(
    storage_paths: list[tuple[str, str]] | None = None,
) -> Iterator[tuple[Path, str, str]]:
    """Find directories containing Copilot chat sessions.

    Args:
        storage_paths: Optional list of (path, edition) tuples to search.
                       If None, uses default VS Code storage paths.

    Yields:
        Tuples of (chat_dir_path, workspace_id, edition)
    """
    if storage_paths is None:
        storage_paths = get_vscode_storage_paths()

    for storage_path, edition in storage_paths:
        storage_dir = Path(storage_path)
        if not storage_dir.exists():
            continue

        # Each subdirectory is a workspace
        for workspace_dir in storage_dir.iterdir():
            if not workspace_dir.is_dir():
                continue

            workspace_id = workspace_dir.name

            # Look for Copilot chat sessions - they may be in different locations
            # depending on the VS Code and Copilot extension versions

            # Check for github.copilot-chat extension storage
            copilot_chat_dir = workspace_dir / "state.vscdb.backup"  # Some versions use this
            if copilot_chat_dir.exists():
                yield copilot_chat_dir.parent, workspace_id, edition

            # Check for chatSessions directory (newer format)
            chat_sessions_dir = workspace_dir / "chatSessions"
            if chat_sessions_dir.exists() and chat_sessions_dir.is_dir():
                yield chat_sessions_dir, workspace_id, edition

            # Check for workspaceState file
            workspace_state = workspace_dir / "workspace.json"
            if workspace_state.exists():
                yield workspace_dir, workspace_id, edition


def _parse_workspace_json(workspace_dir: Path) -> tuple[str | None, str | None]:
    """Parse workspace.json to get workspace name and path."""
    workspace_json = workspace_dir / "workspace.json"
    if workspace_json.exists():
        try:
            with workspace_json.open("rb") as f:
                data = orjson.loads(f.read())
                folder = data.get("folder", "")
                # folder is often a URI like file:///path/to/workspace
                if folder.startswith("file://"):
                    folder = folder[7:]
                    if platform.system() == "Windows" and folder.startswith("/"):
                        # Windows paths like /C:/path
                        folder = folder[1:]
                # URL decode the path (e.g., %3A -> :, %20 -> space)
                folder = unquote(folder) if folder else ""
                workspace_name = Path(folder).name if folder else None
                return workspace_name, folder if folder else None
        except (orjson.JSONDecodeError, OSError):
            pass
    return None, None


def get_cli_storage_paths() -> list[Path]:
    """Get the paths to GitHub Copilot CLI session storage directories.

    Returns a list of Path objects for CLI session directories.
    """
    home = Path.home()
    copilot_dir = home / ".copilot"

    paths = []

    # Current format (v0.0.342+)
    session_state_dir = copilot_dir / "session-state"
    if session_state_dir.exists():
        paths.append(session_state_dir)

    # Legacy format (pre-v0.0.342)
    history_state_dir = copilot_dir / "history-session-state"
    if history_state_dir.exists():
        paths.append(history_state_dir)

    return paths


def scan_chat_sessions(
    storage_paths: list[tuple[str, str]] | None = None,
    include_cli: bool = True,
) -> Iterator[ChatSession]:
    """Scan for and parse all Copilot chat sessions.

    Args:
        storage_paths: Optional list of (path, edition) tuples to search for VS Code sessions.
        include_cli: Whether to also scan for CLI sessions (default: True).

    Yields:
        ChatSession objects for each found session.
    """
    # Scan VS Code chat sessions
    for chat_dir, _workspace_id, edition in find_copilot_chat_dirs(storage_paths):
        # Get workspace info
        workspace_name, workspace_path = _parse_workspace_json(chat_dir.parent)

        # Process JSON files in the directory
        for item in chat_dir.iterdir():
            if item.is_file():
                if item.suffix == ".json":
                    session = _parse_chat_session_file(item, workspace_name, workspace_path, edition)
                    if session:
                        yield session
                elif item.suffix == ".jsonl":
                    session = _parse_vscode_jsonl_file(item, workspace_name, workspace_path, edition)
                    if session:
                        yield session
                elif item.suffix == ".vscdb":
                    # Parse SQLite database files
                    sessions = _parse_vscdb_file(item, workspace_name, workspace_path, edition)
                    for session in sessions:
                        yield session

        # Also check for state.vscdb in parent directory
        state_db = chat_dir.parent / "state.vscdb"
        if state_db.exists():
            sessions = _parse_vscdb_file(state_db, workspace_name, workspace_path, edition)
            for session in sessions:
                yield session

    # Scan CLI chat sessions
    if include_cli:
        for cli_dir in get_cli_storage_paths():
            if not cli_dir.exists() or not cli_dir.is_dir():
                continue

            # Process CLI storage directory - supports two formats:
            # 1. Old format: {session-id}.jsonl files directly in the directory
            # 2. New format: {session-id}/events.jsonl subdirectories
            for item in cli_dir.iterdir():
                if item.is_file() and item.suffix == ".jsonl":
                    # Old format: flat JSONL files
                    session = _parse_cli_jsonl_file(item)
                    if session:
                        yield session
                elif item.is_dir():
                    # New format: subdirectory with events.jsonl
                    events_file = item / "events.jsonl"
                    if events_file.exists():
                        session = _parse_cli_jsonl_file(events_file)
                        if session:
                            yield session


def scan_session_files(
    storage_paths: list[tuple[str, str]] | None = None,
    include_cli: bool = True,
) -> Iterator[SessionFileInfo]:
    """Scan for session files and yield metadata without parsing content.

    This allows callers to check mtime/size before expensive parsing.
    Use parse_session_file() to parse a specific file.

    Args:
        storage_paths: Optional list of (path, edition) tuples to search for VS Code sessions.
        include_cli: Whether to also scan for CLI sessions (default: True).

    Yields:
        SessionFileInfo objects with file metadata.
    """
    # Scan VS Code chat session files
    for chat_dir, _workspace_id, edition in find_copilot_chat_dirs(storage_paths):
        workspace_name, workspace_path = _parse_workspace_json(chat_dir.parent)

        # Process files in the chat directory
        for item in chat_dir.iterdir():
            if item.is_file():
                try:
                    stat = item.stat()
                    if item.suffix == ".json":
                        yield SessionFileInfo(
                            file_path=item,
                            file_type="json",
                            session_type="vscode",
                            vscode_edition=edition,
                            mtime=stat.st_mtime,
                            size=stat.st_size,
                            workspace_name=workspace_name,
                            workspace_path=workspace_path,
                        )
                    elif item.suffix == ".jsonl":
                        yield SessionFileInfo(
                            file_path=item,
                            file_type="jsonl",
                            session_type="vscode",
                            vscode_edition=edition,
                            mtime=stat.st_mtime,
                            size=stat.st_size,
                            workspace_name=workspace_name,
                            workspace_path=workspace_path,
                        )
                    elif item.suffix == ".vscdb":
                        yield SessionFileInfo(
                            file_path=item,
                            file_type="vscdb",
                            session_type="vscode",
                            vscode_edition=edition,
                            mtime=stat.st_mtime,
                            size=stat.st_size,
                            workspace_name=workspace_name,
                            workspace_path=workspace_path,
                        )
                except OSError:
                    continue

        # Check for state.vscdb in parent directory
        state_db = chat_dir.parent / "state.vscdb"
        if state_db.exists():
            try:
                stat = state_db.stat()
                yield SessionFileInfo(
                    file_path=state_db,
                    file_type="vscdb",
                    session_type="vscode",
                    vscode_edition=edition,
                    mtime=stat.st_mtime,
                    size=stat.st_size,
                    workspace_name=workspace_name,
                    workspace_path=workspace_path,
                )
            except OSError:
                pass

    # Scan CLI session files
    if include_cli:
        for cli_dir in get_cli_storage_paths():
            if not cli_dir.exists() or not cli_dir.is_dir():
                continue

            for item in cli_dir.iterdir():
                try:
                    if item.is_file() and item.suffix == ".jsonl":
                        stat = item.stat()
                        yield SessionFileInfo(
                            file_path=item,
                            file_type="jsonl",
                            session_type="cli",
                            vscode_edition="cli",
                            mtime=stat.st_mtime,
                            size=stat.st_size,
                        )
                    elif item.is_dir():
                        events_file = item / "events.jsonl"
                        if events_file.exists():
                            stat = events_file.stat()
                            yield SessionFileInfo(
                                file_path=events_file,
                                file_type="jsonl",
                                session_type="cli",
                                vscode_edition="cli",
                                mtime=stat.st_mtime,
                                size=stat.st_size,
                            )
                except OSError:
                    continue


def parse_session_file(file_info: SessionFileInfo) -> list[ChatSession]:
    """Parse a session file and return ChatSession objects.

    Args:
        file_info: SessionFileInfo from scan_session_files().

    Returns:
        List of ChatSession objects (may be multiple for vscdb files).
    """
    if file_info.file_type == "json":
        session = _parse_chat_session_file(
            file_info.file_path,
            file_info.workspace_name,
            file_info.workspace_path,
            file_info.vscode_edition,
        )
        return [session] if session else []

    elif file_info.file_type == "vscdb":
        return list(
            _parse_vscdb_file(
                file_info.file_path,
                file_info.workspace_name,
                file_info.workspace_path,
                file_info.vscode_edition,
            )
        )

    elif file_info.file_type == "jsonl":
        if file_info.session_type == "vscode":
            session = _parse_vscode_jsonl_file(
                file_info.file_path,
                file_info.workspace_name,
                file_info.workspace_path,
                file_info.vscode_edition,
            )
        else:
            session = _parse_cli_jsonl_file(file_info.file_path)
        return [session] if session else []

    return []
