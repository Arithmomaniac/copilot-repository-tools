"""Diff generation and text edit parsing for scanner."""

import difflib
from pathlib import Path

from .content import _extract_uri_filename, _extract_uri_path
from .models import FileChange


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
    all_edits.sort(key=lambda e: (e.get("range", {}).get("startLineNumber", 0), e.get("range", {}).get("startColumn", 0)), reverse=True)

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
                modified_lines[start_line : end_line + 1] = new_lines

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


def _format_edits_as_diff(edits: list[list[dict]], original_content: str | None = None, filename: str = "file") -> str | None:
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

    # Fallback: Collect all edits and consolidate them
    all_edits = []
    for edit_batch in edits:
        if not isinstance(edit_batch, list):
            continue
        for edit in edit_batch:
            if not isinstance(edit, dict):
                continue
            edit_range = edit.get("range", {})
            new_text = edit.get("text", "")
            if new_text:  # Only include non-empty edits
                start_line = edit_range.get("startLineNumber", 1)
                all_edits.append((start_line, new_text))

    if not all_edits:
        return None

    # Sort by line number
    all_edits.sort(key=lambda x: x[0])

    # Check if this looks like a new file creation (consecutive single-line inserts)
    # This happens when VS Code streams content line by line
    is_new_file = len(all_edits) > 5 and all(all_edits[i][0] == all_edits[i - 1][0] + 1 for i in range(1, min(10, len(all_edits))))

    if is_new_file:
        # Combine all edits into a single block for new file
        combined_text = ""
        for _, text in all_edits:
            combined_text += text
        # Show as a single addition block
        diff_lines = ["@@ New file @@"]
        for line in combined_text.split("\n"):
            diff_lines.append(f"+ {line}")
        return "\n".join(diff_lines).rstrip()

    # Group edits by proximity (within 3 lines of each other)
    groups = []
    current_group = [all_edits[0]]

    for i in range(1, len(all_edits)):
        prev_line = current_group[-1][0]
        curr_line = all_edits[i][0]
        # If within 3 lines, add to current group
        if curr_line - prev_line <= 3:
            current_group.append(all_edits[i])
        else:
            groups.append(current_group)
            current_group = [all_edits[i]]
    groups.append(current_group)

    # Format each group as a hunk
    diff_lines = []
    for group in groups:
        start_line = group[0][0]
        end_line = group[-1][0]

        if start_line == end_line:
            diff_lines.append(f"@@ Line {start_line} @@")
        else:
            diff_lines.append(f"@@ Lines {start_line}-{end_line} @@")

        for _, text in group:
            for line in text.split("\n"):
                diff_lines.append(f"+ {line}")

        diff_lines.append("")  # Empty line between hunks

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
