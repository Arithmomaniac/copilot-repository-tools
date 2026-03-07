"""HTML exporter for Copilot chat sessions.

Renders chat sessions as self-contained static HTML files using the same
Jinja2 template as the web viewer, but with interactive elements (toolbar,
AJAX, copy buttons) stripped out via the `static=True` flag.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .scanner import ChatSession
from .utils import (
    build_block_metadata,
    extract_filename,
    format_timestamp,
    markdown_to_html,
    match_tool_for_block,
    parse_diff_stats,
    strip_ansi,
    urldecode,
)
from .utils import (
    generate_session_filename as _generate_filename,
)


def _get_jinja_env() -> Environment:
    """Create a standalone Jinja2 environment pointing at the web templates."""
    templates_dir = Path(__file__).parent / "web" / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["markdown"] = markdown_to_html
    env.filters["urldecode"] = urldecode
    env.filters["format_timestamp"] = format_timestamp
    env.filters["parse_diff_stats"] = parse_diff_stats
    env.filters["extract_filename"] = extract_filename
    env.filters["strip_ansi"] = strip_ansi
    env.globals["match_tool_for_block"] = match_tool_for_block
    env.globals["get_nested_meta"] = lambda block: getattr(block, "_nested_meta", {})
    return env


def _preprocess_messages(session: ChatSession) -> tuple[str | None, dict[int, dict]]:
    """Pre-process messages to match tool invocations with content blocks.

    Same logic as the webapp session_view route. Returns first user prompt
    and a dict mapping message index to its block metadata.
    """
    first_user_prompt = None
    message_metadata: dict[int, dict] = {}
    for msg_idx, message in enumerate(session.messages):
        if message.role == "user" and first_user_prompt is None:
            first_user_prompt = message.content

        message_metadata[msg_idx] = build_block_metadata(message.content_blocks, message.tool_invocations, message.command_runs)

    return first_user_prompt, message_metadata


def session_to_html(session: ChatSession, include_agent_details: bool = True) -> str:
    """Convert a chat session to a self-contained static HTML string.

    Args:
        session: The ChatSession to convert.
        include_agent_details: If True (default), render full agent content.
                              If False, show only a summary line per agent.

    Returns:
        Complete HTML document as a string.
    """
    first_user_prompt, message_metadata = _preprocess_messages(session)
    env = _get_jinja_env()
    template = env.get_template("session.html")
    return template.render(
        title=session.custom_title or session.workspace_name or f"Session {session.session_id[:8]}",
        session=session,
        message_count=len(session.messages),
        first_user_prompt=first_user_prompt,
        message_metadata=message_metadata,
        static=True,
        is_enriched=True,
        include_agent_details=include_agent_details,
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
    return _generate_filename(session, extension="html")
