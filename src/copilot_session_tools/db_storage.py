"""Storage and ingestion module for Copilot chat session data.

This module contains the write-path logic extracted from database.py:
schema creation/migration, session ingestion, enrichment, and update
operations.  All public functions operate on raw ``sqlite3.Connection``
objects so they can be used both from the ``Database`` façade and from
standalone tooling.
"""

import contextlib
import json
import sqlite3
from datetime import UTC, datetime

from .db_schema import (
    CST_COMMAND_RUN_COLUMNS,
    CST_CONTENT_BLOCK_COLUMNS,
    CST_FILE_CHANGE_COLUMNS,
    CST_MESSAGE_COLUMNS,
    CST_SESSION_COLUMNS,
    CST_TOOL_INVOCATION_COLUMNS,
    command_to_row,
    file_change_to_row,
    insert_sql,
    session_to_row,
    tool_to_row,
)
from .markdown_exporter import message_to_markdown
from .scanner import ChatSession, ContentBlock

CST_SCHEMA_VERSION = 5


def _serialize_nested_data(block: ContentBlock) -> str | None:
    """Serialize a subagent ContentBlock's structured sub-content to JSON."""
    if not (block.content_blocks or block.tool_invocations or block.file_changes or block.command_runs):
        return None
    from dataclasses import asdict

    data: dict = {}
    if block.content_blocks:
        # Only serialize the fields deserialization actually uses (kind, content, description)
        # to avoid silent data loss from asdict's deep recursion on nested ContentBlocks
        data["content_blocks"] = [{"kind": cb.kind, "content": cb.content, "description": cb.description} for cb in block.content_blocks]
    if block.tool_invocations:
        data["tool_invocations"] = [{k: v for k, v in asdict(t).items() if v is not None} for t in block.tool_invocations]
    if block.file_changes:
        data["file_changes"] = [{k: v for k, v in asdict(f).items() if v is not None} for f in block.file_changes]
    if block.command_runs:
        data["command_runs"] = [{k: v for k, v in asdict(c).items() if v is not None} for c in block.command_runs]
    return json.dumps(data)


CST_SCHEMA = """
CREATE TABLE IF NOT EXISTS cst_schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cst_sessions (
    session_id TEXT PRIMARY KEY,
    workspace_name TEXT,
    workspace_path TEXT,
    created_at TEXT,
    updated_at TEXT,
    source_file TEXT,
    vscode_edition TEXT DEFAULT 'stable',
    custom_title TEXT,
    requester_username TEXT,
    responder_username TEXT,
    source_file_mtime REAL,
    source_file_size INTEGER,
    type TEXT DEFAULT 'vscode',
    repository_url TEXT,
    parser_version INTEGER NOT NULL DEFAULT 1,
    source_format TEXT,
    enrichment_version TEXT
);

CREATE TABLE IF NOT EXISTS cst_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES cst_sessions(session_id) ON DELETE CASCADE,
    message_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    timestamp TEXT,
    cached_markdown TEXT,
    agent_id TEXT,
    agent_display_name TEXT,
    agent_nesting_level INTEGER DEFAULT 0,
    original_content TEXT,
    cleanup_model TEXT
);
CREATE INDEX IF NOT EXISTS idx_cst_messages_session ON cst_messages(session_id);

CREATE TABLE IF NOT EXISTS cst_tool_invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES cst_messages(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    input TEXT,
    result TEXT,
    status TEXT,
    start_time INTEGER,
    end_time INTEGER,
    source_type TEXT,
    invocation_message TEXT,
    subagent_invocation_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cst_tool_invocations_message ON cst_tool_invocations(message_id);

CREATE TABLE IF NOT EXISTS cst_file_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES cst_messages(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    diff TEXT,
    content TEXT,
    explanation TEXT,
    language_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cst_file_changes_message ON cst_file_changes(message_id);

CREATE TABLE IF NOT EXISTS cst_command_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES cst_messages(id) ON DELETE CASCADE,
    command TEXT NOT NULL,
    title TEXT,
    result TEXT,
    status TEXT,
    output TEXT,
    timestamp INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cst_command_runs_message ON cst_command_runs(message_id);

CREATE TABLE IF NOT EXISTS cst_content_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES cst_messages(id) ON DELETE CASCADE,
    block_index INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',
    content TEXT,
    description TEXT,
    nested_data TEXT
);
CREATE INDEX IF NOT EXISTS idx_cst_content_blocks_message ON cst_content_blocks(message_id);
"""

CST_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS cst_messages_fts USING fts5(
    content,
    session_id UNINDEXED,
    message_id UNINDEXED,
    role UNINDEXED
);

CREATE TRIGGER IF NOT EXISTS cst_messages_ai AFTER INSERT ON cst_messages BEGIN
    INSERT INTO cst_messages_fts(rowid, content, session_id, message_id, role)
    VALUES (new.id, new.content, new.session_id, new.id, new.role);
END;

CREATE TRIGGER IF NOT EXISTS cst_messages_ad AFTER DELETE ON cst_messages BEGIN
    DELETE FROM cst_messages_fts WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS cst_messages_au AFTER UPDATE ON cst_messages BEGIN
    DELETE FROM cst_messages_fts WHERE rowid = old.id;
    INSERT INTO cst_messages_fts(rowid, content, session_id, message_id, role)
    VALUES (new.id, new.content, new.session_id, new.id, new.role);
END;
"""


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure the cst_* schema exists in the database.

    Creates cst_* tables and FTS triggers alongside the built-in
    Copilot CLI tables. Does NOT create or touch built-in tables.
    """
    cursor = conn.cursor()

    # Create cst_* tables
    conn.executescript(CST_SCHEMA)
    # Create FTS table and triggers
    conn.executescript(CST_FTS_SCHEMA)

    # Insert or update schema version
    cursor.execute("SELECT COUNT(*) FROM cst_schema_version")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO cst_schema_version (version) VALUES (?)",
            (CST_SCHEMA_VERSION,),
        )
    else:
        # Migrate from older schema versions
        cursor.execute("SELECT version FROM cst_schema_version LIMIT 1")
        current_version = cursor.fetchone()[0]
        if current_version < 2:
            # v2: add agent metadata columns to cst_messages and subagent_invocation_id to cst_tool_invocations
            for col_sql in [
                "ALTER TABLE cst_messages ADD COLUMN agent_id TEXT",
                "ALTER TABLE cst_messages ADD COLUMN agent_display_name TEXT",
                "ALTER TABLE cst_messages ADD COLUMN agent_nesting_level INTEGER DEFAULT 0",
                "ALTER TABLE cst_tool_invocations ADD COLUMN subagent_invocation_id TEXT",
            ]:
                with contextlib.suppress(Exception):
                    cursor.execute(col_sql)
        if current_version < 3:
            # v3: add nested_data column to cst_content_blocks for structured subagent rendering
            with contextlib.suppress(Exception):
                cursor.execute("ALTER TABLE cst_content_blocks ADD COLUMN nested_data TEXT")
            # v3: add enrichment_version column to cst_sessions
            with contextlib.suppress(Exception):
                cursor.execute("ALTER TABLE cst_sessions ADD COLUMN enrichment_version TEXT")
        if current_version < 4:
            # v4: catch-up for databases at v3 from either branch (suppress if already present)
            with contextlib.suppress(Exception):
                cursor.execute("ALTER TABLE cst_content_blocks ADD COLUMN nested_data TEXT")
            with contextlib.suppress(Exception):
                cursor.execute("ALTER TABLE cst_sessions ADD COLUMN enrichment_version TEXT")
        if current_version < 5:
            # v5: transcript cleanup columns for voice-dictated message cleanup
            pass  # Handled by catch-up block below
        # v5 catch-up: ensure columns exist even if version was already bumped
        with contextlib.suppress(Exception):
            cursor.execute("ALTER TABLE cst_messages ADD COLUMN original_content TEXT")
        with contextlib.suppress(Exception):
            cursor.execute("ALTER TABLE cst_messages ADD COLUMN cleanup_model TEXT")
        if current_version < CST_SCHEMA_VERSION:
            cursor.execute(
                "UPDATE cst_schema_version SET version = ?",
                (CST_SCHEMA_VERSION,),
            )


def check_builtin_schema_version(conn: sqlite3.Connection) -> None:
    """Warn if the built-in session store schema has been updated beyond our known version."""
    try:
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row and row[0] > 1:
            import warnings

            warnings.warn(
                f"Session store schema version {row[0]} is newer than expected (1). Some features may not work correctly. Consider updating copilot-session-tools.",
                stacklevel=2,
            )
    except Exception:  # noqa: S110
        pass  # Table might not exist if DB is not a session-store.db


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def has_cst_tables(conn: sqlite3.Connection, *, unenriched_only: bool = False) -> bool:
    """Check if cst_* extension tables exist in the database."""
    if unenriched_only:
        return False
    try:
        row = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='cst_sessions'").fetchone()
        return row[0] > 0
    except Exception:
        return False


def discover_sessions_needing_enrichment(conn: sqlite3.Connection) -> list[dict]:
    """Find CLI sessions needing enrichment by comparing built-in turns vs cst_messages.

    Compares built-in turns count against cst_messages user-role count for each session.
    Returns sessions where:
    - No cst_sessions row exists (new, never enriched)
    - Turn count differs (session has new messages since last enrichment)

    Uses direct sqlite_master probe (not has_cst_tables()) so this works
    correctly even when --unenriched-only is set — scan should still enrich.
    """
    try:
        # Check physical table existence, not the unenriched_only flag
        cst_exists = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='cst_sessions'").fetchone()[0] > 0

        if cst_exists:
            rows = conn.execute("""
                SELECT
                    s.id as session_id,
                    COUNT(DISTINCT t.turn_index) as builtin_turns,
                    (SELECT COUNT(*) FROM cst_messages cm
                     WHERE cm.session_id = s.id AND cm.role = 'user') as cst_user_msgs,
                    CASE WHEN cs.session_id IS NULL THEN 'new'
                         ELSE 'stale' END as status
                FROM sessions s
                LEFT JOIN turns t ON s.id = t.session_id
                LEFT JOIN cst_sessions cs ON s.id = cs.session_id
                GROUP BY s.id
                HAVING cst_user_msgs != builtin_turns
                    OR cs.session_id IS NULL
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT
                    s.id as session_id,
                    COUNT(DISTINCT t.turn_index) as builtin_turns,
                    0 as cst_user_msgs,
                    'new' as status
                FROM sessions s
                LEFT JOIN turns t ON s.id = t.session_id
                GROUP BY s.id
            """).fetchall()

        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


def add_session(conn: sqlite3.Connection, session: ChatSession) -> bool:
    """Add a chat session to the database.

    Returns True if the session was added, False if it already exists.
    """
    cursor = conn.cursor()

    # Check if session already exists
    cursor.execute("SELECT session_id FROM cst_sessions WHERE session_id = ?", (session.session_id,))
    if cursor.fetchone():
        return False

    add_session_impl(cursor, session)
    return True


def add_sessions_batch(conn: sqlite3.Connection, sessions: list[ChatSession]) -> tuple[int, int]:
    """Add multiple sessions in a single transaction.

    Much faster than calling add_session() repeatedly as it uses
    a single connection and commit.

    Returns:
        Tuple of (added_count, skipped_count).
    """
    added = 0
    skipped = 0

    cursor = conn.cursor()

    for session in sessions:
        # Check if session already exists
        cursor.execute("SELECT session_id FROM cst_sessions WHERE session_id = ?", (session.session_id,))
        if cursor.fetchone():
            skipped += 1
            continue

        # Add the session within this transaction
        add_session_impl(cursor, session)
        added += 1

    return added, skipped


def add_session_impl(cursor: sqlite3.Cursor, session: ChatSession) -> None:
    """Insert a ChatSession and all its child rows using an existing cursor.

    Used by add_session, add_sessions_batch, update_session, etc.
    """
    from copilot_session_tools import __version__

    # Insert into cst_sessions table
    cursor.execute(
        insert_sql("cst_sessions", CST_SESSION_COLUMNS),
        session_to_row(session, enrichment_version=__version__),
    )

    # Insert messages and related data
    for idx, msg in enumerate(session.messages):
        cached_markdown = message_to_markdown(
            msg,
            message_number=idx + 1,
            include_diffs=True,
            include_tool_inputs=True,
        )

        cursor.execute(
            insert_sql("cst_messages", CST_MESSAGE_COLUMNS),
            (
                session.session_id,
                idx,
                msg.role,
                msg.content,
                msg.timestamp,
                cached_markdown,
                msg.agent_id,
                msg.agent_display_name,
                msg.agent_nesting_level,
            ),
        )
        message_id = cursor.lastrowid

        # Insert tool invocations
        for tool in msg.tool_invocations:
            cursor.execute(
                insert_sql("cst_tool_invocations", CST_TOOL_INVOCATION_COLUMNS),
                tool_to_row(message_id, tool),
            )

        # Insert file changes
        for change in msg.file_changes:
            cursor.execute(
                insert_sql("cst_file_changes", CST_FILE_CHANGE_COLUMNS),
                file_change_to_row(message_id, change),
            )

        # Insert command runs
        for cmd in msg.command_runs:
            cursor.execute(
                insert_sql("cst_command_runs", CST_COMMAND_RUN_COLUMNS),
                command_to_row(message_id, cmd),
            )

        # Insert content blocks
        for block_idx, block in enumerate(msg.content_blocks):
            nested_data = _serialize_nested_data(block) if block.kind in ("subagent", "subagent_failed", "subagent_incomplete") else None
            cursor.execute(
                insert_sql("cst_content_blocks", CST_CONTENT_BLOCK_COLUMNS),
                (
                    message_id,
                    block_idx,
                    block.kind,
                    block.content,
                    block.description,
                    nested_data,
                ),
            )


def update_session(conn: sqlite3.Connection, session: ChatSession) -> None:
    """Update an existing session or add it if it doesn't exist."""
    cursor = conn.cursor()

    # Delete existing session and related data atomically
    cursor.execute("DELETE FROM cst_messages_fts WHERE session_id = ?", (session.session_id,))
    cursor.execute("DELETE FROM cst_messages WHERE session_id = ?", (session.session_id,))
    cursor.execute("DELETE FROM cst_sessions WHERE session_id = ?", (session.session_id,))

    # Re-insert in the same transaction
    add_session_impl(cursor, session)


def update_sessions_batch(conn: sqlite3.Connection, sessions: list[ChatSession]) -> int:
    """Update multiple sessions in a single transaction.

    Returns:
        Number of sessions updated.
    """
    if not sessions:
        return 0
    cursor = conn.cursor()
    for session in sessions:
        cursor.execute("DELETE FROM cst_messages_fts WHERE session_id = ?", (session.session_id,))
        cursor.execute("DELETE FROM cst_messages WHERE session_id = ?", (session.session_id,))
        cursor.execute("DELETE FROM cst_sessions WHERE session_id = ?", (session.session_id,))
        add_session_impl(cursor, session)
    return len(sessions)


# ---------------------------------------------------------------------------
# Version / reparse helpers
# ---------------------------------------------------------------------------


def get_sessions_needing_reparse(conn: sqlite3.Connection, current_parser_version: int) -> list[dict]:
    """Find cst_sessions with parser_version < current_parser_version."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cst_sessions'")
    if cursor.fetchone() is None:
        return []
    rows = conn.execute(
        "SELECT session_id, type, source_format, source_file, parser_version FROM cst_sessions WHERE parser_version < ?",
        (current_parser_version,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_sessions_needing_version_refresh(conn: sqlite3.Connection, current_version: str) -> int:
    """Count enriched sessions whose enrichment_version differs from current_version."""
    from packaging.version import Version

    current = Version(current_version)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cst_sessions'")
    if cursor.fetchone() is None:
        return 0
    rows = conn.execute(
        "SELECT DISTINCT enrichment_version FROM cst_sessions",
    ).fetchall()
    stale_versions = [r[0] for r in rows if r[0] is None or Version(r[0]) < current]
    if not stale_versions:
        return 0
    # Count sessions with NULL or any stale version
    placeholders = ",".join("?" for _ in stale_versions if _ is not None)
    has_null = any(v is None for v in stale_versions)
    conditions = []
    params: list[str] = []
    if has_null:
        conditions.append("enrichment_version IS NULL")
    if placeholders:
        conditions.append(f"enrichment_version IN ({placeholders})")
        params.extend(v for v in stale_versions if v is not None)
    row = conn.execute(
        f"SELECT COUNT(*) FROM cst_sessions WHERE ({' OR '.join(conditions)}) "  # noqa: S608
        "AND EXISTS (SELECT 1 FROM cst_messages m WHERE m.session_id = cst_sessions.session_id)",
        params,
    ).fetchone()
    return row[0] if row else 0


def get_sessions_needing_version_refresh(conn: sqlite3.Connection, current_version: str) -> list[dict]:
    """Find enriched sessions whose enrichment_version is older than current_version."""
    from packaging.version import Version

    current = Version(current_version)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cst_sessions'")
    if cursor.fetchone() is None:
        return []
    rows = conn.execute(
        "SELECT session_id, type, source_format, source_file, enrichment_version FROM cst_sessions",
    ).fetchall()
    return [dict(r) for r in rows if r["enrichment_version"] is None or Version(r["enrichment_version"]) < current]


def get_session_enrichment_version(conn: sqlite3.Connection, session_id: str) -> str | None:
    """Get the enrichment_version for a specific session, or None if not enriched."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cst_sessions'")
    if cursor.fetchone() is None:
        return None
    row = conn.execute(
        "SELECT enrichment_version FROM cst_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return row[0] if row else None


def update_enrichment_version(conn: sqlite3.Connection, session_id: str, version: str) -> None:
    """Stamp a session's enrichment_version and parser_version, creating a stub row if needed."""
    from copilot_session_tools.scanner import PARSER_VERSION

    updated = conn.execute(
        "UPDATE cst_sessions SET enrichment_version = ?, parser_version = ? WHERE session_id = ?",
        (version, PARSER_VERSION, session_id),
    ).rowcount
    if not updated:
        # No cst_sessions row exists — create a minimal stub so the
        # session stops appearing in "needs enrichment" queries.
        conn.execute(
            """INSERT OR IGNORE INTO cst_sessions 
               (session_id, type, enrichment_version, parser_version)
               VALUES (?, 'cli', ?, ?)""",
            (session_id, version, PARSER_VERSION),
        )


def delete_cst_session(conn: sqlite3.Connection, session_id: str) -> bool:
    """Delete all cst_* data for a session. Returns True if session existed."""
    cursor = conn.execute("DELETE FROM cst_sessions WHERE session_id = ?", (session_id,))
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# File-metadata helpers
# ---------------------------------------------------------------------------


def needs_update(conn: sqlite3.Connection, session_id: str, file_mtime: float | None, file_size: int | None) -> bool:
    """Check if a session needs to be updated based on file metadata.

    Returns True if:
    - Session doesn't exist, OR
    - Stored mtime/size is NULL (migration case), OR
    - Stored mtime/size differs from provided values
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT source_file_mtime, source_file_size FROM cst_sessions WHERE session_id = ?",
        (session_id,),
    )
    row = cursor.fetchone()

    if row is None:
        return True

    stored_mtime = row[0]
    stored_size = row[1]

    if stored_mtime is None or stored_size is None:
        return True

    return stored_mtime != file_mtime or stored_size != file_size


def needs_update_by_file(conn: sqlite3.Connection, source_file: str, file_mtime: float, file_size: int) -> bool:
    """Check if a file needs to be parsed based on its metadata.

    This is more efficient than needs_update() as it doesn't require
    parsing the file first to get the session_id.

    Returns True if:
    - No session exists with this source_file, OR
    - Stored mtime/size differs from provided values
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT source_file_mtime, source_file_size FROM cst_sessions WHERE source_file = ?",
        (source_file,),
    )
    row = cursor.fetchone()

    if row is None:
        return True

    stored_mtime, stored_size = row[0], row[1]
    if stored_mtime is None or stored_size is None:
        return True

    return stored_mtime != file_mtime or stored_size != file_size


def get_all_file_metadata(conn: sqlite3.Connection) -> dict[str, tuple[float, int, int]]:
    """Get all stored file metadata in one query.

    Returns a dict mapping source_file -> (mtime, size, session_count)
    for all sessions.  The *session_count* indicates how many sessions
    were parsed from that file (relevant for vscdb files which contain
    multiple sessions in a single file).
    """
    cursor = conn.cursor()
    cursor.execute("SELECT source_file, source_file_mtime, source_file_size, COUNT(*) FROM cst_sessions WHERE source_file IS NOT NULL GROUP BY source_file")
    return {row[0]: (row[1], row[2], row[3]) for row in cursor.fetchall()}


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


def enrich_session(conn: sqlite3.Connection, session: ChatSession) -> None:
    """Write/update cst_* tables for a parsed ChatSession.

    Idempotent: deletes existing data for this session_id, then inserts fresh.
    """
    from copilot_session_tools import __version__

    enriched_at = datetime.now(UTC).isoformat()

    cursor = conn.cursor()

    # Delete existing data for idempotency
    cursor.execute("DELETE FROM cst_messages_fts WHERE session_id = ?", (session.session_id,))
    cursor.execute("DELETE FROM cst_messages WHERE session_id = ?", (session.session_id,))
    cursor.execute("DELETE FROM cst_sessions WHERE session_id = ?", (session.session_id,))

    # Insert session
    cursor.execute(
        insert_sql("cst_sessions", CST_SESSION_COLUMNS),
        session_to_row(session, enrichment_version=__version__, updated_at_fallback=enriched_at),
    )

    # Insert messages and related data
    for idx, msg in enumerate(session.messages):
        cached_markdown = message_to_markdown(
            msg,
            message_number=idx + 1,
            include_diffs=True,
            include_tool_inputs=True,
        )

        cursor.execute(
            insert_sql("cst_messages", CST_MESSAGE_COLUMNS),
            (
                session.session_id,
                idx,
                msg.role,
                msg.content,
                msg.timestamp,
                cached_markdown,
                msg.agent_id,
                msg.agent_display_name,
                msg.agent_nesting_level,
            ),
        )
        message_id = cursor.lastrowid

        # Insert content blocks
        for block_idx, block in enumerate(msg.content_blocks):
            nested_data = _serialize_nested_data(block) if block.kind in ("subagent", "subagent_failed", "subagent_incomplete") else None
            cursor.execute(
                insert_sql("cst_content_blocks", CST_CONTENT_BLOCK_COLUMNS),
                (message_id, block_idx, block.kind, block.content, block.description, nested_data),
            )

        # Insert tool invocations
        for tool in msg.tool_invocations:
            cursor.execute(
                insert_sql("cst_tool_invocations", CST_TOOL_INVOCATION_COLUMNS),
                tool_to_row(message_id, tool),
            )

        # Insert command runs
        for cmd in msg.command_runs:
            cursor.execute(
                insert_sql("cst_command_runs", CST_COMMAND_RUN_COLUMNS),
                command_to_row(message_id, cmd),
            )

        # Insert file changes
        for change in msg.file_changes:
            cursor.execute(
                insert_sql("cst_file_changes", CST_FILE_CHANGE_COLUMNS),
                file_change_to_row(message_id, change),
            )


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def cleanup_orphaned_cst_sessions(conn: sqlite3.Connection) -> list[str]:
    """Find and delete cst_sessions whose session_id doesn't exist in the built-in sessions table.

    Only targets CLI sessions (source_type='cli') since VS Code sessions
    only exist in cst_* tables.

    Returns:
        List of deleted session_ids.
    """
    cursor = conn.cursor()

    # Find orphaned CLI sessions
    rows = cursor.execute(
        """
        SELECT cs.session_id FROM cst_sessions cs
        WHERE cs.type = 'cli'
        AND cs.session_id NOT IN (SELECT id FROM sessions)
        """,
    ).fetchall()

    orphaned_ids = [row[0] for row in rows]

    # Delete orphaned sessions and all related data
    for session_id in orphaned_ids:
        cursor.execute("DELETE FROM cst_messages_fts WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM cst_messages WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM cst_sessions WHERE session_id = ?", (session_id,))

    return orphaned_ids
