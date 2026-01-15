"""Markdown exporter for Copilot chat sessions.

Exports chat sessions to markdown format with:
- Header block with metadata (session ID, workspace, dates)
- Messages separated by horizontal rules
- Message numbers and roles as bold headers
- Tool call summaries in italics
- Thinking block notices in italics (without the full content)
"""

from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from .scanner import ChatMessage, ChatSession

# Threshold to distinguish between seconds and milliseconds timestamps.
# Timestamps above this value (approximately year 2001 in milliseconds) are
# treated as milliseconds and divided by 1000 to convert to seconds.
_MILLISECONDS_THRESHOLD = 1e12


def _format_timestamp(value: str | int | None) -> str:
    """Format an epoch timestamp (milliseconds) to a human-readable date string."""
    if not value:
        return "Unknown"
    try:
        # Handle both string and numeric values - float() accepts both
        numeric_value = float(value)
        # Check if milliseconds (common for JS timestamps)
        if numeric_value > _MILLISECONDS_THRESHOLD:
            numeric_value = numeric_value / 1000
        dt = datetime.fromtimestamp(numeric_value)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        # If parsing fails, return original value as string
        return str(value)


def _urldecode(text: str) -> str:
    """Decode URL-encoded text (e.g., 'c%3A' -> 'c:')."""
    if not text:
        return ""
    return unquote(text)


def _format_tool_summary(message: ChatMessage, include_inputs: bool = False) -> str:
    """Format tool invocations as an italicized summary line.
    
    Args:
        message: The message containing tool invocations.
        include_inputs: If True, include tool inputs as code blocks.
    """
    if not message.tool_invocations:
        return ""
    
    tool_names = [tool.name for tool in message.tool_invocations]
    count = len(tool_names)
    
    if count == 1:
        summary = f"\n\n*Used tool: {tool_names[0]}*"
    elif count <= 3:
        summary = f"\n\n*Used tools: {', '.join(tool_names)}*"
    else:
        summary = f"\n\n*Used {count} tools: {', '.join(tool_names[:3])}, ...*"
    
    if include_inputs:
        for tool in message.tool_invocations:
            if tool.input:
                summary += f"\n\n**{tool.name} input:**\n```\n{tool.input}\n```"
    
    return summary


def _format_file_changes_summary(message: ChatMessage, include_diffs: bool = False) -> str:
    """Format file changes as an italicized summary line.
    
    Args:
        message: The message containing file changes.
        include_diffs: If True, include file diffs as code blocks.
    """
    if not message.file_changes:
        return ""
    
    paths = [change.path for change in message.file_changes]
    count = len(paths)
    
    if count == 1:
        summary = f"\n\n*Changed file: {paths[0]}*"
    elif count <= 3:
        summary = f"\n\n*Changed files: {', '.join(paths)}*"
    else:
        summary = f"\n\n*Changed {count} files: {', '.join(paths[:3])}, ...*"
    
    if include_diffs:
        for change in message.file_changes:
            if change.diff:
                summary += f"\n\n**{change.path}:**\n```diff\n{change.diff}\n```"
    
    return summary


def _format_command_runs_summary(message: ChatMessage) -> str:
    """Format command runs as an italicized summary line."""
    if not message.command_runs:
        return ""
    
    commands = [cmd.command for cmd in message.command_runs]
    count = len(commands)
    
    if count == 1:
        cmd_display = commands[0][:50] + "..." if len(commands[0]) > 50 else commands[0]
        return f"\n\n*Ran command: `{cmd_display}`*"
    else:
        return f"\n\n*Ran {count} commands*"


def _had_thinking_content(message: ChatMessage) -> bool:
    """Check if the message had any thinking blocks."""
    if not message.content_blocks:
        return False
    return any(block.kind == "thinking" for block in message.content_blocks)


def _has_inline_tool_blocks(message: ChatMessage) -> bool:
    """Check if the message has inline tool invocation blocks.
    
    When tools are rendered inline (VSCode-style), we don't need to add
    a separate tool summary at the end of the message.
    """
    if not message.content_blocks:
        return False
    return any(block.kind == "toolInvocation" for block in message.content_blocks)


def _format_message_content(message: ChatMessage, include_thinking: bool = False) -> str:
    """Format message content, optionally including thinking blocks.
    
    Args:
        message: The ChatMessage to format.
        include_thinking: If True, include thinking block content. If False, show
                         a notice that thinking occurred but omit the content.
    
    Returns:
        Formatted message content as a string.
    """
    parts = []
    
    # Check if there was thinking content
    had_thinking = _had_thinking_content(message)
    
    if message.content_blocks:
        # Use structured content blocks
        for block in message.content_blocks:
            if block.kind == "thinking":
                if include_thinking:
                    # Include the actual thinking content in a blockquote
                    parts.append(f"> **Thinking:**\n> {block.content.replace(chr(10), chr(10) + '> ')}")
                # If not including, we'll add a notice at the start
                continue
            elif block.kind == "toolInvocation":
                # Italicize tool invocation messages (only if non-empty)
                if block.content.strip():
                    parts.append(f"*{block.content.strip()}*")
            else:
                # Only add non-empty text blocks
                if block.content.strip():
                    parts.append(block.content)
    else:
        # Fall back to flat content
        if message.content.strip():
            parts.append(message.content)
    
    content = "\n\n".join(parts)
    
    # Post-process to normalize formatting patterns
    import re
    
    # "*Creating [](file://...)*" -> "*Creating filename*" (extract leaf name, keep italics, remove link)
    content = re.sub(
        r'\*Creating \[\]\(file://[^)]+/([^/)]+)\)\*',
        r'*Creating \1*',
        content
    )
    
    # "*Reading [](file://...)*" -> "*Reading filename*" (extract leaf name, keep italics, remove link)
    content = re.sub(
        r'\*Reading \[\]\(file://[^)]+/([^/)]+)\)\*',
        r'*Reading \1*',
        content
    )
    
    # "*Edited `filename`*" -> "*Edited filename*" (remove backticks within italics)
    content = re.sub(
        r'\*Edited `([^`]+)`\*',
        r'*Edited \1*',
        content
    )
    
    # Add thinking notice if there was thinking content (and not already included)
    if had_thinking and not include_thinking:
        content = "*[Was thinking...]*\n\n" + content
    
    return content


def session_to_markdown(
    session: ChatSession,
    include_diffs: bool = False,
    include_tool_inputs: bool = False,
    include_thinking: bool = False,
) -> str:
    """Convert a chat session to markdown format.
    
    Args:
        session: The ChatSession to convert.
        include_diffs: If True, include file diffs as code blocks.
        include_tool_inputs: If True, include tool inputs as code blocks.
        include_thinking: If True, include thinking block content. If False (default),
                         show a notice that thinking occurred but omit the content.
        
    Returns:
        Markdown string representation of the session.
    """
    lines = []
    
    # Header block with metadata
    lines.append("# Chat Session")
    lines.append("")
    
    # Session title/name
    if session.custom_title:
        lines.append(f"**Title:** {session.custom_title}")
    elif session.workspace_name:
        lines.append(f"**Workspace:** {session.workspace_name}")
    else:
        lines.append(f"**Session:** {session.session_id[:8]}...")
    
    lines.append("")
    
    # Metadata in a clear format
    lines.append("## Metadata")
    lines.append("")
    lines.append(f"- **Session ID:** `{session.session_id}`")
    
    if session.workspace_name:
        lines.append(f"- **Workspace:** {session.workspace_name}")
    
    if session.workspace_path:
        decoded_path = _urldecode(session.workspace_path)
        lines.append(f"- **Path:** `{decoded_path}`")
    
    if session.created_at:
        lines.append(f"- **Created:** {_format_timestamp(session.created_at)}")
    
    if session.updated_at:
        lines.append(f"- **Updated:** {_format_timestamp(session.updated_at)}")
    
    lines.append(f"- **Edition:** `{session.vscode_edition}`")
    lines.append(f"- **Messages:** {len(session.messages)}")
    
    if session.requester_username:
        lines.append(f"- **User:** {session.requester_username}")
    
    if session.responder_username:
        lines.append(f"- **Assistant:** {session.responder_username}")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # Messages
    for i, message in enumerate(session.messages, 1):
        msg_md = message_to_markdown(
            message,
            message_number=i,
            include_diffs=include_diffs,
            include_tool_inputs=include_tool_inputs,
            include_thinking=include_thinking,
        )
        lines.append(msg_md)
    
    return "\n".join(lines)


def message_to_markdown(
    message: ChatMessage,
    message_number: int = 0,
    include_diffs: bool = False,
    include_tool_inputs: bool = False,
    include_thinking: bool = False,
) -> str:
    """Convert a single message to markdown format.
    
    Args:
        message: The ChatMessage to convert.
        message_number: The 1-based message number (0 means don't include header).
        include_diffs: If True, include file diffs as code blocks.
        include_tool_inputs: If True, include tool inputs as code blocks.
        include_thinking: If True, include thinking block content.
        
    Returns:
        Markdown string representation of the message.
    """
    lines = []
    
    # Message header: number and role (if message_number > 0)
    if message_number > 0:
        role_display = message.role.upper()
        lines.append(f"## Message {message_number}: **{role_display}**")
        lines.append("")
    
    # Timestamp if available
    if message.timestamp:
        lines.append(f"*{_format_timestamp(message.timestamp)}*")
        lines.append("")
    
    # Content (optionally including thinking blocks)
    content = _format_message_content(message, include_thinking=include_thinking)
    lines.append(content)
    
    # Check if tools are rendered inline via content blocks
    # If so, skip the separate tool/command summaries to avoid duplication
    has_inline_tools = _has_inline_tool_blocks(message)
    
    if not has_inline_tools:
        # Tool invocations summary (in italics, with optional inputs)
        tool_summary = _format_tool_summary(message, include_inputs=include_tool_inputs)
        if tool_summary:
            lines.append(tool_summary)
        
        # Command runs summary (in italics)
        cmd_summary = _format_command_runs_summary(message)
        if cmd_summary:
            lines.append(cmd_summary)
    
    # File changes summary (in italics, with optional diffs) - always include
    file_summary = _format_file_changes_summary(message, include_diffs=include_diffs)
    if file_summary:
        lines.append(file_summary)
    
    lines.append("")
    lines.append("---")
    lines.append("")
    
    return "\n".join(lines)


def export_session_to_file(
    session: ChatSession,
    output_path: Path | str,
    include_diffs: bool = False,
    include_tool_inputs: bool = False,
    include_thinking: bool = False,
) -> None:
    """Export a single session to a markdown file.
    
    Args:
        session: The ChatSession to export.
        output_path: Path to the output markdown file.
        include_diffs: If True, include file diffs as code blocks.
        include_tool_inputs: If True, include tool inputs as code blocks.
        include_thinking: If True, include thinking block content in the export.
    """
    markdown = session_to_markdown(
        session,
        include_diffs=include_diffs,
        include_tool_inputs=include_tool_inputs,
        include_thinking=include_thinking,
    )
    Path(output_path).write_text(markdown, encoding="utf-8")


def _sanitize_filename(name: str, max_length: int = 50) -> str:
    """Sanitize a string to be safe for use as a filename.
    
    Replaces any characters that are not alphanumeric, hyphen, underscore,
    or period with underscores. Also limits the length.
    
    Args:
        name: The string to sanitize.
        max_length: Maximum length of the resulting string.
        
    Returns:
        A filesystem-safe string.
    """
    safe_name = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in name)
    return safe_name[:max_length]


def generate_session_filename(session: ChatSession) -> str:
    """Generate a filename for a session's markdown export.
    
    Args:
        session: The ChatSession to generate a filename for.
        
    Returns:
        A safe filename string.
    """
    # Use custom title, workspace name, or session ID prefix
    if session.custom_title:
        name = session.custom_title
    elif session.workspace_name:
        name = session.workspace_name
    else:
        name = session.session_id[:16]
    
    # Add date if available
    date_str = ""
    if session.created_at:
        try:
            ts = session.created_at
            if isinstance(ts, str):
                ts = float(ts)
            if ts > _MILLISECONDS_THRESHOLD:
                ts = ts / 1000
            date_str = datetime.fromtimestamp(ts).strftime("%Y%m%d")
        except (ValueError, TypeError, OSError):
            pass
    
    # Create safe filename
    safe_name = _sanitize_filename(name)
    
    if date_str:
        filename = f"{date_str}_{safe_name}_{session.session_id[:8]}.md"
    else:
        filename = f"{safe_name}_{session.session_id[:8]}.md"
    
    return filename
