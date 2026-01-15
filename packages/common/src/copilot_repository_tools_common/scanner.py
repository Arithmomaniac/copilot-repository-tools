"""Scanner module to find and parse VS Code Copilot chat history files.

Data structures are informed by:
- Arbuzov/copilot-chat-history (https://github.com/Arbuzov/copilot-chat-history)
- microsoft/vscode-copilot-chat (https://github.com/microsoft/vscode-copilot-chat)
"""

import difflib
import json
import os
import platform
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import orjson


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
    source_type: str | None = None  # 'mcp' or 'internal'
    invocation_message: str | None = None  # Pretty display message (e.g., "Reading file.txt, lines 1 to 100")


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
    description: str | None = None  # Optional description (e.g., generatedTitle for thinking blocks)


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
    cached_markdown: str | None = None  # Pre-computed markdown for this message


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
    type: str = "vscode"  # 'vscode' or 'cli'
    raw_json: bytes | None = None  # Original raw JSON bytes from source file


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
    
    Handles URIs that can be either:
    - A dict object: {$mid: 1, path: "/path/to/file", scheme: "file", ...}
    - A string: "file:///path/to/file"
    """
    uri = item.get("uri")
    
    # Handle URI as dict object (common in VS Code response data)
    if isinstance(uri, dict):
        filename = _extract_uri_filename(uri)
        if not filename:
            return None
        return f"{edit_type} `{filename}`"
    
    # Handle URI as string
    if isinstance(uri, str):
        # Extract filename from URI string
        path = uri
        path = path.removeprefix("file://")
        if "\\" in path:
            filename = path.split("\\")[-1]
        elif "/" in path:
            filename = path.split("/")[-1]
        else:
            filename = path
        return f"{edit_type} `{filename}`" if filename else None
    
    return None


def _extract_uri_path(uri: dict) -> str:
    """Extract the full path from a VS Code URI object."""
    if not isinstance(uri, dict):
        return ""
    
    path = uri.get("fsPath") or uri.get("path") or uri.get("external") or ""
    
    # Handle file:// URIs
    if isinstance(path, str) and path.startswith("file://"):
        path = path[7:]
    
    return path


def _apply_edits_to_content(original_content: str, edits: list[list[dict]]) -> tuple[str, str] | None:
    """Apply edits to original content and return (original, modified) for diff generation.
    
    VS Code textEditGroup stores edits as:
    edits: [[{range: {startLineNumber, startColumn, endLineNumber, endColumn}, text: "new content"}], ...]
    
    Returns tuple of (original_content, modified_content) for diff generation,
    or None if edits cannot be applied.
    """
    if not edits or not isinstance(edits, list) or not original_content:
        return None
    
    # Split content into lines (1-indexed to match VS Code)
    lines = original_content.split("\n")
    modified_lines = lines.copy()
    
    # Collect all edits and sort by position (reverse order to apply from end to start)
    all_edits = []
    for edit_batch in edits:
        if not isinstance(edit_batch, list):
            continue
        for edit in edit_batch:
            if isinstance(edit, dict) and "range" in edit:
                all_edits.append(edit)
    
    # Sort by start position, reverse order (so we can apply from end to beginning)
    all_edits.sort(
        key=lambda e: (
            e.get("range", {}).get("startLineNumber", 0),
            e.get("range", {}).get("startColumn", 0)
        ),
        reverse=True
    )
    
    try:
        for edit in all_edits:
            edit_range = edit.get("range", {})
            new_text = edit.get("text", "")
            
            start_line = edit_range.get("startLineNumber", 1) - 1  # Convert to 0-indexed
            start_col = edit_range.get("startColumn", 1) - 1
            end_line = edit_range.get("endLineNumber", 1) - 1
            end_col = edit_range.get("endColumn", 1) - 1
            
            # Validate line indices
            if start_line < 0 or end_line >= len(modified_lines):
                continue
            
            # Validate column indices
            if start_col < 0 or end_col < 0:
                continue
            
            # Apply the edit
            if start_line == end_line:
                # Single line edit - validate column bounds
                line = modified_lines[start_line]
                if start_col > len(line) or end_col > len(line):
                    continue
                modified_lines[start_line] = line[:start_col] + new_text + line[end_col:]
            else:
                # Multi-line edit - validate column bounds on each line
                first_line = modified_lines[start_line]
                last_line = modified_lines[end_line] if end_line < len(modified_lines) else ""
                if start_col > len(first_line) or end_col > len(last_line):
                    continue
                first_part = first_line[:start_col]
                last_part = last_line[end_col:]
                new_lines = (first_part + new_text + last_part).split("\n")
                modified_lines[start_line:end_line + 1] = new_lines
        
        return (original_content, "\n".join(modified_lines))
    except (IndexError, KeyError, TypeError):
        return None


def _generate_unified_diff(original: str, modified: str, filename: str = "file") -> str:
    """Generate a unified diff between original and modified content."""
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    
    diff = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    
    return "".join(diff)


def _format_edits_as_diff(
    edits: list[list[dict]],
    original_content: str | None = None,
    filename: str = "file"
) -> str | None:
    """Format textEditGroup edits as a diff-style representation.
    
    If original_content is provided (from a recent file read), generates a proper unified diff.
    Otherwise, generates a simplified diff showing only the insertions.
    
    Args:
        edits: List of edit batches. Each batch is a list of dicts with
               {range: {startLineNumber, startColumn, endLineNumber, endColumn}, text: "new content"}
        original_content: Optional original file content from a recent read
        filename: Filename to use in the diff header
        
    Returns:
        Diff string or None if no edits
    """
    if not edits or not isinstance(edits, list):
        return None
    
    # If we have original content, try to generate a proper unified diff
    if original_content:
        result = _apply_edits_to_content(original_content, edits)
        if result:
            original, modified = result
            diff = _generate_unified_diff(original, modified, filename)
            if diff:
                return diff
    
    # Fallback: Generate simplified diff showing insertions
    diff_lines = []
    
    for edit_batch in edits:
        if not isinstance(edit_batch, list):
            continue
        
        for edit in edit_batch:
            if not isinstance(edit, dict):
                continue
            
            edit_range = edit.get("range", {})
            new_text = edit.get("text", "")
            
            if not new_text:
                continue
            
            # Extract line numbers
            start_line = edit_range.get("startLineNumber", "?")
            end_line = edit_range.get("endLineNumber", "?")
            
            # Format as a simple diff showing the insertion
            if start_line == end_line:
                diff_lines.append(f"@@ Line {start_line} @@")
            else:
                diff_lines.append(f"@@ Lines {start_line}-{end_line} @@")
            
            # Show the new text with + prefix for each line
            for line in new_text.split("\n"):
                diff_lines.append(f"+ {line}")
            
            diff_lines.append("")  # Empty line between edits
    
    if diff_lines:
        return "\n".join(diff_lines).rstrip()
    
    return None


def _parse_text_edit_group(item: dict, file_contents_cache: dict | None = None) -> FileChange | None:
    """Parse a textEditGroup item into a FileChange object with diff content.
    
    VS Code stores file edits as textEditGroup items with:
    - uri: VS Code URI object for the file
    - edits: Array of edit batches, each containing edits with range and text
    
    Args:
        item: The textEditGroup item from the response
        file_contents_cache: Optional dict mapping file paths to their content
                            (from recent file reads in the same response)
    """
    uri = item.get("uri")
    if not isinstance(uri, dict):
        return None
    
    path = _extract_uri_path(uri)
    if not path:
        return None
    
    # Extract filename for diff header
    filename = _extract_uri_filename(uri) or "file"
    
    # Check if we have cached content for this file
    original_content = None
    if file_contents_cache:
        # Try direct path match first
        original_content = file_contents_cache.get(path)
        
        if not original_content:
            # Try with just the filename as key
            original_content = file_contents_cache.get(filename)
        
        if not original_content:
            # Try normalized path comparison using Path for robust matching
            path_basename = Path(path).name if path else ""
            for cached_path, content in file_contents_cache.items():
                cached_basename = Path(cached_path).name if cached_path else ""
                # Match if basenames are the same (case-sensitive)
                if path_basename and cached_basename and path_basename == cached_basename:
                    original_content = content
                    break
    
    # Generate diff from edits
    edits = item.get("edits", [])
    diff = _format_edits_as_diff(edits, original_content, filename)
    
    return FileChange(
        path=path,
        diff=diff,
        content=None,
        explanation=None,
        language_id=None,
    )


def _extract_file_content_from_tool(item: dict) -> tuple[str, str] | None:
    """Extract file path and content from a readFile tool invocation result.
    
    VS Code stores file read results in resultDetails for MCP tools,
    or in the tool's result content.
    
    Returns tuple of (file_path, content) or None if not a file read.
    """
    if not isinstance(item, dict):
        return None
    
    tool_id = item.get("toolId", "")
    
    # Check if this is a file read tool
    if "readFile" not in tool_id and "read_file" not in tool_id.lower():
        return None
    
    # Get file path from toolSpecificData
    tool_data = item.get("toolSpecificData", {})
    file_path = None
    
    if isinstance(tool_data, dict):
        file_info = tool_data.get("file", {})
        if isinstance(file_info, dict):
            file_uri = file_info.get("uri", {})
            if isinstance(file_uri, dict):
                file_path = file_uri.get("fsPath") or file_uri.get("path")
    
    if not file_path:
        return None
    
    # Get file content from resultDetails
    result_details = item.get("resultDetails", {})
    content = None
    
    if isinstance(result_details, dict):
        # MCP format: resultDetails.output is an array of {value: "content"}
        outputs = result_details.get("output", [])
        if isinstance(outputs, list):
            for out in outputs:
                if isinstance(out, dict) and out.get("value"):
                    content = str(out["value"])
                    break
    
    if content:
        return (file_path, content)
    
    return None


def _merge_content_blocks(blocks: list[tuple[str, str, str | None]]) -> list[ContentBlock]:
    """Merge consecutive non-thinking content blocks into single blocks.
    
    Takes a list of (kind, content, description) tuples and merges consecutive non-thinking
    blocks into single ContentBlock objects. This ensures that inline references
    and text flow together as a single rendered unit.
    
    Args:
        blocks: List of (kind, content, description) tuples where kind is 'thinking' or 'text'
                and description is optional (used for thinking block descriptions)
    
    Returns:
        List of ContentBlock objects with consecutive text blocks merged
    """
    if not blocks:
        return []
    
    merged = []
    current_kind = None
    current_content = []
    current_description = None
    
    for block in blocks:
        # Handle both 2-tuples and 3-tuples for backward compatibility
        if len(block) == 3:
            kind, content, description = block
        else:
            kind, content = block
            description = None
        
        # Never merge toolInvocation blocks - each should be separate for individual italic formatting
        # Keep thinking separate, merge other text-like content
        if kind == "toolInvocation":
            # Flush any accumulated content first
            if current_content:
                merged.append(ContentBlock(
                    kind=current_kind or "text",
                    content="".join(current_content),
                    description=current_description
                ))
                current_content = []
                current_kind = None
                current_description = None
            # Add toolInvocation as standalone block
            merged.append(ContentBlock(kind="toolInvocation", content=content))
        elif kind == "thinking":
            effective_kind = "thinking"
            if effective_kind == current_kind:
                current_content.append("\n\n")
                current_content.append(content)
                # Keep the first description if we're merging thinking blocks
                if description and not current_description:
                    current_description = description
            else:
                if current_content:
                    merged.append(ContentBlock(
                        kind=current_kind or "text",
                        content="".join(current_content),
                        description=current_description
                    ))
                current_kind = effective_kind
                current_content = [content]
                current_description = description
        else:
            # Regular text content - can be merged
            effective_kind = "text"
            if effective_kind == current_kind:
                # Only add spacing between longer content blocks
                # Short blocks (< 100 chars, likely inline code) get single space
                if len(current_content[-1]) > 100 and len(content) > 100:
                    current_content.append("\n\n")
                else:
                    current_content.append(" ")
                current_content.append(content)
            else:
                if current_content:
                    merged.append(ContentBlock(
                        kind=current_kind or "text",
                        content="".join(current_content),
                        description=current_description
                    ))
                current_kind = effective_kind
                current_content = [content]
                current_description = None
    
    # Flush any remaining content
    if current_content:
        merged.append(ContentBlock(
            kind=current_kind or "text",
            content="".join(current_content),
            description=current_description
        ))
    
    return merged


def _parse_tool_invocation_serialized(item: dict) -> ToolInvocation | None:
    """Parse a single toolInvocationSerialized item from VS Code response.
    
    VS Code stores tool invocations as individual response items with kind=toolInvocationSerialized.
    Structure: {kind, toolId, invocationMessage, toolSpecificData, isComplete, resultDetails, ...}
    
    Tool types and their data:
    - Terminal tools: toolSpecificData.commandLine (can be string or {original, toolEdited})
    - File tools: toolSpecificData.file.uri
    - MCP tools: resultDetails.input (string) and resultDetails.output (array)
    """
    if not isinstance(item, dict):
        return None
    
    tool_id = item.get("toolId", "unknown")
    invocation_msg = item.get("invocationMessage", "")
    # invocationMessage can be a dict with 'value' field or a simple string
    if isinstance(invocation_msg, dict) and "value" in invocation_msg:
        invocation_msg = invocation_msg["value"]
    tool_data = item.get("toolSpecificData", {})
    result_details = item.get("resultDetails", {})
    
    # Extract input from toolSpecificData based on tool kind
    input_data = None
    result_data = None

    if isinstance(tool_data, dict):
        # Terminal command data - commandLine can be string or object
        if "commandLine" in tool_data:
            cmd_line = tool_data.get("commandLine")
            if isinstance(cmd_line, dict):
                # Use toolEdited if available (modified by AI), otherwise original
                input_data = cmd_line.get("toolEdited") or cmd_line.get("original")
            else:
                input_data = str(cmd_line) if cmd_line else None
        
        # File tool data - extract file URI
        elif "file" in tool_data and isinstance(tool_data.get("file"), dict):
            file_info = tool_data["file"]
            file_uri = file_info.get("uri", {})
            if isinstance(file_uri, dict):
                # Extract path from VS Code URI object
                file_path = file_uri.get("fsPath") or file_uri.get("path") or ""
                if file_path:
                    input_data = file_path
            elif isinstance(file_uri, str):
                input_data = file_uri
        
        # Other tool types might have different keys
        elif "input" in tool_data:
            val = tool_data.get("input")
            input_data = str(val) if val is not None else None
    
    # Extract MCP tool results if available
    if isinstance(result_details, dict):
        # MCP tools store input/output in resultDetails
        if "input" in result_details:
            mcp_input = result_details.get("input")
            if mcp_input and not input_data:
                input_data = str(mcp_input)
        
        # Extract output for MCP tools
        if "output" in result_details:
            outputs = result_details.get("output", [])
            if isinstance(outputs, list):
                output_parts = []
                for out in outputs:
                    if isinstance(out, dict) and out.get("value"):
                        output_parts.append(str(out["value"]))
                if output_parts:
                    result_data = "\n".join(output_parts)
    
    # Status: use isComplete to determine status
    status = "completed" if item.get("isComplete") else "pending"
    
    # Extract source type (mcp vs internal)
    source = item.get("source", {})
    source_type = source.get("type") if isinstance(source, dict) else None
    
    # For terminal tools, also extract command output if available
    if isinstance(tool_data, dict) and tool_data.get("kind") == "terminal":
        terminal_output = tool_data.get("terminalCommandOutput", {})
        if isinstance(terminal_output, dict) and terminal_output.get("text") and not result_data:
            result_data = terminal_output.get("text")
    
    return ToolInvocation(
        name=str(tool_id) if tool_id else "unknown",
        input=input_data,
        result=result_data,
        status=status,
        start_time=None,
        end_time=None,
        source_type=source_type,
        invocation_message=invocation_msg if isinstance(invocation_msg, str) else None,
    )



def _parse_tool_invocations(raw_invocations: list) -> list[ToolInvocation]:
    """Parse tool invocations from raw data (legacy format)."""
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
            path = path.removeprefix("file://")
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


def _get_file_metadata(file_path: str | Path | None) -> tuple[float | None, int | None]:
    """Get file modification time and size for incremental refresh.

    Args:
        file_path: Path to the file, or None.

    Returns:
        Tuple of (mtime, size) or (None, None) if file cannot be accessed or path is None.
    """
    if file_path is None:
        return None, None
    try:
        stat_result = Path(file_path).stat()
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
        with file_path.open("rb") as f:
            raw_json_bytes = f.read()
            data = orjson.loads(raw_json_bytes)
    except (orjson.JSONDecodeError, OSError):
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
                    file_contents_cache = {}  # Cache file contents from readFile tools

                    # First pass: collect file contents from readFile tool invocations
                    for item in response_items:
                        if isinstance(item, dict) and item.get("kind") == "toolInvocationSerialized":
                            file_content = _extract_file_content_from_tool(item)
                            if file_content:
                                cached_path, cached_content = file_content
                                file_contents_cache[cached_path] = cached_content

                    # Second pass: process all response items
                    for item in response_items:
                        if isinstance(item, dict):
                            kind = item.get("kind")
                            
                            # Handle tool invocations (current VS Code format)
                            if kind == "toolInvocationSerialized":
                                tool_inv = _parse_tool_invocation_serialized(item)
                                if tool_inv:
                                    tool_invocations.append(tool_inv)
                                # Also extract the invocation message as content
                                if item.get("invocationMessage"):
                                    msg_text = item["invocationMessage"]
                                    # invocationMessage can be a dict with 'value' field
                                    if isinstance(msg_text, dict) and "value" in msg_text:
                                        msg_text = msg_text["value"]
                                    msg_text = str(msg_text)
                                    response_content.append(msg_text)
                                    raw_blocks.append(("toolInvocation", msg_text, None))
                            
                            # Extract text content with kind info
                            elif item.get("value"):
                                value = item["value"]
                                # If value is a dict with nested 'value', extract the string
                                if isinstance(value, dict) and "value" in value:
                                    value = value["value"]
                                # Convert to string if not already
                                if not isinstance(value, str):
                                    value = str(value)
                                kind = kind or "text"
                                response_content.append(value)
                                # For thinking blocks, extract the generatedTitle as description
                                description = None
                                if kind == "thinking":
                                    description = item.get("generatedTitle")
                                raw_blocks.append((kind, value, description))
                            # Handle inline file references (VS Code Copilot Chat format)
                            # These appear as separate array items with kind="inlineReference"
                            elif kind == "inlineReference":
                                ref_name = _extract_inline_reference_name(item)
                                if ref_name:
                                    # Append as inline text to flow with surrounding content
                                    response_content.append(ref_name)
                                    raw_blocks.append(("text", ref_name, None))
                            # Handle file edit indicators (textEditGroup, notebookEditGroup, codeblockUri)
                            elif kind == "textEditGroup":
                                edit_text = _extract_edit_group_text(item, "Edited")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("toolInvocation", edit_text, None))
                                # Parse the actual edits as FileChange with diff content
                                # Pass file contents cache for better diff generation
                                file_change = _parse_text_edit_group(item, file_contents_cache)
                                if file_change:
                                    file_changes.append(file_change)
                            elif kind == "notebookEditGroup":
                                edit_text = _extract_edit_group_text(item, "Edited notebook")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("toolInvocation", edit_text, None))
                            elif kind == "codeblockUri":
                                edit_text = _extract_edit_group_text(item, "Editing")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("toolInvocation", edit_text, None))
                            
                            # Extract tool invocations (legacy format - nested array)
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
        raw_json=raw_json_bytes,
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
        
        for _key, value in rows:
            if value:
                try:
                    # Preserve raw JSON bytes for storage
                    raw_json_bytes = value if isinstance(value, bytes) else value.encode('utf-8')
                    data = orjson.loads(value)
                    # Try to parse as session data
                    if isinstance(data, dict):
                        session = _extract_session_from_dict(
                            data, workspace_name, workspace_path, edition, str(file_path),
                            raw_json=raw_json_bytes
                        )
                        if session:
                            sessions.append(session)
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                # For list items, serialize each item back to bytes
                                item_json = orjson.dumps(item)
                                session = _extract_session_from_dict(
                                    item, workspace_name, workspace_path, edition, str(file_path),
                                    raw_json=item_json
                                )
                                if session:
                                    sessions.append(session)
                except (orjson.JSONDecodeError, TypeError):
                    pass
        
        conn.close()
    except (sqlite3.DatabaseError, sqlite3.OperationalError, OSError):
        # SQLite database might not have expected structure or might be corrupted
        pass
    
    return sessions


def _extract_session_from_dict(
    data: dict, workspace_name: str | None, workspace_path: str | None, 
    edition: str, source_file: str | None, raw_json: bytes | None = None
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
                    file_contents_cache = {}  # Cache file contents from readFile tools

                    # First pass: collect file contents from readFile tool invocations
                    for item in response_items:
                        if isinstance(item, dict) and item.get("kind") == "toolInvocationSerialized":
                            file_content = _extract_file_content_from_tool(item)
                            if file_content:
                                cached_path, cached_content = file_content
                                file_contents_cache[cached_path] = cached_content

                    # Second pass: process all response items
                    for item in response_items:
                        if isinstance(item, dict):
                            kind = item.get("kind")
                            
                            # Handle tool invocations (current VS Code format)
                            if kind == "toolInvocationSerialized":
                                tool_inv = _parse_tool_invocation_serialized(item)
                                if tool_inv:
                                    tool_invocations.append(tool_inv)
                                # Also extract the invocation message as content
                                if item.get("invocationMessage"):
                                    msg_text = item["invocationMessage"]
                                    # invocationMessage can be a dict with 'value' field
                                    if isinstance(msg_text, dict) and "value" in msg_text:
                                        msg_text = msg_text["value"]
                                    msg_text = str(msg_text)
                                    response_content.append(msg_text)
                                    raw_blocks.append(("toolInvocation", msg_text, None))
                            
                            # Extract text content with kind info
                            elif item.get("value"):
                                value = item["value"]
                                # If value is a dict with nested 'value', extract the string
                                if isinstance(value, dict) and "value" in value:
                                    value = value["value"]
                                # Convert to string if not already
                                if not isinstance(value, str):
                                    value = str(value)
                                kind = kind or "text"
                                response_content.append(value)
                                # For thinking blocks, extract the generatedTitle as description
                                description = None
                                if kind == "thinking":
                                    description = item.get("generatedTitle")
                                raw_blocks.append((kind, value, description))
                            # Handle inline file references (VS Code Copilot Chat format)
                            elif kind == "inlineReference":
                                ref_name = _extract_inline_reference_name(item)
                                if ref_name:
                                    response_content.append(ref_name)
                                    raw_blocks.append(("text", ref_name, None))
                            # Handle file edit indicators
                            elif kind == "textEditGroup":
                                edit_text = _extract_edit_group_text(item, "Edited")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("toolInvocation", edit_text, None))
                                # Parse actual edits with file contents cache for better diffs
                                file_change = _parse_text_edit_group(item, file_contents_cache)
                                if file_change:
                                    file_changes.append(file_change)
                            elif kind == "notebookEditGroup":
                                edit_text = _extract_edit_group_text(item, "Edited notebook")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("toolInvocation", edit_text, None))
                            elif kind == "codeblockUri":
                                edit_text = _extract_edit_group_text(item, "Editing")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("toolInvocation", edit_text, None))
                            
                            # Extract tool invocations (legacy format)
                            if item.get("toolInvocations"):
                                tool_invocations.extend(_parse_tool_invocations(item["toolInvocations"]))
                            elif item.get("kind") == "textEditGroup":
                                edit_text = _extract_edit_group_text(item, "Edited")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("toolInvocation", edit_text, None))
                            elif item.get("kind") == "notebookEditGroup":
                                edit_text = _extract_edit_group_text(item, "Edited notebook")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("toolInvocation", edit_text, None))
                            elif item.get("kind") == "codeblockUri":
                                edit_text = _extract_edit_group_text(item, "Editing")
                                if edit_text:
                                    response_content.append(edit_text)
                                    raw_blocks.append(("toolInvocation", edit_text, None))
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
        raw_json=raw_json,
    )


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


def _parse_cli_jsonl_file(file_path: Path) -> ChatSession | None:
    """Parse a GitHub Copilot CLI JSONL session file.
    
    CLI sessions are stored as JSONL (JSON Lines) where each line is a JSON object
    representing an event. The event-based format uses types like:
    - session.start: Session initialization with sessionId, copilotVersion, etc.
    - session.info: Info messages (authentication, mcp, folder_trust)
    - session.model_change: Model switching (newModel)
    - session.error: Error events (errorType, message)
    - session.truncation: Context window management events
    - user.message: User prompts with content and attachments
    - system.message: System-level messages
    - assistant.message: Assistant responses with content and toolRequests
    - assistant.turn_start/end: Turn boundaries
    - tool.execution_start/complete: Tool invocation lifecycle
    - tool.user_requested: User-requested tool executions
    - abort: Session/turn abort events
    
    This function renders CLI sessions similarly to how vscode-copilot-chat renders
    background chats:
    - Consecutive assistant messages are combined into one
    - Tool calls are displayed inline within the assistant message content
    
    Args:
        file_path: Path to the JSONL file.

    Returns:
        ChatSession object or None if parsing fails.
    """
    try:
        events = []

        with file_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = orjson.loads(line)
                    events.append(data)
                except orjson.JSONDecodeError:
                    continue

        if not events:
            return None

        # Extract session metadata from session.start event
        session_id = None
        created_at = None

        for event in events:
            event_type = event.get("type", "")
            event_data = event.get("data", {})

            if event_type == "session.start":
                session_id = event_data.get("sessionId")
                created_at = event_data.get("startTime") or event.get("timestamp")
                # Note: copilot_version and producer are available in event_data but not currently used
                break  # Only need the first session.start event

        # If no session.start, use file stem as session ID
        if not session_id:
            session_id = file_path.stem

        # Extract workspace from first folder_trust event
        workspace_path = None
        workspace_name = None
        requester_username = None

        for event in events:
            if event.get("type") == "session.info":
                event_data = event.get("data", {})
                info_type = event_data.get("infoType")
                message = event_data.get("message", "")
                
                if info_type == "folder_trust" and not workspace_path:
                    # Parse "Folder C:\_SRC\ZTS has been added to trusted folders."
                    if message.startswith("Folder ") and " has been added" in message:
                        folder_path = message[7:message.find(" has been added")]
                        workspace_path = folder_path
                        workspace_name = Path(folder_path).name

                elif info_type == "authentication" and not requester_username and "as user: " in message:
                    # Parse "Logged in with gh as user: Arithmomaniac"
                    requester_username = message.split("as user: ")[-1].strip()
        
        # Build tool execution map: toolCallId -> (start_data, complete_data, user_requested)
        tool_executions: dict = {}
        for event in events:
            event_type = event.get("type", "")
            event_data = event.get("data", {})
            
            if event_type == "tool.execution_start":
                tool_call_id = event_data.get("toolCallId")
                if tool_call_id:
                    if tool_call_id not in tool_executions:
                        tool_executions[tool_call_id] = {"start": None, "complete": None, "user_requested": False}
                    tool_executions[tool_call_id]["start"] = event
            
            elif event_type == "tool.user_requested":
                # User explicitly requested this tool execution
                tool_call_id = event_data.get("toolCallId")
                if tool_call_id:
                    if tool_call_id not in tool_executions:
                        tool_executions[tool_call_id] = {"start": None, "complete": None, "user_requested": True}
                    else:
                        tool_executions[tool_call_id]["user_requested"] = True
                    
            elif event_type == "tool.execution_complete":
                tool_call_id = event_data.get("toolCallId")
                if tool_call_id:
                    if tool_call_id not in tool_executions:
                        tool_executions[tool_call_id] = {"start": None, "complete": None, "user_requested": False}
                    tool_executions[tool_call_id]["complete"] = event
        
        # Build messages using VSCode-style rendering:
        # - Process events in order
        # - Combine consecutive assistant messages
        # - Interleave tool invocations inline with content
        messages: list[ChatMessage] = []
        
        # State for building the current assistant message
        current_assistant_content_blocks: list[ContentBlock] = []
        current_assistant_tool_invocations: list[ToolInvocation] = []
        current_assistant_command_runs: list[CommandRun] = []
        current_assistant_timestamp: str | None = None
        pending_tool_requests: dict[str, dict] = {}  # toolCallId -> request data

        def _flush_pending_assistant_message():
            """Flush accumulated assistant content blocks into a single message."""
            nonlocal current_assistant_content_blocks, current_assistant_tool_invocations
            nonlocal current_assistant_command_runs, current_assistant_timestamp

            has_content = (
                current_assistant_content_blocks
                or current_assistant_tool_invocations
                or current_assistant_command_runs
            )
            if not has_content:
                return
            
            # Build flat content from content blocks
            text_parts = []
            for block in current_assistant_content_blocks:
                if block.kind == "text" and block.content.strip():
                    text_parts.append(block.content)
            flat_content = "\n\n".join(text_parts)
            
            messages.append(ChatMessage(
                role="assistant",
                content=flat_content,
                timestamp=current_assistant_timestamp,
                tool_invocations=current_assistant_tool_invocations.copy(),
                command_runs=current_assistant_command_runs.copy(),
                content_blocks=current_assistant_content_blocks.copy(),
            ))
            
            # Reset state
            current_assistant_content_blocks = []
            current_assistant_tool_invocations = []
            current_assistant_command_runs = []
            current_assistant_timestamp = None
        
        def _build_tool_invocation(
            tool_call_id: str, tool_name: str, arguments: dict
        ) -> tuple[ToolInvocation | None, CommandRun | None]:
            """Build a ToolInvocation or CommandRun from tool request data."""
            # Get execution result if available
            execution = tool_executions.get(tool_call_id, {})
            complete_event = execution.get("complete")
            start_event = execution.get("start")
            
            result = None
            status = None
            if complete_event:
                complete_data = complete_event.get("data", {})
                status = "success" if complete_data.get("success") else "error"
                result_obj = complete_data.get("result", {})
                if isinstance(result_obj, dict):
                    result = result_obj.get("content", "")
                else:
                    result = str(result_obj) if result_obj else None
            
            # Get description from start event or arguments
            description = None
            if start_event:
                start_data = start_event.get("data", {})
                start_args = start_data.get("arguments", {})
                description = start_args.get("description")
            if not description:
                description = arguments.get("description")
            
            # Check if this is a shell/powershell command
            if tool_name in ("powershell", "bash", "shell", "run_command"):
                command = arguments.get("command", "")
                return None, CommandRun(
                    command=command,
                    title=description,
                    result=result,
                    status=status,
                    output=result,
                )
            else:
                # Regular tool invocation
                input_str = None
                if arguments:
                    try:
                        input_str = json.dumps(arguments)
                    except (TypeError, ValueError):
                        input_str = str(arguments)
                
                # Build invocation message for inline display
                invocation_message = None
                
                # Generate pretty messages for known tools
                if tool_name == "view":
                    path = arguments.get("path", "")
                    # Shorten path for display
                    short_path = path.split("\\")[-1] if "\\" in path else path.split("/")[-1] if "/" in path else path
                    invocation_message = f"Viewing `{short_path}`"
                
                elif tool_name == "edit":
                    path = arguments.get("path", "")
                    short_path = path.split("\\")[-1] if "\\" in path else path.split("/")[-1] if "/" in path else path
                    invocation_message = f"Edited `{short_path}`"
                
                elif tool_name == "str_replace_editor":
                    cmd = arguments.get("command", "view")
                    path = arguments.get("path", "")
                    short_path = path.split("\\")[-1] if "\\" in path else path.split("/")[-1] if "/" in path else path
                    if cmd == "create":
                        invocation_message = f"Created `{short_path}`"
                    elif cmd == "str_replace":
                        invocation_message = f"Edited `{short_path}`"
                    else:
                        invocation_message = f"Viewing `{short_path}`"
                
                elif tool_name == "grep":
                    pattern = arguments.get("pattern", "")
                    path = arguments.get("path", "")
                    short_path = path.split("\\")[-1] if "\\" in path else path.split("/")[-1] if "/" in path else path
                    invocation_message = f"Searching for `{pattern}` in `{short_path}`"
                
                elif tool_name == "glob":
                    pattern = arguments.get("pattern", "")
                    path = arguments.get("path", "")
                    short_path = path.split("\\")[-1] if "\\" in path else path.split("/")[-1] if "/" in path else path
                    invocation_message = f"Finding `{pattern}` in `{short_path}`"
                
                elif tool_name == "update_todo":
                    invocation_message = "Updated TODO list"
                
                elif description:
                    invocation_message = description
                else:
                    invocation_message = tool_name
                
                return ToolInvocation(
                    name=tool_name,
                    input=input_str,
                    result=result,
                    status=status,
                    invocation_message=invocation_message,
                ), None
        
        def _add_tool_inline(tool_call_id: str, tool_name: str, arguments: dict):
            """Add a tool invocation inline in the current assistant message."""
            # Handle special meta-tools with pretty formatting
            if tool_name == "report_intent":
                # Intent block - shows what the agent is planning
                intent_text = arguments.get("intent", arguments.get("description", ""))
                if intent_text:
                    current_assistant_content_blocks.append(ContentBlock(
                        kind="intent",
                        content=intent_text,
                    ))
                return
            
            if tool_name == "skill":
                # Skill block - shows which skill was loaded
                skill_name = arguments.get("name", arguments.get("skill", ""))
                if skill_name:
                    current_assistant_content_blocks.append(ContentBlock(
                        kind="skill",
                        content=skill_name,
                    ))
                return
            
            # Skip truly internal tools with no user-visible output
            internal_tools = {
                # Terminal output reading - internal helper, not user-facing action
                "read_powershell",
                "read_bash",
            }
            if tool_name in internal_tools:
                return
            
            tool_inv, cmd_run = _build_tool_invocation(tool_call_id, tool_name, arguments)
            
            if cmd_run:
                # Add command run inline as a content block
                cmd_display = cmd_run.title or cmd_run.command
                if len(cmd_display) > 60:
                    cmd_display = cmd_display[:57] + "..."
                current_assistant_content_blocks.append(ContentBlock(
                    kind="toolInvocation",
                    content=f"$ {cmd_run.command}" if cmd_run.command else cmd_display,
                    description=cmd_run.title,
                ))
                current_assistant_command_runs.append(cmd_run)
            
            elif tool_inv:
                # Add tool invocation inline as a content block
                display_text = tool_inv.invocation_message or tool_inv.name
                current_assistant_content_blocks.append(ContentBlock(
                    kind="toolInvocation",
                    content=display_text,
                    description=tool_inv.name,
                ))
                current_assistant_tool_invocations.append(tool_inv)
        
        for event in events:
            event_type = event.get("type", "")
            event_data = event.get("data", {})
            timestamp = event.get("timestamp")
            
            if event_type == "user.message":
                # Flush any pending assistant content before user message
                _flush_pending_assistant_message()
                pending_tool_requests.clear()
                
                content = event_data.get("content", "")
                messages.append(ChatMessage(
                    role="user",
                    content=content,
                    timestamp=timestamp,
                ))
            
            elif event_type == "system.message":
                # Flush pending assistant content
                _flush_pending_assistant_message()
                pending_tool_requests.clear()
                
                content = event_data.get("content", "")
                if content:
                    messages.append(ChatMessage(
                        role="system",
                        content=content,
                        timestamp=timestamp,
                    ))
            
            elif event_type in ("assistant.turn_start", "assistant.turn_end"):
                # Turn boundaries are internal to a single user interaction.
                # Do NOT flush or create separate messages - all assistant turns
                # between user messages should be combined into a single message.
                # Just continue accumulating content.
                pass
            
            elif event_type == "assistant.message":
                # Set timestamp from first assistant message in the sequence
                if current_assistant_timestamp is None:
                    current_assistant_timestamp = timestamp
                
                content = event_data.get("content", "")
                tool_requests = event_data.get("toolRequests", [])
                
                # Add any text content first
                if content and content.strip():
                    current_assistant_content_blocks.append(ContentBlock(
                        kind="text",
                        content=content.strip(),
                    ))
                
                # Store tool requests for processing when execution starts/completes
                for req in tool_requests:
                    tool_call_id = req.get("toolCallId")
                    if tool_call_id:
                        pending_tool_requests[tool_call_id] = req
            
            elif event_type == "tool.execution_start":
                # Add the tool invocation inline when execution starts
                tool_call_id = event_data.get("toolCallId")
                tool_name = event_data.get("toolName", "unknown")
                arguments = event_data.get("arguments", {})
                
                # Use stored request data if available, otherwise use start event data
                req = pending_tool_requests.get(tool_call_id, {})
                if not arguments and req:
                    arguments = req.get("arguments", {})
                if tool_name == "unknown" and req:
                    tool_name = req.get("name", tool_name)
                
                _add_tool_inline(tool_call_id, tool_name, arguments)
            
            elif event_type == "abort":
                # Session or turn was aborted - add as status block
                abort_reason = event_data.get("reason", "unknown")
                current_assistant_content_blocks.append(ContentBlock(
                    kind="status",
                    content=f"Aborted: {abort_reason}",
                    description="abort",
                ))
            
            elif event_type == "session.error":
                # Session encountered an error - add as status block
                error_type = event_data.get("errorType", "unknown")
                error_message = event_data.get("message", "")
                current_assistant_content_blocks.append(ContentBlock(
                    kind="status",
                    content=f"Error: {error_message}" if error_message else f"Error: {error_type}",
                    description="error",
                ))
            
            elif event_type == "session.model_change":
                # Model was changed during session
                new_model = event_data.get("newModel", "unknown")
                current_assistant_content_blocks.append(ContentBlock(
                    kind="status",
                    content=f"Switched to {new_model}",
                    description="model-change",  # hyphenated for CSS class
                ))
        
        # Flush any remaining assistant content
        _flush_pending_assistant_message()
        
        if not messages:
            return None
        
        # Get file metadata for incremental refresh
        source_file_mtime, source_file_size = _get_file_metadata(file_path)
        
        # Get updated_at from last event timestamp
        updated_at = events[-1].get("timestamp") if events else None
        
        return ChatSession(
            session_id=session_id,
            workspace_name=workspace_name,
            workspace_path=workspace_path,
            messages=messages,
            created_at=created_at,
            updated_at=updated_at,
            source_file=str(file_path),
            vscode_edition="cli",  # CLI edition badge
            custom_title=None,
            requester_username=requester_username,
            responder_username=None,
            source_file_mtime=source_file_mtime,
            source_file_size=source_file_size,
            type="cli",
        )
    except (OSError, Exception):
        return None


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
