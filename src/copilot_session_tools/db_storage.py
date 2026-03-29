"""Storage and ingestion module for Copilot chat session data.

This module contains the write-path logic extracted from database.py:
schema creation/migration, session ingestion, enrichment, and update
operations.  All public functions operate on raw ``sqlite3.Connection``
objects so they can be used both from the ``Database`` façade and from
standalone tooling.
"""

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
from .scanner import ChatSession

CST_SCHEMA_VERSION = 6


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
    cleanup_model TEXT,
    parent_message_id INTEGER REFERENCES cst_messages(id) ON DELETE CASCADE,
    child_index INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cst_messages_session ON cst_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_cst_messages_parent ON cst_messages(parent_message_id);

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
    child_message_id INTEGER REFERENCES cst_messages(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_cst_content_blocks_message ON cst_content_blocks(message_id);

CREATE VIEW IF NOT EXISTS cst_messages_tree AS
SELECT
    m.id,
    m.session_id,
    m.message_index,
    m.role,
    m.agent_display_name,
    m.agent_nesting_level,
    m.parent_message_id,
    m.child_index,
    pm.agent_display_name as parent_agent_name,
    pm.message_index as parent_message_index
FROM cst_messages m
LEFT JOIN cst_messages pm ON m.parent_message_id = pm.id;

CREATE VIEW IF NOT EXISTS cst_all_tool_invocations AS
SELECT
    t.id as tool_id,
    t.name,
    t.input,
    t.result,
    t.status,
    t.invocation_message,
    m.id as message_id,
    m.session_id,
    m.agent_display_name,
    m.agent_nesting_level,
    m.parent_message_id
FROM cst_tool_invocations t
JOIN cst_messages m ON t.message_id = m.id;

CREATE VIEW IF NOT EXISTS cst_subagent_summary AS
SELECT
    m.id as message_id,
    m.session_id,
    m.agent_display_name,
    m.parent_message_id,
    (SELECT COUNT(*) FROM cst_tool_invocations t WHERE t.message_id = m.id) as tool_count,
    (SELECT COUNT(*) FROM cst_file_changes f WHERE f.message_id = m.id) as file_change_count,
    (SELECT COUNT(*) FROM cst_command_runs c WHERE c.message_id = m.id) as command_count,
    (SELECT COUNT(*) FROM cst_content_blocks cb WHERE cb.message_id = m.id) as content_block_count
FROM cst_messages m
WHERE m.parent_message_id IS NOT NULL;
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


def _drop_and_recreate_cst_tables(conn: sqlite3.Connection) -> None:
    """Drop and recreate all cst_* tables for major schema migrations.

    This is safe because cst_* tables are derived from source session files
    and will be re-populated during the next enrichment pass.
    """
    cursor = conn.cursor()
    # Drop views before tables
    for view in ["cst_messages_tree", "cst_all_tool_invocations", "cst_subagent_summary"]:
        cursor.execute(f"DROP VIEW IF EXISTS {view}")
    # Drop in reverse dependency order
    for table in [
        "cst_messages_fts",  # Virtual table
        "cst_content_blocks",
        "cst_command_runs",
        "cst_file_changes",
        "cst_tool_invocations",
        "cst_messages",
        "cst_sessions",
        "cst_schema_version",
    ]:
        cursor.execute(f"DROP TABLE IF EXISTS {table}")
    # Drop triggers (they reference tables that no longer exist)
    for trigger in ["cst_messages_ai", "cst_messages_ad", "cst_messages_au"]:
        cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    # Recreate with new schema
    conn.executescript(CST_SCHEMA)
    conn.executescript(CST_FTS_SCHEMA)
    # Insert fresh schema version
    cursor.execute("INSERT INTO cst_schema_version (version) VALUES (?)", (CST_SCHEMA_VERSION,))


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure the cst_* schema exists in the database.

    Creates cst_* tables and FTS triggers alongside the built-in
    Copilot CLI tables. Does NOT create or touch built-in tables.
    """
    cursor = conn.cursor()

    # Check if schema already exists and needs migration BEFORE creating tables.
    # This is critical for v6+ because CST_SCHEMA includes views that reference
    # new columns — running it against a v5 table would fail.
    needs_migration = False
    try:
        cursor.execute("SELECT COUNT(*) FROM cst_schema_version")
        has_schema = cursor.fetchone()[0] > 0
        if has_schema:
            cursor.execute("SELECT version FROM cst_schema_version LIMIT 1")
            current_version = cursor.fetchone()[0]
            if current_version < 6:
                # v6 requires drop/recreate — do it before CST_SCHEMA runs
                _drop_and_recreate_cst_tables(conn)
                return  # _drop_and_recreate already creates schema + stamps version
            needs_migration = current_version < CST_SCHEMA_VERSION
    except sqlite3.OperationalError:
        has_schema = False

    # Create cst_* tables (safe: either fresh DB or already at v6+)
    conn.executescript(CST_SCHEMA)
    # Create FTS table and triggers
    conn.executescript(CST_FTS_SCHEMA)

    if not has_schema:
        # Fresh database — stamp schema version
        cursor.execute("SELECT COUNT(*) FROM cst_schema_version")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO cst_schema_version (version) VALUES (?)",
                (CST_SCHEMA_VERSION,),
            )
    elif needs_migration:
        # Future migrations (v7+) would go here
        cursor.execute(
            "UPDATE cst_schema_version SET version = ?",
            (CST_SCHEMA_VERSION,),
        )


def check_builtin_schema_version(conn: sqlite3.Connection) -> None:
    """Warn if the Chronicle session store schema has been updated beyond our known version.

    Expects Chronicle to be ATTACHed as ``chronicle`` schema.
    """
    try:
        row = conn.execute("SELECT version FROM chronicle.schema_version LIMIT 1").fetchone()
        if row and row[0] > 1:
            import warnings

            warnings.warn(
                f"Session store schema version {row[0]} is newer than expected (1). Some features may not work correctly. Consider updating copilot-session-tools.",
                stacklevel=2,
            )
    except Exception:  # noqa: S110
        pass  # Table might not exist or chronicle not attached


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


def discover_sessions_needing_enrichment(conn: sqlite3.Connection, *, has_chronicle: bool = False) -> list[dict]:
    """Find CLI sessions needing enrichment by comparing Chronicle turns vs cst_messages.

    When Chronicle is ATTACHed (``has_chronicle=True``), compares
    ``chronicle.sessions``/``chronicle.turns`` against ``cst_messages``.
    Without Chronicle, returns an empty list (no discovery source).

    Returns sessions where:
    - No cst_sessions row exists (new, never enriched)
    - Turn count differs (session has new messages since last enrichment)

    Uses direct sqlite_master probe (not has_cst_tables()) so this works
    correctly even when --unenriched-only is set — scan should still enrich.
    """
    if not has_chronicle:
        return []

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
                FROM chronicle.sessions s
                LEFT JOIN chronicle.turns t ON s.id = t.session_id
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
                FROM chronicle.sessions s
                LEFT JOIN chronicle.turns t ON s.id = t.session_id
                GROUP BY s.id
            """).fetchall()

        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


def _delete_session_data(cursor: sqlite3.Cursor, session_id: str) -> None:
    """Delete all cst_* rows for a session, including child-table artifacts.

    Explicitly deletes from child tables (tool_invocations, file_changes,
    command_runs, content_blocks) before deleting messages, rather than
    relying on ``ON DELETE CASCADE`` — which only fires when
    ``PRAGMA foreign_keys = ON``.  This is safe in all FK modes.
    """
    cursor.execute(
        "DELETE FROM cst_content_blocks WHERE message_id IN (SELECT id FROM cst_messages WHERE session_id = ?)",
        (session_id,),
    )
    cursor.execute(
        "DELETE FROM cst_tool_invocations WHERE message_id IN (SELECT id FROM cst_messages WHERE session_id = ?)",
        (session_id,),
    )
    cursor.execute(
        "DELETE FROM cst_file_changes WHERE message_id IN (SELECT id FROM cst_messages WHERE session_id = ?)",
        (session_id,),
    )
    cursor.execute(
        "DELETE FROM cst_command_runs WHERE message_id IN (SELECT id FROM cst_messages WHERE session_id = ?)",
        (session_id,),
    )
    cursor.execute("DELETE FROM cst_messages_fts WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM cst_messages WHERE session_id = ?", (session_id,))


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


def _insert_content_blocks_recursive(
    cursor: sqlite3.Cursor,
    session_id: str,
    parent_message_id: int,
    msg_index: int,
    content_blocks,
) -> None:
    """Insert content blocks, recursively handling child subagent messages."""
    for block_idx, block in enumerate(content_blocks):
        child_message_id = None

        if block.child_message and block.kind in ("subagent", "subagent_failed", "subagent_incomplete"):
            child = block.child_message
            child_cached_md = message_to_markdown(child, message_number=msg_index + 1)
            cursor.execute(
                insert_sql("cst_messages", CST_MESSAGE_COLUMNS),
                (
                    session_id,
                    msg_index,  # Same message_index as parent
                    child.role,
                    child.content,
                    child.timestamp,
                    child_cached_md,
                    child.agent_id,
                    child.agent_display_name,
                    child.agent_nesting_level,
                    parent_message_id,  # parent_message_id = parent's ID
                    block_idx,  # child_index = block position for ordering
                ),
            )
            child_message_id = cursor.lastrowid

            # Insert child's artifacts
            for tool in child.tool_invocations:
                cursor.execute(
                    insert_sql("cst_tool_invocations", CST_TOOL_INVOCATION_COLUMNS),
                    tool_to_row(child_message_id, tool),
                )
            for change in child.file_changes:
                cursor.execute(
                    insert_sql("cst_file_changes", CST_FILE_CHANGE_COLUMNS),
                    file_change_to_row(child_message_id, change),
                )
            for cmd in child.command_runs:
                cursor.execute(
                    insert_sql("cst_command_runs", CST_COMMAND_RUN_COLUMNS),
                    command_to_row(child_message_id, cmd),
                )

            # Recurse into child's content blocks (handles grandchildren)
            _insert_content_blocks_recursive(
                cursor,
                session_id,
                child_message_id,
                msg_index,
                child.content_blocks,
            )

        # Insert this content block row (links to child_message_id if applicable)
        cursor.execute(
            insert_sql("cst_content_blocks", CST_CONTENT_BLOCK_COLUMNS),
            (parent_message_id, block_idx, block.kind, block.content, block.description, child_message_id),
        )


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
                None,  # parent_message_id — set by db-insertion todo
                msg.child_index,
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

        # Insert content blocks (recursive for nested subagent children)
        _insert_content_blocks_recursive(
            cursor,
            session.session_id,
            message_id,
            idx,
            msg.content_blocks,
        )


def update_session(conn: sqlite3.Connection, session: ChatSession) -> None:
    """Update an existing session or add it if it doesn't exist."""
    cursor = conn.cursor()

    # Delete existing session and related data atomically
    # Explicitly delete from child tables — required when FK enforcement is OFF.
    _delete_session_data(cursor, session.session_id)
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
        _delete_session_data(cursor, session.session_id)
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
    # Explicitly delete from child tables first — required because FK enforcement
    # may be OFF (e.g. inside batch_connection), so CASCADE won't fire.
    _delete_session_data(cursor, session.session_id)
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
                None,  # parent_message_id — set by db-insertion todo
                msg.child_index,
            ),
        )
        message_id = cursor.lastrowid

        # Insert content blocks (recursive for nested subagent children)
        _insert_content_blocks_recursive(
            cursor,
            session.session_id,
            message_id,
            idx,
            msg.content_blocks,
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


def cleanup_orphaned_cst_sessions(conn: sqlite3.Connection, *, has_chronicle: bool = False) -> list[str]:
    """Find and delete cst_sessions whose session_id doesn't exist in Chronicle's sessions table.

    Only targets CLI sessions (source_type='cli') since VS Code sessions
    only exist in cst_* tables.

    Requires Chronicle to be ATTACHed as ``chronicle`` schema.
    Without Chronicle, orphan detection is skipped (returns empty list).

    Returns:
        List of deleted session_ids.
    """
    if not has_chronicle:
        return []

    cursor = conn.cursor()

    # Find orphaned CLI sessions
    rows = cursor.execute(
        """
        SELECT cs.session_id FROM cst_sessions cs
        WHERE cs.type = 'cli'
        AND cs.session_id NOT IN (SELECT id FROM chronicle.sessions)
        """,
    ).fetchall()

    orphaned_ids = [row[0] for row in rows]

    # Delete orphaned sessions and all related data
    for session_id in orphaned_ids:
        cursor.execute("DELETE FROM cst_messages_fts WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM cst_messages WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM cst_sessions WHERE session_id = ?", (session_id,))

    return orphaned_ids
