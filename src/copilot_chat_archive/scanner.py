"""Scanner module to find and parse VS Code Copilot chat history files.

Data structures are informed by:
- Arbuzov/copilot-chat-history (https://github.com/Arbuzov/copilot-chat-history)
- microsoft/vscode-copilot-chat (https://github.com/microsoft/vscode-copilot-chat)
"""

import json
import os
import platform
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote


@dataclass
class ToolInvocation:
    """Represents a tool invocation in a chat response.
    
    Based on ChatToolInvocation from Arbuzov/copilot-chat-history.
    """
    
    name: str
    input: str | None = None
    result: str | None = None
    status: str | None = None
    start_time: int | None = None
    end_time: int | None = None


@dataclass
class FileChange:
    """Represents a file change in a chat response.
    
    Based on ChatFileChange from Arbuzov/copilot-chat-history.
    """
    
    path: str
    diff: str | None = None
    content: str | None = None
    explanation: str | None = None
    language_id: str | None = None


@dataclass
class CommandRun:
    """Represents a command execution in a chat response.
    
    Based on ChatCommandRun from Arbuzov/copilot-chat-history.
    """
    
    command: str
    title: str | None = None
    result: str | None = None
    status: str | None = None
    output: str | None = None
    timestamp: int | None = None


@dataclass
class ContentBlock:
    """Represents a content block in an assistant response.
    
    Each block has a kind (e.g., 'text', 'thinking', 'tool') and content.
    This allows differentiation between thinking/reasoning and regular output.
    """
    
    kind: str  # 'text', 'thinking', 'tool', 'promptFile', etc.
    content: str


@dataclass
class ChatMessage:
    """Represents a single message in a chat session.
    
    Enhanced based on ChatMessage/ChatResponseItem from Arbuzov/copilot-chat-history.
    """

    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str | None = None
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    file_changes: list[FileChange] = field(default_factory=list)
    command_runs: list[CommandRun] = field(default_factory=list)
    content_blocks: list[ContentBlock] = field(default_factory=list)  # Structured content with kind


@dataclass
class ChatSession:
    """Represents a Copilot chat session.
    
    Based on ChatSession/ChatSessionData from Arbuzov/copilot-chat-history.
    """

    session_id: str
    workspace_name: str | None
    workspace_path: str | None
    messages: list[ChatMessage]
    created_at: str | None = None
    updated_at: str | None = None
    source_file: str | None = None
    vscode_edition: str = "stable"  # 'stable' or 'insider'
    custom_title: str | None = None
    requester_username: str | None = None
    responder_username: str | None = None
    source_file_mtime: float | None = None  # File modification time for incremental refresh
    source_file_size: int | None = None  # File size in bytes for incremental refresh


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
                # URL decode the path (e.g., %3A -> :, %20 -> space)
                folder = unquote(folder) if folder else ""
                workspace_name = Path(folder).name if folder else None
                return workspace_name, folder if folder else None
        except (json.JSONDecodeError, OSError):
            pass
    return None, None


def _get_first_truthy_value(*values):
    """Return the first truthy value from the arguments, or None if none are truthy."""
    for v in values:
        if v:
            return str(v)
    return None


def _extract_inline_reference_name(item: dict) -> str | None:
    """Extract the display name from an inline reference item and format as markdown.
    
    VS Code Copilot Chat includes file references as separate response items with
    kind="inlineReference". These can have different structures:
    - {kind: "inlineReference", name: "filename.ext", inlineReference: {path: "..."}}
    - {kind: "inlineReference", inlineReference: {name: "filename.ext", ...}}
    - {kind: "inlineReference", inlineReference: {path: "/path/to/file"}}
    
    VS Code renders these as markdown links with backticked labels: [`filename`](path)
    We format them similarly for consistent rendering.
    
    Returns the formatted reference as markdown, or None if no valid reference found.
    """
    name = None
    path = None
    
    # Try top-level name first
    if item.get("name"):
        name = str(item["name"])
    
    # Check inlineReference object for more details
    ref = item.get("inlineReference")
    if isinstance(ref, dict):
        # Try name from reference if not already found
        if not name and ref.get("name"):
            name = str(ref["name"])
        
        # Get the path for link target
        if ref.get("path"):
            path = str(ref["path"])
        elif ref.get("fsPath"):
            path = str(ref["fsPath"])
        elif ref.get("external"):
            path = str(ref["external"])
        
        # If no name yet, extract from path
        if not name and path:
            # Extract just the filename from the path
            if "/" in path:
                name = path.split("/")[-1]
            elif "\\" in path:
                name = path.split("\\")[-1]
            else:
                name = path
    
    if not name:
        return None
    
    # Format as backticked text (matching VS Code's inline code style for file refs)
    # If we have a path, we could make it a link, but for archived HTML the paths
    # may not be valid, so just use backticks for clear visual distinction
    return f"`{name}`"


def _extract_uri_filename(uri_obj: dict) -> str | None:
    """Extract filename from a URI object.
    
    URI objects in VS Code Copilot Chat have structures like:
    - {fsPath: "c:\\path\\file.ext", path: "/c:/path/file.ext", ...}
    - {path: "/path/to/file.ext", scheme: "file", ...}
    
    Returns the filename portion, or None if not extractable.
    """
    path = None
    
    # Try fsPath first (Windows-style)
    if uri_obj.get("fsPath"):
        path = str(uri_obj["fsPath"])
    elif uri_obj.get("path"):
        path = str(uri_obj["path"])
    elif uri_obj.get("external"):
        path = str(uri_obj["external"])
    
    if not path:
        return None
    
    # Extract just the filename
    if "\\" in path:
        return path.split("\\")[-1]
    elif "/" in path:
        return path.split("/")[-1]
    return path


def _extract_edit_group_text(item: dict, edit_type: str = "Edited") -> str | None:
    """Extract text representation from textEditGroup, notebookEditGroup, or codeblockUri.
    
    These items represent file edits and should be rendered as:
    "Edited `filename.ext`" or similar.
    """
    uri = item.get("uri")
    if not isinstance(uri, dict):
        return None
    
    filename = _extract_uri_filename(uri)
    if not filename:
        return None
    
    return f"{edit_type} `{filename}`"


def _merge_content_blocks(blocks: list[tuple[str, str]]) -> list[ContentBlock]:
    """Merge consecutive non-thinking content blocks into single blocks.
    
    Takes a list of (kind, content) tuples and merges consecutive non-thinking
    blocks into single ContentBlock objects. This ensures that inline references
    and text flow together as a single rendered unit.
    
    Args:
        blocks: List of (kind, content) tuples where kind is 'thinking' or 'text'
    
    Returns:
        List of ContentBlock objects with consecutive text blocks merged
    """
    if not blocks:
        return []
    
    merged = []
    current_kind = None
    current_content = []
    
    for kind, content in blocks:
        # Treat all non-thinking blocks as text for merging purposes
        effective_kind = "thinking" if kind == "thinking" else "text"
        
        if effective_kind == current_kind:
            # Continue accumulating content of the same kind
            # Add newline separator between blocks for proper markdown rendering
            current_content.append("\n\n")
            current_content.append(content)
        else:
            # Kind changed - flush the accumulated content
            if current_content:
                merged.append(ContentBlock(
                    kind=current_kind or "text",
                    content="".join(current_content)
                ))
            current_kind = effective_kind
            current_content = [content]
    
    # Flush any remaining content
    if current_content:
        merged.append(ContentBlock(
            kind=current_kind or "text",
            content="".join(current_content)
        ))
    
    return merged


def _parse_tool_invocations(raw_invocations: list) -> list[ToolInvocation]:
    """Parse tool invocations from raw data."""
    invocations = []
    for inv in raw_invocations:
        if isinstance(inv, dict):
            invocations.append(ToolInvocation(
                name=inv.get("name") or inv.get("toolName") or "unknown",
                input=_get_first_truthy_value(inv.get("input"), inv.get("arguments")),
                result=_get_first_truthy_value(inv.get("result"), inv.get("output")),
                status=inv.get("status"),
                start_time=inv.get("startTime"),
                end_time=inv.get("endTime"),
            ))
    return invocations


def _parse_file_changes(raw_changes: list) -> list[FileChange]:
    """Parse file changes from raw data."""
    changes = []
    for change in raw_changes:
        if isinstance(change, dict):
            path = change.get("path") or change.get("uri") or ""
            if path.startswith("file://"):
                path = path[7:]
            changes.append(FileChange(
                path=path,
                diff=change.get("diff"),
                content=change.get("content"),
                explanation=change.get("explanation"),
                language_id=change.get("languageId"),
            ))
    return changes


def _parse_command_runs(raw_commands: list) -> list[CommandRun]:
    """Parse command runs from raw data."""
    commands = []
    for cmd in raw_commands:
        if isinstance(cmd, dict):
            result_val = cmd.get("result")
            commands.append(CommandRun(
                command=cmd.get("command") or "unknown",
                title=cmd.get("title"),
                result=str(result_val) if result_val is not None else None,
                status=cmd.get("status"),
                output=cmd.get("output"),
                timestamp=cmd.get("timestamp"),
            ))
    return commands


def _get_file_metadata(file_path: str | Path) -> tuple[float | None, int | None]:
    """Get file modification time and size for incremental refresh.
    
    Args:
        file_path: Path to the file.
        
    Returns:
        Tuple of (mtime, size) or (None, None) if file cannot be accessed.
    """
    try:
        stat_result = os.stat(file_path)
        return stat_result.st_mtime, stat_result.st_size
    except OSError:
        return None, None


def _parse_chat_session_file(
    file_path: Path, workspace_name: str | None, workspace_path: str | None, edition: str
) -> ChatSession | None:
    """Parse a single chat session JSON file.
    
    Supports multiple formats including:
    - Standard messages array format
    - VS Code Copilot Chat "requests" format (from Arbuzov/copilot-chat-history)
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    messages = []

    # Try to extract messages from various possible structures
    # The "requests" format is from VS Code Copilot Chat (Arbuzov/copilot-chat-history)
    # Check each key explicitly to avoid the issue where empty list [] is falsy
    raw_messages = None
    for key in ("requests", "messages", "exchanges"):
        val = data.get(key)
        if val:  # Only use if non-empty
            raw_messages = val
            break
    raw_messages = raw_messages or []

    for msg in raw_messages:
        if isinstance(msg, dict):
            # Handle "requests" format from VS Code Copilot Chat
            # Each request has message.text (user) and response[] (assistant)
            if "message" in msg and isinstance(msg.get("message"), dict):
                # User message
                user_text = msg["message"].get("text", "")
                if user_text:
                    messages.append(ChatMessage(
                        role="user",
                        content=user_text,
                        timestamp=str(msg.get("timestamp")) if msg.get("timestamp") else None,
                    ))
                
                # Assistant response with tool invocations, file changes, etc.
                response_items = msg.get("response", [])
                if response_items:
                    response_content = []
                    raw_blocks = []  # Collect as (kind, content) tuples for merging
                    tool_invocations = []
                    file_changes = []
                    command_runs = []
                    
                    for item in response_items:
                        if isinstance(item, dict):
                            # Extract text content with kind info
                            if item.get("value"):
                                value = str(item["value"])
                                kind = item.get("kind", "text")
                                response_content.append(value)
                                raw_blocks.append((kind, value))
                            # Handle inline file references (VS Code Copilot Chat format)
                            # These appear as separate array items with kind="inlineReference"
                            elif item.get("kind") == "inlineReference":
                                ref_name = _extract_inline_reference_name(item)
                                if ref_name:
                                    # Append as inline text to flow with surrounding content
                                    response_content.append(ref_name)
                                    raw_blocks.append(("text", ref_name))
                            # Handle file edit indicators (textEditGroup, notebookEditGroup, codeblockUri)
                            elif item.get("kind") == "textEditGroup":
                                edit_text = _extract_edit_group_text(item, "Edited")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("text", edit_text))
                            elif item.get("kind") == "notebookEditGroup":
                                edit_text = _extract_edit_group_text(item, "Edited notebook")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("text", edit_text))
                            elif item.get("kind") == "codeblockUri":
                                edit_text = _extract_edit_group_text(item, "Editing")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("text", edit_text))
                            
                            # Extract tool invocations
                            if item.get("toolInvocations"):
                                tool_invocations.extend(
                                    _parse_tool_invocations(item["toolInvocations"])
                                )
                            
                            # Extract file changes
                            for key in ("fileChanges", "fileEdits", "files"):
                                if item.get(key):
                                    file_changes.extend(_parse_file_changes(item[key]))
                            
                            # Extract command runs
                            if item.get("commandRuns"):
                                command_runs.extend(_parse_command_runs(item["commandRuns"]))
                    
                    # Also check top-level of the request
                    if msg.get("toolInvocations"):
                        tool_invocations.extend(_parse_tool_invocations(msg["toolInvocations"]))
                    if msg.get("commandRuns"):
                        command_runs.extend(_parse_command_runs(msg["commandRuns"]))
                    if msg.get("fileChanges"):
                        file_changes.extend(_parse_file_changes(msg["fileChanges"]))
                    
                    if response_content or tool_invocations or file_changes or command_runs:
                        # Merge consecutive text blocks for better markdown rendering
                        content_blocks = _merge_content_blocks(raw_blocks)
                        messages.append(ChatMessage(
                            role="assistant",
                            content="".join(response_content),
                            tool_invocations=tool_invocations,
                            file_changes=file_changes,
                            command_runs=command_runs,
                            content_blocks=content_blocks,
                        ))
            else:
                # Standard message format
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
                
                # Parse tool invocations and file changes from standard format
                tool_invocations = _parse_tool_invocations(msg.get("toolInvocations", []))
                file_changes = _parse_file_changes(msg.get("fileChanges", []) or msg.get("fileEdits", []))
                command_runs = _parse_command_runs(msg.get("commandRuns", []))

                messages.append(ChatMessage(
                    role=role,
                    content=str(content),
                    timestamp=str(timestamp) if timestamp else None,
                    tool_invocations=tool_invocations,
                    file_changes=file_changes,
                    command_runs=command_runs,
                ))

    if not messages:
        return None

    session_id = data.get("sessionId", data.get("id", file_path.stem))
    created_at = data.get("createdAt", data.get("created", data.get("creationDate")))
    updated_at = data.get("updatedAt", data.get("lastModified", data.get("lastMessageDate")))

    # Capture file metadata for incremental refresh
    source_file_mtime, source_file_size = _get_file_metadata(file_path)

    return ChatSession(
        session_id=str(session_id),
        workspace_name=workspace_name,
        workspace_path=workspace_path,
        messages=messages,
        created_at=str(created_at) if created_at else None,
        updated_at=str(updated_at) if updated_at else None,
        source_file=str(file_path),
        vscode_edition=edition,
        custom_title=data.get("customTitle"),
        requester_username=data.get("requesterUsername"),
        responder_username=data.get("responderUsername"),
        source_file_mtime=source_file_mtime,
        source_file_size=source_file_size,
    )


def _parse_vscdb_file(
    file_path: Path, workspace_name: str | None, workspace_path: str | None, edition: str
) -> list[ChatSession]:
    """Parse a VS Code SQLite database file for chat sessions.
    
    VS Code stores extension state in SQLite databases with .vscdb extension.
    """
    sessions = []
    try:
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
    except (sqlite3.DatabaseError, sqlite3.OperationalError, OSError):
        # SQLite database might not have expected structure or might be corrupted
        pass
    
    return sessions


def _extract_session_from_dict(
    data: dict, workspace_name: str | None, workspace_path: str | None, 
    edition: str, source_file: str
) -> ChatSession | None:
    """Extract a chat session from a dictionary structure.
    
    Supports the VS Code Copilot Chat format with requests, tool invocations, etc.
    """
    messages = []
    
    # Look for messages in various formats - "requests" is the VS Code Copilot format
    # Check each key explicitly to avoid the issue where empty list [] is falsy
    raw_messages = None
    for key in ("requests", "messages", "exchanges", "history"):
        val = data.get(key)
        if val:  # Only use if non-empty
            raw_messages = val
            break
    
    if not raw_messages:
        return None
    
    for msg in raw_messages:
        if isinstance(msg, dict):
            # Handle "requests" format from VS Code Copilot Chat
            if "message" in msg and isinstance(msg.get("message"), dict):
                user_text = msg["message"].get("text", "")
                if user_text:
                    messages.append(ChatMessage(
                        role="user",
                        content=user_text,
                        timestamp=str(msg.get("timestamp")) if msg.get("timestamp") else None,
                    ))
                
                response_items = msg.get("response", [])
                if response_items:
                    response_content = []
                    raw_blocks = []  # Collect as (kind, content) tuples for merging
                    tool_invocations = []
                    file_changes = []
                    command_runs = []
                    
                    for item in response_items:
                        if isinstance(item, dict):
                            if item.get("value"):
                                value = str(item["value"])
                                kind = item.get("kind", "text")
                                response_content.append(value)
                                raw_blocks.append((kind, value))
                            # Handle inline file references (VS Code Copilot Chat format)
                            elif item.get("kind") == "inlineReference":
                                ref_name = _extract_inline_reference_name(item)
                                if ref_name:
                                    response_content.append(ref_name)
                                    raw_blocks.append(("text", ref_name))
                            # Handle file edit indicators
                            elif item.get("kind") == "textEditGroup":
                                edit_text = _extract_edit_group_text(item, "Edited")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("text", edit_text))
                            elif item.get("kind") == "notebookEditGroup":
                                edit_text = _extract_edit_group_text(item, "Edited notebook")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("text", edit_text))
                            elif item.get("kind") == "codeblockUri":
                                edit_text = _extract_edit_group_text(item, "Editing")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("text", edit_text))
                            if item.get("toolInvocations"):
                                tool_invocations.extend(_parse_tool_invocations(item["toolInvocations"]))
                            for key in ("fileChanges", "fileEdits", "files"):
                                if item.get(key):
                                    file_changes.extend(_parse_file_changes(item[key]))
                            if item.get("commandRuns"):
                                command_runs.extend(_parse_command_runs(item["commandRuns"]))
                    
                    if response_content or tool_invocations or file_changes or command_runs:
                        # Merge consecutive text blocks for better markdown rendering
                        content_blocks = _merge_content_blocks(raw_blocks)
                        messages.append(ChatMessage(
                            role="assistant",
                            content="".join(response_content),
                            tool_invocations=tool_invocations,
                            file_changes=file_changes,
                            command_runs=command_runs,
                            content_blocks=content_blocks,
                        ))
            else:
                # Standard format
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
                tool_invocations = _parse_tool_invocations(msg.get("toolInvocations", []))
                file_changes = _parse_file_changes(msg.get("fileChanges", []) or msg.get("fileEdits", []))
                command_runs = _parse_command_runs(msg.get("commandRuns", []))
                
                messages.append(ChatMessage(
                    role=role,
                    content=str(content),
                    timestamp=str(timestamp) if timestamp else None,
                    tool_invocations=tool_invocations,
                    file_changes=file_changes,
                    command_runs=command_runs,
                ))
    
    if not messages:
        return None
    
    session_id = data.get("sessionId", data.get("id", str(hash(source_file))))
    created_at = data.get("createdAt", data.get("created", data.get("creationDate")))
    updated_at = data.get("updatedAt", data.get("lastModified", data.get("lastMessageDate")))
    
    # Capture file metadata for incremental refresh
    source_file_mtime, source_file_size = _get_file_metadata(source_file)

    return ChatSession(
        session_id=str(session_id),
        workspace_name=workspace_name,
        workspace_path=workspace_path,
        messages=messages,
        created_at=str(created_at) if created_at else None,
        updated_at=str(updated_at) if updated_at else None,
        source_file=source_file,
        vscode_edition=edition,
        custom_title=data.get("customTitle"),
        requester_username=data.get("requesterUsername"),
        responder_username=data.get("responderUsername"),
        source_file_mtime=source_file_mtime,
        source_file_size=source_file_size,
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
