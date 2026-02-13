"""Content extraction, formatting, and utility helpers for scanner."""

from pathlib import Path

from .models import ContentBlock


def _get_first_truthy_value(*values: str | int | None) -> str | None:
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

    # Kinds that should never be merged - each gets its own block
    standalone_kinds = {"toolInvocation", "status", "ask_user", "intent", "skill"}

    for block in blocks:
        # Handle both 2-tuples and 3-tuples for backward compatibility
        if len(block) == 3:
            kind, content, description = block
        else:
            kind, content = block
            description = None

        # Never merge standalone kinds - each should be separate
        if kind in standalone_kinds:
            # Flush any accumulated content first
            if current_content:
                merged.append(ContentBlock(kind=current_kind or "text", content="".join(current_content), description=current_description))
                current_content = []
                current_kind = None
                current_description = None
            # Add as standalone block
            merged.append(ContentBlock(kind=kind, content=content, description=description))
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
                    merged.append(ContentBlock(kind=current_kind or "text", content="".join(current_content), description=current_description))
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
                    merged.append(ContentBlock(kind=current_kind or "text", content="".join(current_content), description=current_description))
                current_kind = effective_kind
                current_content = [content]
                current_description = None

    # Flush any remaining content
    if current_content:
        merged.append(ContentBlock(kind=current_kind or "text", content="".join(current_content), description=current_description))

    return merged


def _shorten_path(path: str) -> str:
    """Extract the filename from a full file path for display."""
    if not path:
        return path
    if "\\" in path:
        return path.split("\\")[-1]
    if "/" in path:
        return path.split("/")[-1]
    return path


# Tool display format: (template_string, list_of_arg_keys_to_extract)
# Templates use {arg_name} for substitution, with {short_path} auto-shortened
_TOOL_DISPLAY_FORMATS: dict[str, tuple[str, list[str]]] = {
    "view": ("Viewing `{short_path}`", ["path"]),
    "edit": ("Edited `{short_path}`", ["path"]),
    "create": ("Created `{short_path}`", ["path"]),
    "grep": ("Searching for `{pattern}` in `{short_path}`", ["pattern", "path"]),
    "glob": ("Finding `{pattern}` in `{short_path}`", ["pattern", "path"]),
    "web_search": ("\U0001f50d Web search: `{query_short}`", ["query"]),
    "web_fetch": ("\U0001f310 Fetching `{url_short}`", ["url"]),
    "task": ("\U0001f916 Agent ({agent_type}): {description}", ["agent_type", "description"]),
    "update_todo": ("Updated TODO list", []),
    "store_memory": ("\U0001f4be Stored memory: {subject}", ["subject"]),
    "task_complete": ("\u2705 Task complete: {summary}", ["summary"]),
    "sql": ("\U0001f5c4\ufe0f SQL: {description}", ["description"]),
}


def _format_tool_display_message(tool_name: str, arguments: dict, description: str | None = None) -> str:
    """Generate a display message for a tool invocation using data-driven formats."""
    fmt = _TOOL_DISPLAY_FORMATS.get(tool_name)
    if fmt:
        template, arg_keys = fmt
        # Build substitution dict
        subs: dict[str, str] = {}
        for key in arg_keys:
            val = arguments.get(key, "")
            subs[key] = str(val) if val else ""
        # Auto-generate short_path from path arg
        if "path" in arguments:
            subs["short_path"] = _shorten_path(arguments.get("path", ""))
        # Auto-generate truncated versions
        for key in ("query", "url"):
            if key in subs:
                val = subs[key]
                subs[f"{key}_short"] = val[:80] + "..." if len(val) > 80 else val
        try:
            return template.format(**subs)
        except KeyError:
            pass

    # Handle str_replace_editor specially (command-dependent)
    if tool_name == "str_replace_editor":
        cmd = arguments.get("command", "view")
        path = _shorten_path(arguments.get("path", ""))
        if cmd == "create":
            return f"Created `{path}`"
        elif cmd == "str_replace":
            return f"Edited `{path}`"
        return f"Viewing `{path}`"

    # Fallback
    return description or tool_name


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
