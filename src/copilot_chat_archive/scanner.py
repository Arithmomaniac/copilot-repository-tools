"""Scanner module to find and parse VS Code Copilot chat history files."""

import json
import os
import platform
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator


@dataclass
class ChatMessage:
    """Represents a single message in a chat session."""

    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str | None = None


@dataclass
class ChatSession:
    """Represents a Copilot chat session."""

    session_id: str
    workspace_name: str | None
    workspace_path: str | None
    messages: list[ChatMessage]
    created_at: str | None = None
    updated_at: str | None = None
    source_file: str | None = None
    vscode_edition: str = "stable"  # 'stable' or 'insider'


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
            insider_path = (
                Path(appdata) / "Code - Insiders" / "User" / "workspaceStorage"
            )
            paths.append((str(insider_path), "insider"))
    elif system == "Darwin":  # macOS
        home = Path.home()
        # VS Code Stable
        stable_path = (
            home / "Library" / "Application Support" / "Code" / "User" / "workspaceStorage"
        )
        paths.append((str(stable_path), "stable"))
        # VS Code Insiders
        insider_path = (
            home
            / "Library"
            / "Application Support"
            / "Code - Insiders"
            / "User"
            / "workspaceStorage"
        )
        paths.append((str(insider_path), "insider"))
    else:  # Linux and others
        home = Path.home()
        # VS Code Stable
        stable_path = home / ".config" / "Code" / "User" / "workspaceStorage"
        paths.append((str(stable_path), "stable"))
        # VS Code Insiders
        insider_path = (
            home / ".config" / "Code - Insiders" / "User" / "workspaceStorage"
        )
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
            copilot_chat_dir = (
                workspace_dir / "state.vscdb.backup"
            )  # Some versions use this
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
            with open(workspace_json, encoding="utf-8") as f:
                data = json.load(f)
                folder = data.get("folder", "")
                # folder is often a URI like file:///path/to/workspace
                if folder.startswith("file://"):
                    folder = folder[7:]
                    if platform.system() == "Windows" and folder.startswith("/"):
                        # Windows paths like /C:/path
                        folder = folder[1:]
                workspace_name = Path(folder).name if folder else None
                return workspace_name, folder if folder else None
        except (json.JSONDecodeError, OSError):
            pass
    return None, None


def _parse_chat_session_file(
    file_path: Path, workspace_name: str | None, workspace_path: str | None, edition: str
) -> ChatSession | None:
    """Parse a single chat session JSON file."""
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # Handle different JSON formats
    messages = []

    # Try to extract messages from various possible structures
    raw_messages = data.get("messages", []) or data.get("exchanges", [])

    for msg in raw_messages:
        if isinstance(msg, dict):
            role = msg.get("role", msg.get("type", "unknown"))
            # Normalize roles
            if role in ("human", "user"):
                role = "user"
            elif role in ("assistant", "copilot", "ai"):
                role = "assistant"

            content = msg.get("content", msg.get("text", msg.get("message", "")))

            # Handle content that might be a list
            if isinstance(content, list):
                content = "\n".join(
                    str(c.get("text", c) if isinstance(c, dict) else c)
                    for c in content
                )

            timestamp = msg.get("timestamp", msg.get("createdAt"))

            messages.append(ChatMessage(role=role, content=str(content), timestamp=timestamp))

    if not messages:
        return None

    session_id = data.get("sessionId", data.get("id", file_path.stem))
    created_at = data.get("createdAt", data.get("created"))
    updated_at = data.get("updatedAt", data.get("lastModified"))

    return ChatSession(
        session_id=str(session_id),
        workspace_name=workspace_name,
        workspace_path=workspace_path,
        messages=messages,
        created_at=created_at,
        updated_at=updated_at,
        source_file=str(file_path),
        vscode_edition=edition,
    )


def _parse_vscdb_file(
    file_path: Path, workspace_name: str | None, workspace_path: str | None, edition: str
) -> list[ChatSession]:
    """Parse a VS Code SQLite database file for chat sessions.
    
    VS Code stores extension state in SQLite databases with .vscdb extension.
    """
    sessions = []
    try:
        import sqlite3
        conn = sqlite3.connect(str(file_path))
        cursor = conn.cursor()
        
        # VS Code stores key-value pairs in the ItemTable
        cursor.execute("SELECT key, value FROM ItemTable WHERE key LIKE '%copilot%chat%' OR key LIKE '%sessions%'")
        rows = cursor.fetchall()
        
        for key, value in rows:
            if value:
                try:
                    data = json.loads(value)
                    # Try to parse as session data
                    if isinstance(data, dict):
                        session = _extract_session_from_dict(
                            data, workspace_name, workspace_path, edition, str(file_path)
                        )
                        if session:
                            sessions.append(session)
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                session = _extract_session_from_dict(
                                    item, workspace_name, workspace_path, edition, str(file_path)
                                )
                                if session:
                                    sessions.append(session)
                except (json.JSONDecodeError, TypeError):
                    pass
        
        conn.close()
    except Exception:
        # SQLite database might not have expected structure or might be corrupted
        pass
    
    return sessions


def _extract_session_from_dict(
    data: dict, workspace_name: str | None, workspace_path: str | None, 
    edition: str, source_file: str
) -> ChatSession | None:
    """Extract a chat session from a dictionary structure."""
    messages = []
    
    # Look for messages in various formats
    raw_messages = (
        data.get("messages", []) or
        data.get("exchanges", []) or
        data.get("history", [])
    )
    
    if not raw_messages:
        return None
    
    for msg in raw_messages:
        if isinstance(msg, dict):
            role = msg.get("role", msg.get("type", "unknown"))
            if role in ("human", "user"):
                role = "user"
            elif role in ("assistant", "copilot", "ai"):
                role = "assistant"
            
            content = msg.get("content", msg.get("text", msg.get("message", "")))
            if isinstance(content, list):
                content = "\n".join(
                    str(c.get("text", c) if isinstance(c, dict) else c)
                    for c in content
                )
            
            timestamp = msg.get("timestamp", msg.get("createdAt"))
            messages.append(ChatMessage(role=role, content=str(content), timestamp=timestamp))
    
    if not messages:
        return None
    
    session_id = data.get("sessionId", data.get("id", str(hash(source_file))))
    
    return ChatSession(
        session_id=str(session_id),
        workspace_name=workspace_name,
        workspace_path=workspace_path,
        messages=messages,
        created_at=data.get("createdAt", data.get("created")),
        updated_at=data.get("updatedAt", data.get("lastModified")),
        source_file=source_file,
        vscode_edition=edition,
    )


def scan_chat_sessions(
    storage_paths: list[tuple[str, str]] | None = None,
) -> Iterator[ChatSession]:
    """Scan for and parse all Copilot chat sessions.

    Args:
        storage_paths: Optional list of (path, edition) tuples to search.

    Yields:
        ChatSession objects for each found session.
    """
    for chat_dir, workspace_id, edition in find_copilot_chat_dirs(storage_paths):
        # Get workspace info
        workspace_name, workspace_path = _parse_workspace_json(chat_dir.parent)

        # Process JSON files in the directory
        for item in chat_dir.iterdir():
            if item.is_file():
                if item.suffix == ".json":
                    session = _parse_chat_session_file(
                        item, workspace_name, workspace_path, edition
                    )
                    if session:
                        yield session
                elif item.suffix == ".vscdb":
                    # Parse SQLite database files
                    sessions = _parse_vscdb_file(
                        item, workspace_name, workspace_path, edition
                    )
                    for session in sessions:
                        yield session

        # Also check for state.vscdb in parent directory
        state_db = chat_dir.parent / "state.vscdb"
        if state_db.exists():
            sessions = _parse_vscdb_file(
                state_db, workspace_name, workspace_path, edition
            )
            for session in sessions:
                yield session
