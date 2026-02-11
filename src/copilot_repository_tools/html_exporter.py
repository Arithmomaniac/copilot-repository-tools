"""HTML exporter for Copilot chat sessions.

Renders chat sessions as self-contained static HTML files using the same
Jinja2 template as the web viewer, but with interactive elements (toolbar,
AJAX, copy buttons) stripped out via the `static=True` flag.
"""

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .markdown_exporter import generate_session_filename as _md_generate_filename
from .scanner import ChatSession

# Threshold to distinguish between seconds and milliseconds timestamps.
_MILLISECONDS_THRESHOLD = 1e12

# Create a reusable markdown converter with extensions
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

# Regex pattern for ANSI escape codes
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


def _markdown_to_html(text: str) -> str:
    """Convert markdown text to HTML using the markdown library."""
    if not text:
        return ""

    text = text.replace("\r\n", "\n")

    def extract_filename_from_file_uri(uri: str) -> str:
        decoded = unquote(uri)
        path = decoded.replace("file:///", "").split("#")[0]
        if "/" in path:
            return path.split("/")[-1]
        if "\\" in path:
            return path.split("\\")[-1]
        return path

    def replace_empty_file_link(match):
        uri = match.group(1)
        filename = extract_filename_from_file_uri(uri)
        return f"`{filename}`"

    text = re.sub(r"\[\]\(file://([^)]+)\)", replace_empty_file_link, text)
    text = re.sub(r'^(Using ["""][^"""]+["""])$', r"_\1_", text, flags=re.MULTILINE)
    text = re.sub(r"_Edited `([^`]+)`_", r"_Edited \1_", text)
    text = re.sub(r"^(Ran terminal command:.*)$", r"_\1_", text, flags=re.MULTILINE)
    text = re.sub(r"^((?:Now )?[Ll]et me [^:]+:)$", r"_\1_", text, flags=re.MULTILINE)
    text = re.sub(r"^(Made changes\.)$", r"_\1_", text, flags=re.MULTILINE)

    _md_converter.reset()
    return Markup(_md_converter.convert(text))  # noqa: S704 - markdown output is intentionally rendered as HTML


def _urldecode(text: str) -> str:
    if not text:
        return ""
    return unquote(text)


def _strip_ansi(text: str | None) -> str:
    if not text:
        return ""
    return _ANSI_ESCAPE_PATTERN.sub("", text)


def _format_timestamp(value: str) -> str:
    if not value:
        return ""
    try:
        epoch_ms = float(value)
        epoch_s = epoch_ms / 1000
        dt = datetime.fromtimestamp(epoch_s)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(value)


def _parse_diff_stats(diff: str) -> dict:
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


def _extract_filename(path: str) -> str:
    if not path:
        return ""
    if "/" in path:
        return path.split("/")[-1]
    if "\\" in path:
        return path.split("\\")[-1]
    return path


def _match_tool_for_block(block_content: str, tools: list, used_indices: set) -> tuple:
    """Match a tool invocation block content to a tool from the list."""
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

    for i, tool in enumerate(tools):
        if i not in used_indices:
            used_indices = used_indices | {i}
            return tool, used_indices

    return None, used_indices


def _get_jinja_env() -> Environment:
    """Create a standalone Jinja2 environment pointing at the web templates."""
    templates_dir = Path(__file__).parent / "web" / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["markdown"] = _markdown_to_html
    env.filters["urldecode"] = _urldecode
    env.filters["format_timestamp"] = _format_timestamp
    env.filters["parse_diff_stats"] = _parse_diff_stats
    env.filters["extract_filename"] = _extract_filename
    env.filters["strip_ansi"] = _strip_ansi
    env.globals["match_tool_for_block"] = _match_tool_for_block
    return env


def _preprocess_messages(session: ChatSession) -> str | None:
    """Pre-process messages to match tool invocations with content blocks.

    Same logic as the webapp session_view route. Returns first user prompt.
    """
    first_user_prompt = None
    for message in session.messages:
        if message.role == "user" and first_user_prompt is None:
            first_user_prompt = message.content

        block_tool_map = {}
        block_cmd_map = {}
        used_tool_indices: set = set()
        used_cmd_indices: set = set()

        for i, block in enumerate(message.content_blocks):
            if block.kind == "toolInvocation":
                if message.command_runs and block.content.startswith("$"):
                    cmd_text = block.content[1:].strip()
                    for j, cmd in enumerate(message.command_runs):
                        if j in used_cmd_indices:
                            continue
                        if cmd.command and (cmd.command.startswith(cmd_text[:30]) or cmd_text[:30] in cmd.command):
                            block_cmd_map[i] = cmd
                            used_cmd_indices.add(j)
                            break

                if i not in block_cmd_map and message.tool_invocations:
                    matched_tool, used_tool_indices = _match_tool_for_block(block.content, message.tool_invocations, used_tool_indices)
                    if matched_tool:
                        block_tool_map[i] = matched_tool

        message._block_tool_map = block_tool_map  # type: ignore[attr-defined]
        message._block_cmd_map = block_cmd_map  # type: ignore[attr-defined]
        message._matched_tool_names = {t.name for t in block_tool_map.values()}  # type: ignore[attr-defined]
        message._matched_cmd_indices = used_cmd_indices  # type: ignore[attr-defined]

    return first_user_prompt


def session_to_html(session: ChatSession) -> str:
    """Convert a chat session to a self-contained static HTML string.

    Args:
        session: The ChatSession to convert.

    Returns:
        Complete HTML document as a string.
    """
    first_user_prompt = _preprocess_messages(session)
    env = _get_jinja_env()
    template = env.get_template("session.html")
    return template.render(
        title=session.custom_title or session.workspace_name or f"Session {session.session_id[:8]}",
        session=session,
        message_count=len(session.messages),
        first_user_prompt=first_user_prompt,
        static=True,
    )


def export_session_to_html_file(
    session: ChatSession,
    output_path: Path | str,
) -> None:
    """Export a single session to a static HTML file.

    Args:
        session: The ChatSession to export.
        output_path: Path to the output HTML file.
    """
    html = session_to_html(session)
    Path(output_path).write_text(html, encoding="utf-8")


def generate_session_html_filename(session: ChatSession) -> str:
    """Generate a filename for a session's HTML export.

    Args:
        session: The ChatSession to generate a filename for.

    Returns:
        A safe filename string with .html extension.
    """
    # Reuse markdown filename logic but swap extension
    md_filename = _md_generate_filename(session)
    return md_filename.rsplit(".", 1)[0] + ".html"
