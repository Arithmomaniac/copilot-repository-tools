"""Retrieval module for reading and reconstructing Copilot chat sessions.

Extracted from database.py to isolate read-path concerns:
- Session reconstruction from relational tables
- Session listing (cst_* + built-in deduplication)
- Message markdown rendering
- Analytics (stats, workspaces, repositories)
- Built-in table accessors
- FTS index optimization and JSON export
"""

from __future__ import annotations

import json
import sqlite3

from .db_schema import row_to_command, row_to_file_change, row_to_tool
from .markdown_exporter import message_to_markdown
from .scanner import (
    ChatMessage,
    ChatSession,
    CommandRun,
    ContentBlock,
    FileChange,
    ToolInvocation,
)


def _deserialize_content_block(row: sqlite3.Row) -> ContentBlock:
    """Deserialize a content block row, including nested_data for subagent blocks."""
    nested_data_str = row["nested_data"] if "nested_data" in row.keys() else None  # noqa: SIM118
    block = ContentBlock(
        kind=row["kind"],
        content=row["content"],
        description=row["description"] if "description" in row.keys() else None,  # noqa: SIM118
    )
    if nested_data_str:
        data = json.loads(nested_data_str)
        if "content_blocks" in data:
            block.content_blocks = [
                ContentBlock(
                    kind=cb.get("kind", "text"),
                    content=cb.get("content", ""),
                    description=cb.get("description"),
                )
                for cb in data["content_blocks"]
            ]
        if "tool_invocations" in data:
            block.tool_invocations = [ToolInvocation(**t) for t in data["tool_invocations"]]
        if "file_changes" in data:
            block.file_changes = [FileChange(**f) for f in data["file_changes"]]
        if "command_runs" in data:
            block.command_runs = [CommandRun(**c) for c in data["command_runs"]]
    return block


def reconstruct_message(cursor: sqlite3.Cursor, message_id: int, msg_row: sqlite3.Row) -> ChatMessage:
    """Reconstruct a ChatMessage from database rows by querying related tables.

    Args:
        cursor: SQLite cursor for querying related tables.
        message_id: The cst_messages.id of the message.
        msg_row: The cst_messages row for this message.

    Returns:
        Fully hydrated ChatMessage with tool invocations, file changes,
        command runs, and content blocks.
    """
    # Query tool_invocations for this message
    cursor.execute("SELECT * FROM cst_tool_invocations WHERE message_id = ?", (message_id,))
    tool_invocations = [row_to_tool(t) for t in cursor.fetchall()]

    # Query file_changes
    cursor.execute("SELECT * FROM cst_file_changes WHERE message_id = ?", (message_id,))
    file_changes = [row_to_file_change(f) for f in cursor.fetchall()]

    # Query command_runs
    cursor.execute("SELECT * FROM cst_command_runs WHERE message_id = ?", (message_id,))
    command_runs = [row_to_command(c) for c in cursor.fetchall()]

    # Query content_blocks
    cursor.execute("SELECT * FROM cst_content_blocks WHERE message_id = ? ORDER BY block_index", (message_id,))
    content_blocks = [_deserialize_content_block(b) for b in cursor.fetchall()]

    # Get cached_markdown safely
    cached_md = msg_row["cached_markdown"] if "cached_markdown" in msg_row.keys() else None  # noqa: SIM118

    return ChatMessage(
        role=msg_row["role"],
        content=msg_row["content"],
        timestamp=msg_row["timestamp"],
        tool_invocations=tool_invocations,
        file_changes=file_changes,
        command_runs=command_runs,
        content_blocks=content_blocks,
        cached_markdown=cached_md,
        agent_id=msg_row["agent_id"] if "agent_id" in msg_row.keys() else None,  # noqa: SIM118
        agent_display_name=msg_row["agent_display_name"] if "agent_display_name" in msg_row.keys() else None,  # noqa: SIM118
        agent_nesting_level=msg_row["agent_nesting_level"] if "agent_nesting_level" in msg_row.keys() else 0,  # noqa: SIM118
        original_content=msg_row["original_content"] if "original_content" in msg_row.keys() else None,  # noqa: SIM118
        cleanup_model=msg_row["cleanup_model"] if "cleanup_model" in msg_row.keys() else None,  # noqa: SIM118
    )


def get_cst_session(conn: sqlite3.Connection, session_id: str) -> ChatSession | None:
    """Get a session from cst_* tables by its ID.

    Args:
        conn: SQLite connection.
        session_id: The session ID to look up.

    Returns:
        ChatSession if found, None otherwise.
    """
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM cst_sessions WHERE session_id = ?", (session_id,))
    row = cursor.fetchone()
    if not row:
        return None

    # Get messages with their IDs for fetching related data
    cursor.execute(
        """
        SELECT id, role, content, timestamp, cached_markdown,
               agent_id, agent_display_name, agent_nesting_level,
               original_content, cleanup_model
        FROM cst_messages
        WHERE session_id = ?
        ORDER BY message_index
        """,
        (session_id,),
    )
    message_rows = cursor.fetchall()

    messages = []
    for msg_row in message_rows:
        message_id = msg_row["id"]
        messages.append(reconstruct_message(cursor, message_id, msg_row))

    # Helper to safely get optional fields from sqlite3.Row
    def safe_get(key):
        try:
            return row[key]
        except (IndexError, KeyError):
            return None

    return ChatSession(
        session_id=row["session_id"],
        workspace_name=row["workspace_name"],
        workspace_path=row["workspace_path"],
        messages=messages,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        source_file=row["source_file"],
        vscode_edition=row["vscode_edition"],
        custom_title=safe_get("custom_title"),
        requester_username=safe_get("requester_username"),
        responder_username=safe_get("responder_username"),
        source_file_mtime=safe_get("source_file_mtime"),
        source_file_size=safe_get("source_file_size"),
        type=safe_get("type") or "vscode",
        repository_url=safe_get("repository_url"),
    )


def get_builtin_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Read a session from the built-in sessions table.

    Args:
        conn: SQLite connection.
        session_id: The session ID to look up.

    Returns:
        Dict with session fields, or None.
    """
    try:
        row = conn.execute(
            "SELECT id, cwd, repository, branch, summary, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def get_builtin_turns(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Read turns from the built-in turns table for a session.

    Args:
        conn: SQLite connection.
        session_id: The session ID.

    Returns:
        List of turn dicts.
    """
    try:
        rows = conn.execute(
            "SELECT turn_index, user_message, assistant_response FROM turns WHERE session_id = ? ORDER BY turn_index",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_builtin_checkpoints(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Read checkpoints from the built-in checkpoints table."""
    try:
        rows = conn.execute(
            "SELECT checkpoint_number, title, overview, history, work_done, technical_details, "
            "important_files, next_steps FROM checkpoints WHERE session_id = ? ORDER BY checkpoint_number",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_builtin_files(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Read file references from the built-in session_files table."""
    try:
        rows = conn.execute(
            "SELECT file_path, tool_name, turn_index, first_seen_at FROM session_files WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_builtin_refs(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Read refs from the built-in session_refs table."""
    try:
        rows = conn.execute(
            "SELECT ref_type, ref_value, turn_index, created_at FROM session_refs WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def list_builtin_sessions(conn: sqlite3.Connection, limit: int = 100, offset: int = 0) -> list[dict]:
    """List sessions from the built-in sessions table."""
    try:
        rows = conn.execute(
            "SELECT id, cwd, repository, branch, summary, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def count_builtin_turns(conn: sqlite3.Connection, session_id: str) -> int:
    """Count turns for a session in the built-in turns table."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def get_builtin_session_as_chat_session(conn: sqlite3.Connection, session_id: str) -> ChatSession | None:
    """Convert built-in session/turns data to a ChatSession.

    Args:
        conn: SQLite connection.
        session_id: The session ID to look up in built-in tables.

    Returns:
        ChatSession if found, None otherwise.
    """
    session_data = get_builtin_session(conn, session_id)
    if not session_data:
        return None

    turns = get_builtin_turns(conn, session_id)
    messages: list[ChatMessage] = []
    for turn in turns:
        user_msg = turn.get("user_message")
        if user_msg:
            messages.append(ChatMessage(role="user", content=user_msg))
        assistant_msg = turn.get("assistant_response")
        if assistant_msg:
            messages.append(ChatMessage(role="assistant", content=assistant_msg))

    return ChatSession(
        session_id=session_id,
        workspace_name=session_data.get("repository"),
        workspace_path=session_data.get("cwd"),
        messages=messages,
        created_at=session_data.get("created_at"),
        updated_at=session_data.get("updated_at"),
        vscode_edition="cli",
        custom_title=session_data.get("summary"),
        type="cli",
        repository_url=session_data.get("repository"),
        source_format="cli",
    )


def get_all_session_ids(conn: sqlite3.Connection) -> list[str]:
    """Get all session IDs from cst_sessions.

    Args:
        conn: SQLite connection.

    Returns:
        List of session ID strings.
    """
    rows = conn.execute("SELECT session_id FROM cst_sessions").fetchall()
    return [r[0] for r in rows]


def get_messages_markdown(
    conn: sqlite3.Connection,
    session_id: str,
    start: int | None = None,
    end: int | None = None,
    content_set: set[str] | None = None,
) -> str:
    """Get markdown for specific messages or all messages in a session.

    Args:
        conn: SQLite connection.
        session_id: The session ID to get messages from.
        start: Optional 1-based start message index (inclusive).
        end: Optional 1-based end message index (inclusive).
        content_set: Controls which content types to include.
            If None, uses DEFAULT_INCLUDES from content_types module.

    Returns:
        Combined markdown string for the selected messages.
    """
    from .content_types import DEFAULT_INCLUDES

    if content_set is None:
        content_set = DEFAULT_INCLUDES.copy()

    cursor = conn.cursor()

    # Build query based on range
    if start is not None or end is not None:
        # Convert to 0-based indices
        start_idx = (start - 1) if start else 0
        end_idx = (end - 1) if end else 999999  # Large number for "no limit"

        cursor.execute(
            """
            SELECT id, role, content, timestamp, cached_markdown, message_index
            FROM cst_messages
            WHERE session_id = ? AND message_index >= ? AND message_index <= ?
            ORDER BY message_index
            """,
            (session_id, start_idx, end_idx),
        )
    else:
        cursor.execute(
            """
            SELECT id, role, content, timestamp, cached_markdown, message_index
            FROM cst_messages
            WHERE session_id = ?
            ORDER BY message_index
            """,
            (session_id,),
        )

    rows = cursor.fetchall()
    markdown_parts = []

    # Cached markdown was generated with diffs+tool-inputs ON, thinking OFF,
    # agent-details+tools+commands+file-changes ON.  Use it when the requested content_set matches.
    cache_set = {"diffs", "tool-inputs", "agent-details", "tools", "commands", "file-changes"}
    if content_set == cache_set:
        for row in rows:
            md = row["cached_markdown"]
            if md:
                markdown_parts.append(md)
    else:
        # Need to regenerate markdown with specific options
        for row in rows:
            message_id = row["id"]
            message_index = row["message_index"] + 1  # Convert to 1-based

            # Create message object
            message = reconstruct_message(cursor, message_id, row)

            include_diffs = "diffs" in content_set
            include_tool_inputs = "tool-inputs" in content_set
            include_thinking = "thinking" in content_set
            include_agent_details = "agent-details" in content_set
            include_tools = "tools" in content_set
            include_commands = "commands" in content_set
            include_file_changes = "file-changes" in content_set

            # Generate markdown with specified options
            md = message_to_markdown(
                message,
                message_number=message_index,
                include_diffs=include_diffs,
                include_tool_inputs=include_tool_inputs,
                include_thinking=include_thinking,
                include_agent_details=include_agent_details,
                include_tools=include_tools,
                include_commands=include_commands,
                include_file_changes=include_file_changes,
            )
            markdown_parts.append(md)

    return "\n".join(markdown_parts)


def list_sessions(
    conn: sqlite3.Connection,
    *,
    has_cst: bool,
    workspace_name: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    session_type: str | None = None,
) -> list[dict]:
    """List sessions from both built-in and cst_* tables.

    Returns dicts with at minimum: session_id, title, session_type, start_time,
    updated_at, is_enriched, source.  Also includes workspace_name, workspace_path,
    vscode_edition, custom_title, repository_url, message_count, last_message_at,
    first_user_prompt for backward compatibility with cst-sourced rows.

    Deduplicates by session_id — cst_sessions takes precedence over built-in.

    Args:
        conn: SQLite connection.
        has_cst: Whether cst_* tables exist and should be queried.
        workspace_name: Optional workspace name filter (cst rows only).
        limit: Maximum number of sessions to return.
        offset: Number of sessions to skip.
        session_type: Optional filter: 'cli', 'vscode', etc.

    Returns:
        List of session info dictionaries sorted by updated_at descending.
    """
    results: dict[str, dict] = {}

    # 1. Read from cst_sessions if available
    if has_cst:
        cursor = conn.cursor()

        query = """
            SELECT
                s.session_id,
                s.workspace_name,
                s.workspace_path,
                s.created_at,
                s.updated_at,
                s.vscode_edition,
                s.custom_title,
                s.repository_url,
                s.type as session_type,
                s.source_format,
                COUNT(m.id) as message_count,
                MAX(m.timestamp) as last_message_at,
                (SELECT content FROM cst_messages m2
                 WHERE m2.session_id = s.session_id AND m2.role = 'user'
                 ORDER BY m2.message_index LIMIT 1) as first_user_prompt
            FROM cst_sessions s
            LEFT JOIN cst_messages m ON s.session_id = m.session_id
        """
        conditions = []
        params: list = []

        if workspace_name:
            conditions.append("s.workspace_name = ?")
            params.append(workspace_name)
        if session_type:
            conditions.append("s.type = ?")
            params.append(session_type)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " GROUP BY s.session_id HAVING COUNT(m.id) > 0 ORDER BY last_message_at DESC, s.created_at DESC"

        cursor.execute(query, params)
        for row in cursor.fetchall():
            d = dict(row)
            d["title"] = d.get("custom_title") or d.get("workspace_name")
            d["start_time"] = d.get("created_at")
            d["is_enriched"] = True
            d["source"] = "cst"
            if not d.get("session_type"):
                d["session_type"] = d.get("source_format") or "vscode"
            results[d["session_id"]] = d

    # 2. Read from built-in sessions (cli type only)
    if session_type is None or session_type == "cli":
        builtin = list_builtin_sessions(conn, limit=10000)
        # Batch-query turn counts for unenriched sessions
        unenriched_sids = [row["id"] for row in builtin if row["id"] not in results]
        turn_counts: dict[str, int] = {}
        if unenriched_sids:
            try:
                placeholders = ",".join("?" * len(unenriched_sids))
                rows = conn.execute(
                    f"SELECT session_id, SUM((user_message IS NOT NULL AND user_message != '') + (assistant_response IS NOT NULL AND assistant_response != '')) "  # noqa: S608
                    f"FROM turns WHERE session_id IN ({placeholders}) GROUP BY session_id",
                    unenriched_sids,
                ).fetchall()
                turn_counts = dict(rows)
            except sqlite3.OperationalError:
                pass
        for row in builtin:
            sid = row["id"]
            if sid not in results:  # cst_sessions takes precedence
                tc = turn_counts.get(sid, 0)
                if tc == 0:
                    continue  # Skip empty sessions
                results[sid] = {
                    "session_id": sid,
                    "title": row.get("summary"),
                    "session_type": "cli",
                    "start_time": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "is_enriched": False,
                    "source": "builtin",
                    # Backward-compat fields
                    "workspace_name": row.get("repository"),
                    "workspace_path": row.get("cwd"),
                    "created_at": row.get("created_at"),
                    "vscode_edition": "cli",
                    "custom_title": row.get("summary"),
                    "repository_url": row.get("repository"),
                    "message_count": tc,  # Actual non-null message count
                    "last_message_at": row.get("updated_at"),
                    "first_user_prompt": None,
                }

    # Sort by updated_at desc, apply limit/offset
    sorted_results = sorted(results.values(), key=lambda x: x.get("updated_at") or "", reverse=True)
    effective_limit = limit if limit else len(sorted_results)
    return sorted_results[offset : offset + effective_limit]


def get_workspaces(conn: sqlite3.Connection) -> list[dict]:
    """Get all unique workspaces with activity stats.

    Args:
        conn: SQLite connection.

    Returns:
        List of workspace info dictionaries.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            workspace_name,
            workspace_path,
            COUNT(*) as session_count,
            MAX(created_at) as last_activity
        FROM cst_sessions
        WHERE workspace_name IS NOT NULL
        GROUP BY workspace_name, workspace_path
        ORDER BY session_count DESC, workspace_name ASC
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def get_repositories(conn: sqlite3.Connection) -> list[dict]:
    """Get all unique repositories with activity stats.

    Args:
        conn: SQLite connection.

    Returns:
        List of repository info dictionaries.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            repository_url,
            COUNT(*) as session_count,
            MAX(created_at) as last_activity
        FROM cst_sessions
        WHERE repository_url IS NOT NULL
        GROUP BY repository_url
        ORDER BY session_count DESC, repository_url ASC
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def get_stats(conn: sqlite3.Connection, *, has_cst: bool) -> dict:
    """Get database statistics.

    Args:
        conn: SQLite connection.
        has_cst: Whether cst_* tables exist.

    Returns:
        Dictionary with stats (combines enriched cst_* and built-in counts).
    """
    cursor = conn.cursor()

    cst_session_count = 0
    cst_message_count = 0
    workspace_count = 0
    editions: dict = {}

    if has_cst:
        cursor.execute("SELECT COUNT(*) FROM cst_sessions")
        cst_session_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM cst_messages")
        cst_message_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT workspace_name) FROM cst_sessions")
        workspace_count = cursor.fetchone()[0]

        cursor.execute("SELECT vscode_edition, COUNT(*) FROM cst_sessions GROUP BY vscode_edition")
        editions = dict(cursor.fetchall())

    # Count built-in sessions and turns not in cst_*
    builtin_only_count = 0
    builtin_message_count = 0
    try:
        if has_cst:
            cursor.execute("SELECT COUNT(*) FROM sessions WHERE id NOT IN (SELECT session_id FROM cst_sessions)")
            builtin_only_count = cursor.fetchone()[0]
            cursor.execute(
                "SELECT SUM((user_message IS NOT NULL AND user_message != '') + (assistant_response IS NOT NULL AND assistant_response != '')) "
                "FROM turns WHERE session_id NOT IN (SELECT session_id FROM cst_sessions)"
            )
            row = cursor.fetchone()
            builtin_message_count = row[0] or 0
        else:
            cursor.execute("SELECT COUNT(*) FROM sessions")
            builtin_only_count = cursor.fetchone()[0]
            cursor.execute("SELECT SUM((user_message IS NOT NULL AND user_message != '') + (assistant_response IS NOT NULL AND assistant_response != '')) FROM turns")
            row = cursor.fetchone()
            builtin_message_count = row[0] or 0
    except Exception:  # noqa: S110
        pass

    # Each built-in turn may have user, assistant, or both
    total_message_count = cst_message_count + builtin_message_count

    # Include unenriched CLI sessions in the cli edition count
    if builtin_only_count > 0:
        editions["cli"] = editions.get("cli", 0) + builtin_only_count

    return {
        "session_count": cst_session_count + builtin_only_count,
        "message_count": total_message_count,
        "workspace_count": workspace_count,
        "editions": editions,
        "enriched_count": cst_session_count,
        "unenriched_count": builtin_only_count,
    }


def optimize_fts(conn: sqlite3.Connection) -> dict:
    """Optimize the FTS5 full-text search index for better query performance.

    This merges FTS index segments, reducing fragmentation and improving
    search speed. Should be run periodically, especially after bulk imports.

    Args:
        conn: SQLite connection.

    Returns:
        Dictionary with optimization results including segment counts before/after.
    """
    cursor = conn.cursor()

    # Get segment count before optimization
    cursor.execute("SELECT COUNT(*) FROM cst_messages_fts_data")
    segments_before = cursor.fetchone()[0]

    # Run FTS5 optimize command - merges all segments into one
    cursor.execute("INSERT INTO cst_messages_fts(cst_messages_fts) VALUES('optimize')")

    # Get segment count after optimization
    cursor.execute("SELECT COUNT(*) FROM cst_messages_fts_data")
    segments_after = cursor.fetchone()[0]

    # Also run integrity check
    cursor.execute("INSERT INTO cst_messages_fts(cst_messages_fts) VALUES('integrity-check')")

    conn.commit()

    return {
        "segments_before": segments_before,
        "segments_after": segments_after,
        "optimized": True,
    }
