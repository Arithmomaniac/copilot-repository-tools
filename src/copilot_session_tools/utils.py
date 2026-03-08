"""Shared utility functions for copilot-session-tools.

Centralizes constants, text processing, timestamp formatting, and
block-metadata matching that were previously duplicated across
html_exporter.py, markdown_exporter.py, webapp.py, and cli.py.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

import markdown
from markupsafe import Markup

from .scanner import ChatSession

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MILLISECONDS_THRESHOLD = 1e12
"""Timestamps above this value are treated as milliseconds."""

DEFAULT_DB_DIR = Path.home() / ".copilot"
"""Directory containing the session-store database."""

DEFAULT_DB_PATH = DEFAULT_DB_DIR / "session-store.db"
"""Default path to the SQLite session-store database."""

# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------


def format_timestamp(value: str | int | float | None, *, unknown_label: str = "") -> str:
    """Format an epoch timestamp to a human-readable UTC date string.

    Handles both seconds and milliseconds (JS-style) timestamps,
    and both string and numeric inputs.

    Args:
        value: Epoch timestamp (seconds or milliseconds), or None.
        unknown_label: Text to return when *value* is falsy.
                       The markdown exporter passes ``"Unknown"``; the
                       HTML/web side passes ``""``.
    """
    if not value:
        return unknown_label
    try:
        numeric = float(value)
        if numeric > MILLISECONDS_THRESHOLD:
            numeric = numeric / 1000
        dt = datetime.fromtimestamp(numeric, tz=UTC)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(value)


# ---------------------------------------------------------------------------
# Text processing helpers
# ---------------------------------------------------------------------------


def urldecode(text: str) -> str:
    """Decode URL-encoded text (e.g., ``c%3A`` → ``c:``)."""
    if not text:
        return ""
    return unquote(text)


# Regex pattern for ANSI escape codes (SGR, cursor control, OSC sequences).
_ANSI_ESCAPE_PATTERN = re.compile(
    r"\x1b"
    r"(?:"
    r"\[[0-9;]*[A-Za-z]"
    r"|"
    r"\][^\x07]*\x07"
    r"|"
    r"\][^\x1b]*\x1b\\"
    r")"
)


def strip_ansi(text: str | None) -> str:
    """Strip ANSI escape codes from terminal output."""
    if not text:
        return ""
    return _ANSI_ESCAPE_PATTERN.sub("", text)


def truncate_preview(text: str, max_chars: int = 80) -> str:
    """Extract first meaningful line and truncate for preview display."""
    if not text:
        return ""
    lines = text.strip().split("\n")
    first_line = next((line.strip() for line in lines if line.strip()), "")
    # Strip HTML tags (before markdown, since markdown strip removes '>')
    first_line = re.sub(r"<[^>]+>", "", first_line).strip()
    # Strip markdown formatting
    first_line = re.sub(r"[#*_`>]", "", first_line).strip()
    if not first_line:
        return ""
    if len(first_line) > max_chars:
        # Try to break at a word boundary
        truncated = first_line[:max_chars]
        space_idx = truncated.rfind(" ")
        if space_idx > max_chars // 2:
            truncated = truncated[:space_idx]
        return truncated + "…"
    return first_line


def extract_filename(path: str | None) -> str:
    """Return the leaf filename from a Unix or Windows path."""
    if not path:
        return ""
    if "/" in path:
        return path.split("/")[-1]
    if "\\" in path:
        return path.split("\\")[-1]
    return path


def parse_diff_stats(diff: str | None) -> dict:
    """Count ``+`` and ``-`` lines in a unified diff.

    Returns:
        ``{"additions": int, "deletions": int}``
    """
    if not diff:
        return {"additions": 0, "deletions": 0}
    additions = 0
    deletions = 0
    for line in diff.split("\n"):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return {"additions": additions, "deletions": deletions}


# ---------------------------------------------------------------------------
# Markdown → HTML conversion
# ---------------------------------------------------------------------------

_md_converter = markdown.Markdown(
    extensions=[
        "tables",
        "fenced_code",
        "sane_lists",
        "smarty",
        "nl2br",
    ],
    extension_configs={
        "smarty": {
            "smart_dashes": True,
            "smart_quotes": True,
        },
    },
)


def _extract_filename_from_file_uri(uri: str) -> str:
    """Extract the leaf filename from a ``file://`` URI."""
    decoded = unquote(uri)
    path = decoded.replace("file:///", "").split("#")[0]
    if "/" in path:
        return path.split("/")[-1]
    if "\\" in path:
        return path.split("\\")[-1]
    return path


def markdown_to_html(text: str) -> str:
    """Convert markdown to HTML, normalizing common VS Code Copilot patterns."""
    if not text:
        return ""

    text = text.replace("\r\n", "\n")

    def replace_empty_file_link(match):
        uri = match.group(1)
        filename = _extract_filename_from_file_uri(uri)
        return f"`{filename}`"

    text = re.sub(r"\[\]\(file://([^)]+)\)", replace_empty_file_link, text)
    text = re.sub(r'^(Using ["""][^"""]+["""])$', r"_\1_", text, flags=re.MULTILINE)
    text = re.sub(r"_Edited `([^`]+)`_", r"_Edited \1_", text)
    text = re.sub(r"^(Ran terminal command:.*)$", r"_\1_", text, flags=re.MULTILINE)
    text = re.sub(r"^((?:Now )?[Ll]et me [^:]+:)$", r"_\1_", text, flags=re.MULTILINE)
    text = re.sub(r"^(Made changes\.)$", r"_\1_", text, flags=re.MULTILINE)

    _md_converter.reset()
    return Markup(_md_converter.convert(text))  # noqa: S704 - markdown output is intentionally rendered as HTML


# ---------------------------------------------------------------------------
# Block ↔ tool/command matching (used by HTML exporter + webapp)
# ---------------------------------------------------------------------------


def match_tool_for_block(block_content: str, tools: list, used_indices: set) -> tuple:
    """Match a tool-invocation block's content to a tool from *tools*.

    Returns ``(matched_tool | None, updated_used_indices)``.
    """
    if not tools:
        return None, used_indices

    match = re.search(r"`([^`]+)`", block_content)
    short_name = match.group(1) if match else None

    if not short_name:
        match = re.search(r"Running\s+(\S+)", block_content)
        short_name = match.group(1) if match else None

    if short_name:
        for i, tool in enumerate(tools):
            if i in used_indices:
                continue
            if short_name.lower() in tool.name.lower() or tool.name.lower().endswith(short_name.lower()):
                used_indices = used_indices | {i}
                return tool, used_indices

    # Fallback: sequential match, skipping 'task' (renders as subagent block)
    for i, tool in enumerate(tools):
        if i not in used_indices and tool.name != "task":
            used_indices = used_indices | {i}
            return tool, used_indices

    return None, used_indices


def build_block_metadata(content_blocks, tool_invocations, command_runs):
    """Build tool/command matching metadata for a set of content blocks.

    Recurses into subagent blocks so the template can render nested tools.

    Returns:
        Dict with ``block_tool_map``, ``block_cmd_map``,
        ``matched_tool_names``, ``matched_cmd_indices``.
    """
    block_tool_map: dict = {}
    block_cmd_map: dict = {}
    used_tool_indices: set = set()
    used_cmd_indices: set = set()

    for i, block in enumerate(content_blocks):
        if block.kind == "toolInvocation":
            if command_runs and block.content.startswith("$"):
                cmd_text = block.content[1:].strip()
                for j, cmd in enumerate(command_runs):
                    if j in used_cmd_indices:
                        continue
                    if cmd.command and (cmd.command.startswith(cmd_text[:30]) or cmd_text[:30] in cmd.command):
                        block_cmd_map[i] = cmd
                        used_cmd_indices.add(j)
                        break

            if i not in block_cmd_map and tool_invocations:
                matched_tool, used_tool_indices = match_tool_for_block(block.content, tool_invocations, used_tool_indices)
                if matched_tool:
                    block_tool_map[i] = matched_tool

        # Recursively build metadata for nested subagent blocks
        if block.kind in ("subagent", "subagent_failed", "subagent_incomplete") and block.content_blocks:
            block._nested_meta = build_block_metadata(block.content_blocks, block.tool_invocations, block.command_runs)

    return {
        "block_tool_map": block_tool_map,
        "block_cmd_map": block_cmd_map,
        "matched_tool_names": {t.name for t in block_tool_map.values()},
        "matched_cmd_indices": used_cmd_indices,
    }


# ---------------------------------------------------------------------------
# Session filename generation
# ---------------------------------------------------------------------------


def sanitize_filename(name: str, max_length: int = 50) -> str:
    """Replace non-alphanumeric characters with underscores and truncate."""
    safe_name = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in name)
    return safe_name[:max_length]


def generate_session_filename(session: ChatSession, extension: str = "md") -> str:
    """Generate a filesystem-safe filename for a session export.

    Args:
        session: The session to name.
        extension: File extension (without dot), e.g. ``"md"`` or ``"html"``.
    """
    if session.custom_title:
        name = session.custom_title
    elif session.workspace_name:
        name = session.workspace_name
    else:
        name = session.session_id[:16]

    date_str = ""
    if session.created_at:
        try:
            ts = session.created_at
            if isinstance(ts, str):
                ts = float(ts)
            if ts > MILLISECONDS_THRESHOLD:
                ts = ts / 1000
            date_str = datetime.fromtimestamp(ts, tz=UTC).strftime("%Y%m%d")
        except (ValueError, TypeError, OSError):
            pass

    safe_name = sanitize_filename(name)
    if date_str:
        return f"{date_str}_{safe_name}_{session.session_id[:8]}.{extension}"
    return f"{safe_name}_{session.session_id[:8]}.{extension}"
