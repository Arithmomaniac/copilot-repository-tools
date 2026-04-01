"""Schema constants and column mappings for the cst_* database tables.

Centralizes column names so that renames, additions, or reorderings
only need to change one file.  Both the write path (db_storage.py)
and read path (db_retrieval.py) reference these constants.
"""

from __future__ import annotations

import sqlite3

from .scanner import ChatSession, CommandRun, FileChange, ToolInvocation

# ---------------------------------------------------------------------------
# Column tuples used in INSERT statements (order matters for parameterized queries)
# ---------------------------------------------------------------------------

CST_SESSION_COLUMNS = (
    "session_id",
    "workspace_name",
    "workspace_path",
    "created_at",
    "updated_at",
    "source_file",
    "vscode_edition",
    "custom_title",
    "requester_username",
    "responder_username",
    "source_file_mtime",
    "source_file_size",
    "type",
    "repository_url",
    "parser_version",
    "source_format",
    "enrichment_version",
)

CST_MESSAGE_COLUMNS = (
    "session_id",
    "message_index",
    "role",
    "content",
    "timestamp",
    "cached_markdown",
    "agent_id",
    "agent_display_name",
    "agent_nesting_level",
    "parent_message_id",
    "child_index",
)

CST_TOOL_INVOCATION_COLUMNS = (
    "message_id",
    "name",
    "input",
    "result",
    "status",
    "start_time",
    "end_time",
    "source_type",
    "invocation_message",
    "subagent_invocation_id",
    "is_agent_backlink",
    "backlink_agent_id",
)

CST_FILE_CHANGE_COLUMNS = (
    "message_id",
    "path",
    "diff",
    "content",
    "explanation",
    "language_id",
)

CST_COMMAND_RUN_COLUMNS = (
    "message_id",
    "command",
    "title",
    "result",
    "status",
    "output",
    "timestamp",
)

CST_CONTENT_BLOCK_COLUMNS = (
    "message_id",
    "block_index",
    "kind",
    "content",
    "description",
    "child_message_id",
    "prompt",
    "is_background",
    "agent_id",
)

# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


def insert_sql(table: str, columns: tuple[str, ...]) -> str:
    """Generate a parameterized INSERT statement from a table name and column tuple."""
    cols = ", ".join(columns)
    placeholders = ", ".join("?" * len(columns))
    return f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"  # noqa: S608


# ---------------------------------------------------------------------------
# Dataclass → row helpers  (write path)
# ---------------------------------------------------------------------------


def session_to_row(
    session: ChatSession,
    *,
    enrichment_version: str | None = None,
    updated_at_fallback: str | None = None,
) -> tuple:
    """Map a ChatSession to a parameter tuple matching CST_SESSION_COLUMNS.

    *updated_at_fallback* is used when ``session.updated_at`` is falsy
    (e.g. the enrichment path fills it with a timestamp).
    """
    return (
        session.session_id,
        session.workspace_name,
        session.workspace_path,
        session.created_at,
        session.updated_at or updated_at_fallback,
        session.source_file,
        session.vscode_edition,
        session.custom_title,
        session.requester_username,
        session.responder_username,
        session.source_file_mtime,
        session.source_file_size,
        session.type,
        session.repository_url,
        session.parser_version,
        session.source_format,
        enrichment_version,
    )


def tool_to_row(message_id: int, tool: ToolInvocation) -> tuple:
    """Map a ToolInvocation to a parameter tuple matching CST_TOOL_INVOCATION_COLUMNS."""
    return (
        message_id,
        tool.name,
        tool.input,
        tool.result,
        tool.status,
        tool.start_time,
        tool.end_time,
        tool.source_type,
        tool.invocation_message,
        tool.subagent_invocation_id,
        tool.is_agent_backlink,
        tool.backlink_agent_id,
    )


def file_change_to_row(message_id: int, change: FileChange) -> tuple:
    """Map a FileChange to a parameter tuple matching CST_FILE_CHANGE_COLUMNS."""
    return (
        message_id,
        change.path,
        change.diff,
        change.content,
        change.explanation,
        change.language_id,
    )


def command_to_row(message_id: int, cmd: CommandRun) -> tuple:
    """Map a CommandRun to a parameter tuple matching CST_COMMAND_RUN_COLUMNS."""
    return (
        message_id,
        cmd.command,
        cmd.title,
        cmd.result,
        cmd.status,
        cmd.output,
        cmd.timestamp,
    )


# ---------------------------------------------------------------------------
# Row → dataclass helpers  (read path)
# ---------------------------------------------------------------------------


def row_to_tool(row: sqlite3.Row) -> ToolInvocation:
    """Map a cst_tool_invocations row to a ToolInvocation."""
    keys = row.keys()
    return ToolInvocation(
        name=row["name"],
        input=row["input"],
        result=row["result"],
        status=row["status"],
        start_time=row["start_time"],
        end_time=row["end_time"],
        source_type=row["source_type"] if "source_type" in keys else None,
        invocation_message=row["invocation_message"] if "invocation_message" in keys else None,
        subagent_invocation_id=row["subagent_invocation_id"] if "subagent_invocation_id" in keys else None,
        is_agent_backlink=bool(row["is_agent_backlink"]) if "is_agent_backlink" in keys and row["is_agent_backlink"] else False,
        backlink_agent_id=row["backlink_agent_id"] if "backlink_agent_id" in keys else None,
    )


def row_to_file_change(row: sqlite3.Row) -> FileChange:
    """Map a cst_file_changes row to a FileChange."""
    return FileChange(
        path=row["path"],
        diff=row["diff"],
        content=row["content"],
        explanation=row["explanation"],
        language_id=row["language_id"],
    )


def row_to_command(row: sqlite3.Row) -> CommandRun:
    """Map a cst_command_runs row to a CommandRun."""
    return CommandRun(
        command=row["command"],
        title=row["title"],
        result=row["result"],
        status=row["status"],
        output=row["output"],
        timestamp=row["timestamp"],
    )
