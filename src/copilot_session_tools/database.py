"""Database module for storing and querying Copilot chat sessions.

Schema design inspired by:
- tad-hq/universal-session-viewer: FTS5 full-text search
- jazzyalex/agent-sessions: SQLite indexing patterns

The database is ~/.copilot/session-store.db which already has built-in tables
(sessions, turns, checkpoints, session_files, session_refs, search_index,
schema_version) managed by Copilot CLI. We add our own cst_* tables alongside.
"""

import contextlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


@dataclass
class ParsedQuery:
    """Represents a parsed search query with extracted field filters."""

    fts_query: str  # The FTS5 query string for content search
    role: str | None = None  # Extracted role filter (user/assistant)
    workspace: str | None = None  # Extracted workspace filter
    title: str | None = None  # Extracted title filter
    repository: str | None = None  # Extracted repository filter
    edition: str | None = None  # Extracted edition filter (stable/insider/cli)
    start_date: str | None = None  # Extracted start date filter (yyyy-mm-dd format, inclusive)
    end_date: str | None = None  # Extracted end date filter (yyyy-mm-dd format, inclusive)


def _validate_date_format(date_str: str) -> str | None:
    """Validate date string is in yyyy-mm-dd format.

    Args:
        date_str: Date string to validate.

    Returns:
        The validated date string if valid, None otherwise.
    """
    if not date_str:
        return None
    # Check format: yyyy-mm-dd
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return None
    # Basic validation of month/day ranges
    try:
        _year, month, day = map(int, date_str.split("-"))
        if month < 1 or month > 12 or day < 1 or day > 31:
            return None
        return date_str
    except (ValueError, AttributeError):
        return None


def _escape_fts5_token(token: str) -> str:
    """Escape a single FTS5 token to prevent syntax errors.

    FTS5 has special operators like:
    - Dash (-) which means NOT
    - Colon (:) for column specification
    - Parentheses, brackets for grouping

    If a token is already quoted, leave it as-is.
    Otherwise, wrap it in quotes if it contains special characters.

    Args:
        token: A single search token (word or phrase).

    Returns:
        The escaped token, safe for FTS5 MATCH queries.
    """
    if not token:
        return token

    # If already quoted, leave as-is
    if token.startswith('"') and token.endswith('"'):
        return token

    # FTS5 special characters that need escaping:
    # - (dash/NOT operator), : (column spec), (, ), [, ]
    # Note: We don't need to escape * (prefix match) or ^ (first token)
    # as these are useful operators users might want to use
    special_chars = ["-", ":", "(", ")", "[", "]"]

    # Check if token contains any special characters
    if any(char in token for char in special_chars):
        # Escape internal quotes by doubling them (FTS5 convention)
        escaped = token.replace('"', '""')
        # Wrap in quotes
        return f'"{escaped}"'

    return token


def parse_search_query(query: str) -> ParsedQuery:
    """Parse a search query to extract field prefixes and convert to FTS5 format.

    Supports:
    - Multiple words: "python function" → matches both words (AND logic)
    - Exact phrases: '"python function"' → matches exact phrase
    - Field prefixes: 'role:user workspace:myproject title:something repository:github.com/owner/repo edition:cli'
    - Date filters: 'start_date:2024-01-01 end_date:2024-12-31' (yyyy-mm-dd format, inclusive)

    Args:
        query: The raw search query string.

    Returns:
        ParsedQuery with extracted field filters and FTS5 query string.
    """
    if not query or not query.strip():
        return ParsedQuery(fts_query="")

    query = query.strip()

    # Extract field prefixes (role:, workspace:, title:, repository:, edition:, start_date:, end_date:)
    role = None
    workspace = None
    title = None
    repository = None
    edition = None
    start_date = None
    end_date = None

    # Pattern for field:value (value can be quoted or unquoted)
    field_pattern = r'\b(role|workspace|title|repository|repo|edition|start_date|end_date):(?:"([^"]*)"|(\S+))'

    def extract_field(match):
        nonlocal role, workspace, title, repository, edition, start_date, end_date
        field_name = match.group(1).lower()
        # Value is either in group 2 (quoted) or group 3 (unquoted)
        value = match.group(2) if match.group(2) is not None else match.group(3)

        if field_name == "role":
            role = value.lower()
        elif field_name == "workspace":
            workspace = value
        elif field_name == "title":
            title = value
        elif field_name in ("repository", "repo"):
            repository = value
        elif field_name == "edition":
            edition = value.lower()
        elif field_name == "start_date":
            validated = _validate_date_format(value)
            if validated:
                start_date = validated
        elif field_name == "end_date":
            validated = _validate_date_format(value)
            if validated:
                end_date = validated

        return ""  # Remove the field prefix from the query

    # Remove field prefixes and extract their values
    remaining_query = re.sub(field_pattern, extract_field, query, flags=re.IGNORECASE)
    remaining_query = remaining_query.strip()

    # Now process the remaining query for FTS5
    # FTS5 by default uses AND for multiple terms, so we just need to handle:
    # 1. Quoted phrases (keep as-is)
    # 2. Unquoted words (escape special chars and join with spaces for implicit AND)

    if not remaining_query:
        fts_query = ""
    else:
        # Tokenize the query preserving quoted strings
        tokens = []
        # Pattern to match quoted strings or individual words
        token_pattern = r'"[^"]*"|[^\s"]+'

        for match in re.finditer(token_pattern, remaining_query):
            token = match.group(0)
            # Clean up any empty quotes
            if token == '""':
                continue
            # Escape FTS5 special characters in the token
            escaped_token = _escape_fts5_token(token)
            tokens.append(escaped_token)

        # Join tokens with space (FTS5 uses implicit AND)
        fts_query = " ".join(tokens)

    return ParsedQuery(
        fts_query=fts_query,
        role=role,
        workspace=workspace,
        title=title,
        repository=repository,
        edition=edition,
        start_date=start_date,
        end_date=end_date,
    )


# SQL LIKE pattern to detect ISO timestamp format (e.g., "2025-01-15T10:30:00Z")
# Pattern matches "YYYY-MM-DD" prefix which is common to all ISO timestamps
_ISO_TIMESTAMP_PATTERN = "____-__-__%"


def _build_date_filter_clause(start_date: str | None, end_date: str | None, date_column: str = "s.created_at") -> tuple[str, list]:
    """Build SQL WHERE clause fragments for date filtering.

    The created_at field can be:
    1. ISO timestamp string like "2025-01-15T10:30:00Z"
    2. Millisecond epoch timestamp like "1704067200000"

    This function handles both formats by using SQLite's date/datetime functions.

    Args:
        start_date: Start date in yyyy-mm-dd format (inclusive).
        end_date: End date in yyyy-mm-dd format (inclusive).
        date_column: The SQL column name to filter on.

    Returns:
        Tuple of (SQL clause string, list of parameters).
    """
    clauses = []
    params = []

    if start_date:
        # Handle both ISO timestamps and millisecond epochs
        # For ISO: compare directly using date extraction
        # For epoch ms: convert to date using SQLite's datetime functions
        clauses.append(f"""
            (
                (TYPEOF({date_column}) = 'text' AND (
                    ({date_column} LIKE '{_ISO_TIMESTAMP_PATTERN}' AND DATE(SUBSTR({date_column}, 1, 10)) >= ?) OR
                    ({date_column} NOT LIKE '{_ISO_TIMESTAMP_PATTERN}' AND DATE({date_column} / 1000, 'unixepoch') >= ?)
                ))
            )
        """)
        params.extend([start_date, start_date])

    if end_date:
        clauses.append(f"""
            (
                (TYPEOF({date_column}) = 'text' AND (
                    ({date_column} LIKE '{_ISO_TIMESTAMP_PATTERN}' AND DATE(SUBSTR({date_column}, 1, 10)) <= ?) OR
                    ({date_column} NOT LIKE '{_ISO_TIMESTAMP_PATTERN}' AND DATE({date_column} / 1000, 'unixepoch') <= ?)
                ))
            )
        """)
        params.extend([end_date, end_date])

    return " AND ".join(clauses) if clauses else "", params


# Allowed sort options with their SQL ORDER BY clauses (whitelist for security)
# Note: For relevance, we combine FTS5 rank (text relevance) with recency.
# FTS5 rank is negative (lower/more negative = better match).
# We subtract a recency bonus to make recent items more negative (better rank).
# The formula: rank - (days_since_2020 * 0.001) gives more weight to text relevance
# while providing a small boost to recent items.
_SORT_ORDER_CLAUSES = {
    "relevance": """ORDER BY (
        rank - 
        CASE 
            WHEN TYPEOF(s.created_at) = 'text' AND s.created_at LIKE '____-__-__%' 
            THEN (JULIANDAY(SUBSTR(s.created_at, 1, 10)) - JULIANDAY('2020-01-01')) * 0.001
            WHEN TYPEOF(s.created_at) = 'text'
            THEN (JULIANDAY(DATETIME(CAST(s.created_at AS REAL) / 1000, 'unixepoch')) - JULIANDAY('2020-01-01')) * 0.001
            ELSE 0
        END
    )""",
    "date": "ORDER BY s.created_at DESC",
}

from datetime import UTC

from .markdown_exporter import message_to_markdown
from .scanner import (
    ChatMessage,
    ChatSession,
    CommandRun,
    ContentBlock,
    FileChange,
    ToolInvocation,
)

CST_SCHEMA_VERSION = 3

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
    agent_nesting_level INTEGER DEFAULT 0
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
    description TEXT
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


class Database:
    """SQLite database for storing Copilot chat sessions.

    Uses FTS5 for full-text search (inspired by tad-hq/universal-session-viewer).

    Adds cst_* tables alongside the built-in Copilot CLI tables
    (sessions, turns, checkpoints, etc.) in ~/.copilot/session-store.db.
    """

    # List of cst_* tables that can be dropped and recreated
    DERIVED_TABLES: ClassVar[list[str]] = [
        "cst_messages_fts",  # FTS table must be dropped first
        "cst_content_blocks",
        "cst_command_runs",
        "cst_file_changes",
        "cst_tool_invocations",
        "cst_messages",
        "cst_sessions",
    ]

    # List of triggers that need to be dropped/recreated with derived tables
    DERIVED_TRIGGERS: ClassVar[list[str]] = [
        "cst_messages_ai",
        "cst_messages_ad",
        "cst_messages_au",
    ]

    def __init__(self, db_path: str | Path, *, unenriched_only: bool = False):
        """Initialize the database connection.

        Args:
            db_path: Path to the SQLite database file.
            unenriched_only: If True, disable cst_* table reads (use built-in tables only).
        """
        self.db_path = Path(db_path)
        self.unenriched_only = unenriched_only
        self._ensure_schema()
        self._check_builtin_schema_version()

    @contextmanager
    def _get_connection(self):
        """Get a database connection context manager.

        Verifies this is a valid session-store.db by checking for the
        built-in schema_version table before yielding the connection.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self):
        """Ensure the cst_* schema exists in the database.

        Creates cst_* tables and FTS triggers alongside the built-in
        Copilot CLI tables. Does NOT create or touch built-in tables.
        """
        with self._get_connection() as conn:
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
                    # v3: add enrichment_version column to cst_sessions
                    with contextlib.suppress(Exception):
                        cursor.execute("ALTER TABLE cst_sessions ADD COLUMN enrichment_version TEXT")
                if current_version < CST_SCHEMA_VERSION:
                    cursor.execute(
                        "UPDATE cst_schema_version SET version = ?",
                        (CST_SCHEMA_VERSION,),
                    )

    def _check_builtin_schema_version(self):
        """Warn if the built-in session store schema has been updated beyond our known version."""
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
                if row and row[0] > 1:
                    import warnings

                    warnings.warn(
                        f"Session store schema version {row[0]} is newer than expected (1). Some features may not work correctly. Consider updating copilot-session-tools.",
                        stacklevel=2,
                    )
        except Exception:  # noqa: S110
            pass  # Table might not exist if DB is not a session-store.db

    def has_cst_tables(self) -> bool:
        """Check if cst_* extension tables exist in the database."""
        if self.unenriched_only:
            return False
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='cst_sessions'").fetchone()
                return row[0] > 0
        except Exception:
            return False

    def discover_sessions_needing_enrichment(self) -> list[dict]:
        """Find CLI sessions needing enrichment by comparing built-in turns vs cst_messages.

        Compares built-in turns count against cst_messages user-role count for each session.
        Returns sessions where:
        - No cst_sessions row exists (new, never enriched)
        - Turn count differs (session has new messages since last enrichment)

        Uses direct sqlite_master probe (not has_cst_tables()) so this works
        correctly even when --unenriched-only is set — scan should still enrich.
        """
        with self._get_connection() as conn:
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

    def add_session(self, session: ChatSession) -> bool:
        """Add a chat session to the database.

        Args:
            session: The ChatSession to add.

        Returns:
            True if the session was added, False if it already exists.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Check if session already exists
            cursor.execute("SELECT session_id FROM cst_sessions WHERE session_id = ?", (session.session_id,))
            if cursor.fetchone():
                return False

            self._add_session_impl(cursor, session)
            return True

    def add_sessions_batch(self, sessions: list[ChatSession]) -> tuple[int, int]:
        """Add multiple sessions in a single transaction.

        Much faster than calling add_session() repeatedly as it uses
        a single connection and commit.

        Args:
            sessions: List of ChatSession objects to add.

        Returns:
            Tuple of (added_count, skipped_count).
        """
        added = 0
        skipped = 0

        with self._get_connection() as conn:
            cursor = conn.cursor()

            for session in sessions:
                # Check if session already exists
                cursor.execute("SELECT session_id FROM cst_sessions WHERE session_id = ?", (session.session_id,))
                if cursor.fetchone():
                    skipped += 1
                    continue

                # Add the session within this transaction
                self._add_session_impl(cursor, session)
                added += 1

        return added, skipped

    def _add_session_impl(self, cursor, session: ChatSession):
        """Internal implementation of add_session that uses an existing cursor.

        Used by add_sessions_batch for efficient batch inserts.
        """
        # Insert into cst_sessions table
        cursor.execute(
            """
            INSERT INTO cst_sessions 
            (session_id, workspace_name, workspace_path, created_at, updated_at, 
             source_file, vscode_edition, custom_title, requester_username, responder_username,
             source_file_mtime, source_file_size, type, repository_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.session_id,
                session.workspace_name,
                session.workspace_path,
                session.created_at,
                session.updated_at,
                session.source_file,
                session.vscode_edition,
                session.custom_title,
                session.requester_username,
                session.responder_username,
                session.source_file_mtime,
                session.source_file_size,
                session.type,
                session.repository_url,
            ),
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
                """
                INSERT INTO cst_messages 
                (session_id, message_index, role, content, timestamp, cached_markdown,
                 agent_id, agent_display_name, agent_nesting_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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
                    """
                    INSERT INTO cst_tool_invocations
                    (message_id, name, input, result, status, start_time, end_time,
                     source_type, invocation_message, subagent_invocation_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
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
                    ),
                )

            # Insert file changes
            for change in msg.file_changes:
                cursor.execute(
                    """
                    INSERT INTO cst_file_changes
                    (message_id, path, diff, content, explanation, language_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        change.path,
                        change.diff,
                        change.content,
                        change.explanation,
                        change.language_id,
                    ),
                )

            # Insert command runs
            for cmd in msg.command_runs:
                cursor.execute(
                    """
                    INSERT INTO cst_command_runs
                    (message_id, command, title, result, status, output, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        cmd.command,
                        cmd.title,
                        cmd.result,
                        cmd.status,
                        cmd.output,
                        cmd.timestamp,
                    ),
                )

            # Insert content blocks
            for block_idx, block in enumerate(msg.content_blocks):
                cursor.execute(
                    """
                    INSERT INTO cst_content_blocks
                    (message_id, block_index, kind, content, description)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        block_idx,
                        block.kind,
                        block.content,
                        block.description,
                    ),
                )

    def update_session(self, session: ChatSession):
        """Update an existing session or add it if it doesn't exist.

        Args:
            session: The ChatSession to update.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Delete existing session and related data atomically
            cursor.execute("DELETE FROM cst_messages_fts WHERE session_id = ?", (session.session_id,))
            cursor.execute("DELETE FROM cst_messages WHERE session_id = ?", (session.session_id,))
            cursor.execute("DELETE FROM cst_sessions WHERE session_id = ?", (session.session_id,))

            # Re-insert in the same transaction
            self._add_session_impl(cursor, session)

    def get_sessions_needing_reparse(self, current_parser_version: int) -> list[dict]:
        """Find cst_sessions with parser_version < current_parser_version."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cst_sessions'")
            if cursor.fetchone() is None:
                return []
            rows = conn.execute(
                "SELECT session_id, type, source_format, source_file, parser_version FROM cst_sessions WHERE parser_version < ?",
                (current_parser_version,),
            ).fetchall()
            return [dict(r) for r in rows]

            return [dict(r) for r in rows]

    def count_sessions_needing_version_refresh(self, current_version: str) -> int:
        """Count enriched sessions whose enrichment_version differs from current_version."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cst_sessions'")
            if cursor.fetchone() is None:
                return 0
            row = conn.execute(
                "SELECT COUNT(*) FROM cst_sessions WHERE enrichment_version IS NULL OR enrichment_version < ?",
                (current_version,),
            ).fetchone()
            return row[0] if row else 0

    def get_session_enrichment_version(self, session_id: str) -> str | None:
        """Get the enrichment_version for a specific session, or None if not enriched."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cst_sessions'")
            if cursor.fetchone() is None:
                return None
            row = conn.execute(
                "SELECT enrichment_version FROM cst_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return row[0] if row else None

    def delete_cst_session(self, session_id: str) -> bool:
        """Delete all cst_* data for a session. Returns True if session existed."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM cst_sessions WHERE session_id = ?", (session_id,))
            return cursor.rowcount > 0

    def needs_update(self, session_id: str, file_mtime: float | None, file_size: int | None) -> bool:
        """Check if a session needs to be updated based on file metadata.

        Returns True if:
        - Session doesn't exist, OR
        - Stored mtime/size is NULL (migration case), OR
        - Stored mtime/size differs from provided values

        Args:
            session_id: The session ID to check.
            file_mtime: The current file modification time.
            file_size: The current file size in bytes.

        Returns:
            True if the session needs to be updated, False otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT source_file_mtime, source_file_size FROM cst_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()

            if row is None:
                # Session doesn't exist
                return True

            stored_mtime = row[0]
            stored_size = row[1]

            # If stored values are NULL (migration case), session needs update
            if stored_mtime is None or stored_size is None:
                return True

            # Compare with provided values
            return stored_mtime != file_mtime or stored_size != file_size

    def needs_update_by_file(self, source_file: str, file_mtime: float, file_size: int) -> bool:
        """Check if a file needs to be parsed based on its metadata.

        This is more efficient than needs_update() as it doesn't require
        parsing the file first to get the session_id.

        Returns True if:
        - No session exists with this source_file, OR
        - Stored mtime/size differs from provided values

        Args:
            source_file: The path to the source file.
            file_mtime: The current file modification time.
            file_size: The current file size in bytes.

        Returns:
            True if the file needs to be parsed, False otherwise.
        """
        with self._get_connection() as conn:
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

    def get_all_file_metadata(self) -> dict[str, tuple[float, int, int]]:
        """Get all stored file metadata in one query.

        Returns a dict mapping source_file -> (mtime, size, session_count)
        for all sessions.  The *session_count* indicates how many sessions
        were parsed from that file (relevant for vscdb files which contain
        multiple sessions in a single file).
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT source_file, source_file_mtime, source_file_size, COUNT(*) FROM cst_sessions WHERE source_file IS NOT NULL GROUP BY source_file")
            return {row[0]: (row[1], row[2], row[3]) for row in cursor.fetchall()}

    def _reconstruct_message(self, cursor, message_id: int, msg_row) -> ChatMessage:
        """Reconstruct a ChatMessage from database rows by querying related tables."""
        # Query tool_invocations for this message
        cursor.execute("SELECT * FROM cst_tool_invocations WHERE message_id = ?", (message_id,))
        tool_invocations = []
        for t in cursor.fetchall():
            t_keys = t.keys()
            tool_invocations.append(
                ToolInvocation(
                    name=t["name"],
                    input=t["input"],
                    result=t["result"],
                    status=t["status"],
                    start_time=t["start_time"],
                    end_time=t["end_time"],
                    source_type=t["source_type"] if "source_type" in t_keys else None,
                    invocation_message=t["invocation_message"] if "invocation_message" in t_keys else None,
                    subagent_invocation_id=t["subagent_invocation_id"] if "subagent_invocation_id" in t_keys else None,
                )
            )

        # Query file_changes
        cursor.execute("SELECT * FROM cst_file_changes WHERE message_id = ?", (message_id,))
        file_changes = [
            FileChange(
                path=f["path"],
                diff=f["diff"],
                content=f["content"],
                explanation=f["explanation"],
                language_id=f["language_id"],
            )
            for f in cursor.fetchall()
        ]

        # Query command_runs
        cursor.execute("SELECT * FROM cst_command_runs WHERE message_id = ?", (message_id,))
        command_runs = [
            CommandRun(
                command=c["command"],
                title=c["title"],
                result=c["result"],
                status=c["status"],
                output=c["output"],
                timestamp=c["timestamp"],
            )
            for c in cursor.fetchall()
        ]

        # Query content_blocks
        cursor.execute("SELECT * FROM cst_content_blocks WHERE message_id = ? ORDER BY block_index", (message_id,))
        content_blocks = [
            ContentBlock(
                kind=b["kind"],
                content=b["content"],
                description=b["description"] if "description" in b.keys() else None,  # noqa: SIM118
            )
            for b in cursor.fetchall()
        ]

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
        )

    def get_session(self, session_id: str) -> ChatSession | None:
        """Get a session by ID. Checks cst_sessions first (enriched), falls back to built-in (unenriched).

        Args:
            session_id: The session ID to look up.

        Returns:
            ChatSession if found, None otherwise.
        """
        # Try enriched path first
        if self.has_cst_tables():
            session = self._get_cst_session(session_id)
            if session:
                return session

        # Fall back to built-in (unenriched)
        return self._get_builtin_session_as_chat_session(session_id)

    def _get_builtin_session_as_chat_session(self, session_id: str) -> ChatSession | None:
        """Convert built-in session/turns data to a ChatSession.

        Args:
            session_id: The session ID to look up in built-in tables.

        Returns:
            ChatSession if found, None otherwise.
        """
        session_data = self.get_builtin_session(session_id)
        if not session_data:
            return None

        turns = self.get_builtin_turns(session_id)
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

    def _get_cst_session(self, session_id: str) -> ChatSession | None:
        """Get a session from cst_* tables by its ID.

        Args:
            session_id: The session ID to look up.

        Returns:
            ChatSession if found, None otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM cst_sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if not row:
                return None

            # Get messages with their IDs for fetching related data
            cursor.execute(
                """
                SELECT id, role, content, timestamp, cached_markdown,
                       agent_id, agent_display_name, agent_nesting_level
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
                messages.append(self._reconstruct_message(cursor, message_id, msg_row))

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

    def get_messages_markdown(
        self,
        session_id: str,
        start: int | None = None,
        end: int | None = None,
        include_diffs: bool = True,
        include_tool_inputs: bool = True,
        include_thinking: bool = False,
    ) -> str:
        """Get markdown for specific messages or all messages in a session.

        Args:
            session_id: The session ID to get messages from.
            start: Optional 1-based start message index (inclusive).
            end: Optional 1-based end message index (inclusive).
            include_diffs: Whether to include file diffs in the markdown.
            include_tool_inputs: Whether to include tool inputs in the markdown.
            include_thinking: Whether to include thinking/reasoning blocks.

        Returns:
            Combined markdown string for the selected messages.
        """
        from .markdown_exporter import message_to_markdown

        with self._get_connection() as conn:
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

            # If both options are enabled, use cached markdown
            if include_diffs and include_tool_inputs and not include_thinking:
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
                    message = self._reconstruct_message(cursor, message_id, row)

                    # Generate markdown with specified options
                    md = message_to_markdown(
                        message,
                        message_number=message_index,
                        include_diffs=include_diffs,
                        include_tool_inputs=include_tool_inputs,
                        include_thinking=include_thinking,
                    )
                    markdown_parts.append(md)

            return "\n".join(markdown_parts)

    def list_sessions(
        self,
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
            workspace_name: Optional workspace name filter (cst rows only).
            limit: Maximum number of sessions to return.
            offset: Number of sessions to skip.
            session_type: Optional filter: 'cli', 'vscode', etc.

        Returns:
            List of session info dictionaries sorted by updated_at descending.
        """
        results: dict[str, dict] = {}

        # 1. Read from cst_sessions if available
        if self.has_cst_tables():
            with self._get_connection() as conn:
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

                query += " GROUP BY s.session_id ORDER BY last_message_at DESC, s.created_at DESC"

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
            builtin = self.list_builtin_sessions(limit=10000)
            # Batch-query turn counts for unenriched sessions
            unenriched_sids = [row["id"] for row in builtin if row["id"] not in results]
            turn_counts: dict[str, int] = {}
            if unenriched_sids:
                try:
                    with self._get_connection() as conn:
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

    def search(
        self,
        query: str,
        limit: int = 50,
        skip: int = 0,
        role: str | None = None,
        include_messages: bool = True,
        include_tool_calls: bool = True,
        include_file_changes: bool = True,
        session_title: str | None = None,
        sort_by: str = "relevance",
        repository: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        """Search messages using full-text search with field filtering.

        Supports advanced query syntax:
        - Multiple words: "python function" → matches both words (AND logic)
        - Exact phrases: '"python function"' → matches exact phrase
        - Field prefixes: 'role:user', 'workspace:myproject', 'title:something', 'repository:github.com/owner/repo'
        - Date filters: 'start_date:2024-01-01 end_date:2024-12-31' (yyyy-mm-dd format, inclusive)

        Args:
            query: The search query (supports field prefixes and quoted phrases).
            limit: Maximum number of results to return (top).
            skip: Number of results to skip (for pagination).
            role: Filter by message role ('user', 'assistant', or None for both).
                  Can also be specified in query as 'role:user' or 'role:assistant'.
            include_messages: Whether to search message content.
            include_tool_calls: Whether to also search tool invocations.
            include_file_changes: Whether to also search file changes.
            session_title: Filter by session title/workspace name.
                           Can also be specified in query as 'title:...' or 'workspace:...'.
            sort_by: Sort order - 'relevance' (default) or 'date'.
            repository: Filter by repository URL.
                        Can also be specified in query as 'repository:...' or 'repo:...'.
            start_date: Filter results on or after this date (yyyy-mm-dd format, inclusive).
                        Can also be specified in query as 'start_date:yyyy-mm-dd'.
            end_date: Filter results on or before this date (yyyy-mm-dd format, inclusive).
                      Can also be specified in query as 'end_date:yyyy-mm-dd'.

        Also queries the built-in search_index FTS table and merges results.

        Returns:
            List of matching messages with session info.
        """
        results = []
        builtin_results: dict[str, dict] = {}

        # Parse the query to extract field filters and convert to FTS5 format
        parsed = parse_search_query(query)

        # Use parsed field filters, with explicit parameters taking precedence
        effective_role = role if role else parsed.role
        effective_title = session_title if session_title else parsed.title
        effective_workspace = parsed.workspace  # Only from query parsing
        effective_repository = repository if repository else parsed.repository
        effective_edition = parsed.edition  # Only from query parsing
        effective_start_date = start_date if start_date else parsed.start_date
        effective_end_date = end_date if end_date else parsed.end_date

        # If no FTS query after parsing, we can't do FTS search
        # But we might still have field filters to apply
        fts_query = parsed.fts_query

        # Search built-in search_index FTS table for unenriched results
        if fts_query and include_messages:
            try:
                with self._get_connection() as conn:
                    rows = conn.execute(
                        "SELECT session_id, content, rank FROM search_index WHERE search_index MATCH ? ORDER BY rank LIMIT ?",
                        (fts_query, limit * 2),
                    ).fetchall()
                    for row in rows:
                        sid = row["session_id"]
                        if sid not in builtin_results:
                            builtin_results[sid] = {
                                "session_id": sid,
                                "content": (row["content"] or "")[:500],
                                "highlighted": (row["content"] or "")[:500],
                                "match_type": "builtin_search",
                                "is_enriched": False,
                                "rank": row["rank"],
                            }
            except Exception:  # noqa: S110
                pass  # search_index might not exist

        # Check if we have any filters to apply (even without FTS query)
        has_filters = effective_role or effective_title or effective_workspace or effective_repository or effective_edition or effective_start_date or effective_end_date

        # Get the safe order clause from whitelist (defaults to relevance)
        order_clause = _SORT_ORDER_CLAUSES.get(sort_by, _SORT_ORDER_CLAUSES["relevance"])

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Search messages (only if include_messages is True)
            if include_messages:
                if fts_query:
                    # FTS search with optional filters
                    message_query = """
                        SELECT 
                            m.id,
                            m.session_id,
                            m.message_index,
                            m.role,
                            m.content,
                            s.workspace_name,
                            s.custom_title,
                            s.created_at,
                            s.vscode_edition,
                            highlight(cst_messages_fts, 0, '<mark>', '</mark>') as highlighted,
                            'message' as match_type,
                            rank
                        FROM cst_messages_fts
                        JOIN cst_messages m ON cst_messages_fts.rowid = m.id
                        JOIN cst_sessions s ON m.session_id = s.session_id
                        WHERE cst_messages_fts MATCH ?
                    """
                    params = [fts_query]

                    if effective_role:
                        message_query += " AND m.role = ?"
                        params.append(effective_role)

                    if effective_title:
                        message_query += " AND (s.workspace_name LIKE ? OR s.custom_title LIKE ?)"
                        params.extend([f"%{effective_title}%", f"%{effective_title}%"])

                    if effective_workspace:
                        message_query += " AND s.workspace_name LIKE ?"
                        params.append(f"%{effective_workspace}%")

                    if effective_repository:
                        message_query += " AND s.repository_url LIKE ?"
                        params.append(f"%{effective_repository}%")

                    if effective_edition:
                        message_query += " AND s.vscode_edition = ?"
                        params.append(effective_edition)

                    # Add date filters
                    date_clause, date_params = _build_date_filter_clause(effective_start_date, effective_end_date, "s.created_at")
                    if date_clause:
                        message_query += f" AND {date_clause}"
                        params.extend(date_params)

                    # Note: order_clause is safe because it comes from _SORT_ORDER_CLAUSES whitelist

                    message_query += f" {order_clause} LIMIT ?"
                    params.append(limit + skip)

                    cursor.execute(message_query, params)
                    results.extend([dict(row) for row in cursor.fetchall()])

                elif has_filters:
                    # Filter-only query (no FTS, but with field filters)
                    message_query = """
                        SELECT 
                            m.id,
                            m.session_id,
                            m.message_index,
                            m.role,
                            m.content,
                            s.workspace_name,
                            s.custom_title,
                            s.created_at,
                            s.vscode_edition,
                            m.content as highlighted,
                            'message' as match_type
                        FROM cst_messages m
                        JOIN cst_sessions s ON m.session_id = s.session_id
                        WHERE 1=1
                    """
                    params = []

                    if effective_role:
                        message_query += " AND m.role = ?"
                        params.append(effective_role)

                    if effective_title:
                        message_query += " AND (s.workspace_name LIKE ? OR s.custom_title LIKE ?)"
                        params.extend([f"%{effective_title}%", f"%{effective_title}%"])

                    if effective_workspace:
                        message_query += " AND s.workspace_name LIKE ?"
                        params.append(f"%{effective_workspace}%")

                    if effective_repository:
                        message_query += " AND s.repository_url LIKE ?"
                        params.append(f"%{effective_repository}%")

                    if effective_edition:
                        message_query += " AND s.vscode_edition = ?"
                        params.append(effective_edition)

                    # Add date filters
                    date_clause, date_params = _build_date_filter_clause(effective_start_date, effective_end_date, "s.created_at")
                    if date_clause:
                        message_query += f" AND {date_clause}"
                        params.extend(date_params)

                    message_query += " ORDER BY s.created_at DESC LIMIT ?"
                    params.append(limit + skip)

                    cursor.execute(message_query, params)
                    results.extend([dict(row) for row in cursor.fetchall()])

            # Search tool invocations
            # For tool/file searches, we use the original query terms for LIKE matching
            search_terms = fts_query if fts_query else query
            if include_tool_calls and len(results) < limit and search_terms:
                remaining = limit - len(results)
                tool_query = """
                    SELECT 
                        t.id,
                        m.session_id,
                        'assistant' as role,
                        t.name || ': ' || COALESCE(t.input, '') || ' -> ' || COALESCE(t.result, '') as content,
                        s.workspace_name,
                        s.custom_title,
                        s.created_at,
                        s.vscode_edition,
                        t.name || ': ' || COALESCE(t.input, '') as highlighted,
                        'tool_invocation' as match_type
                    FROM cst_tool_invocations t
                    JOIN cst_messages m ON t.message_id = m.id
                    JOIN cst_sessions s ON m.session_id = s.session_id
                    WHERE (t.name LIKE ? OR t.input LIKE ? OR t.result LIKE ?)
                """
                params = [f"%{search_terms}%", f"%{search_terms}%", f"%{search_terms}%"]

                if effective_workspace:
                    tool_query += " AND s.workspace_name LIKE ?"
                    params.append(f"%{effective_workspace}%")

                if effective_title:
                    tool_query += " AND (s.workspace_name LIKE ? OR s.custom_title LIKE ?)"
                    params.extend([f"%{effective_title}%", f"%{effective_title}%"])

                if effective_repository:
                    tool_query += " AND s.repository_url LIKE ?"
                    params.append(f"%{effective_repository}%")

                if effective_edition:
                    tool_query += " AND s.vscode_edition = ?"
                    params.append(effective_edition)

                # Add date filters
                date_clause, date_params = _build_date_filter_clause(effective_start_date, effective_end_date, "s.created_at")
                if date_clause:
                    tool_query += f" AND {date_clause}"
                    params.extend(date_params)

                tool_query += " LIMIT ?"
                params.append(remaining)

                cursor.execute(tool_query, params)
                results.extend([dict(row) for row in cursor.fetchall()])

            # Search file changes
            if include_file_changes and len(results) < limit and search_terms:
                remaining = limit - len(results)
                file_query = """
                    SELECT 
                        f.id,
                        m.session_id,
                        'assistant' as role,
                        f.path || ': ' || COALESCE(f.explanation, '') as content,
                        s.workspace_name,
                        s.custom_title,
                        s.created_at,
                        s.vscode_edition,
                        f.path as highlighted,
                        'file_change' as match_type
                    FROM cst_file_changes f
                    JOIN cst_messages m ON f.message_id = m.id
                    JOIN cst_sessions s ON m.session_id = s.session_id
                    WHERE (f.path LIKE ? OR f.explanation LIKE ? OR f.diff LIKE ?)
                """
                params = [f"%{search_terms}%", f"%{search_terms}%", f"%{search_terms}%"]

                if effective_workspace:
                    file_query += " AND s.workspace_name LIKE ?"
                    params.append(f"%{effective_workspace}%")

                if effective_title:
                    file_query += " AND (s.workspace_name LIKE ? OR s.custom_title LIKE ?)"
                    params.extend([f"%{effective_title}%", f"%{effective_title}%"])

                if effective_repository:
                    file_query += " AND s.repository_url LIKE ?"
                    params.append(f"%{effective_repository}%")

                if effective_edition:
                    file_query += " AND s.vscode_edition = ?"
                    params.append(effective_edition)

                # Add date filters
                date_clause, date_params = _build_date_filter_clause(effective_start_date, effective_end_date, "s.created_at")
                if date_clause:
                    file_query += f" AND {date_clause}"
                    params.extend(date_params)

                file_query += " LIMIT ?"
                params.append(remaining)

                cursor.execute(file_query, params)
                results.extend([dict(row) for row in cursor.fetchall()])

        # Merge built-in search_index results that weren't covered by cst search
        cst_session_ids = {r["session_id"] for r in results}
        for sid, builtin_row in builtin_results.items():
            if sid not in cst_session_ids:
                results.append(builtin_row)

        # Apply skip/limit to merged results for correct pagination
        return results[skip : skip + limit]

    def get_workspaces(self) -> list[dict]:
        """Get all unique workspaces.

        Returns:
            List of workspace info dictionaries.
        """
        with self._get_connection() as conn:
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
                ORDER BY last_activity DESC
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_repositories(self) -> list[dict]:
        """Get all unique repositories.

        Returns:
            List of repository info dictionaries.
        """
        with self._get_connection() as conn:
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
                ORDER BY last_activity DESC
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_stats(self) -> dict:
        """Get database statistics.

        Returns:
            Dictionary with stats (combines enriched cst_* and built-in counts).
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cst_session_count = 0
            cst_message_count = 0
            workspace_count = 0
            editions: dict = {}

            if self.has_cst_tables():
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
                if self.has_cst_tables():
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

    def export_json(self) -> str:
        """Export all data as JSON.

        Returns:
            JSON string with all sessions and messages.
        """
        sessions = []
        for session_info in self.list_sessions():
            session = self.get_session(session_info["session_id"])
            if session:
                sessions.append(
                    {
                        "session_id": session.session_id,
                        "workspace_name": session.workspace_name,
                        "workspace_path": session.workspace_path,
                        "created_at": session.created_at,
                        "updated_at": session.updated_at,
                        "vscode_edition": session.vscode_edition,
                        "messages": [
                            {
                                "role": msg.role,
                                "content": msg.content,
                                "timestamp": msg.timestamp,
                            }
                            for msg in session.messages
                        ],
                    }
                )
        return json.dumps(sessions, indent=2)

    def optimize_fts(self) -> dict:
        """Optimize the FTS5 full-text search index for better query performance.

        This merges FTS index segments, reducing fragmentation and improving
        search speed. Should be run periodically, especially after bulk imports.

        Returns:
            Dictionary with optimization results including segment counts before/after.
        """
        with self._get_connection() as conn:
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

    def get_builtin_session(self, session_id: str) -> dict | None:
        """Read a session from the built-in sessions table."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT id, cwd, repository, branch, summary, created_at, updated_at FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                return dict(row) if row else None
        except sqlite3.OperationalError:
            return None

    def get_builtin_turns(self, session_id: str) -> list[dict]:
        """Read turns from the built-in turns table for a session."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT turn_index, user_message, assistant_response FROM turns WHERE session_id = ? ORDER BY turn_index",
                    (session_id,),
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def get_builtin_checkpoints(self, session_id: str) -> list[dict]:
        """Read checkpoints from the built-in checkpoints table."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT checkpoint_number, title, overview, history, work_done, technical_details, "
                    "important_files, next_steps FROM checkpoints WHERE session_id = ? ORDER BY checkpoint_number",
                    (session_id,),
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def get_builtin_files(self, session_id: str) -> list[dict]:
        """Read file references from the built-in session_files table."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT file_path, tool_name, turn_index, first_seen_at FROM session_files WHERE session_id = ?",
                    (session_id,),
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def get_builtin_refs(self, session_id: str) -> list[dict]:
        """Read refs from the built-in session_refs table."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT ref_type, ref_value, turn_index, created_at FROM session_refs WHERE session_id = ?",
                    (session_id,),
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def list_builtin_sessions(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """List sessions from the built-in sessions table."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, cwd, repository, branch, summary, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def count_builtin_turns(self, session_id: str) -> int:
        """Count turns for a session in the built-in turns table."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM turns WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0

    def enrich_session(self, session: ChatSession) -> None:
        """Write/update cst_* tables for a parsed ChatSession.

        Idempotent: deletes existing data for this session_id, then inserts fresh.

        Args:
            session: The parsed ChatSession to enrich.
        """
        from datetime import datetime

        from copilot_session_tools import __version__

        enriched_at = datetime.now(UTC).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Delete existing data for idempotency
            cursor.execute("DELETE FROM cst_messages_fts WHERE session_id = ?", (session.session_id,))
            cursor.execute("DELETE FROM cst_messages WHERE session_id = ?", (session.session_id,))
            cursor.execute("DELETE FROM cst_sessions WHERE session_id = ?", (session.session_id,))

            # Insert session
            cursor.execute(
                """
                INSERT INTO cst_sessions
                (session_id, workspace_name, workspace_path, created_at, updated_at,
                 source_file, vscode_edition, custom_title, requester_username, responder_username,
                 source_file_mtime, source_file_size, type, repository_url,
                 parser_version, source_format, enrichment_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.workspace_name,
                    session.workspace_path,
                    session.created_at,
                    session.updated_at or enriched_at,
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
                    __version__,
                ),
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
                    """
                    INSERT INTO cst_messages
                    (session_id, message_index, role, content, timestamp, cached_markdown,
                     agent_id, agent_display_name, agent_nesting_level)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
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
                    cursor.execute(
                        """
                        INSERT INTO cst_content_blocks
                        (message_id, block_index, kind, content, description)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (message_id, block_idx, block.kind, block.content, block.description),
                    )

                # Insert tool invocations
                for tool in msg.tool_invocations:
                    cursor.execute(
                        """
                        INSERT INTO cst_tool_invocations
                        (message_id, name, input, result, status, start_time, end_time,
                         source_type, invocation_message, subagent_invocation_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
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
                        ),
                    )

                # Insert command runs
                for cmd in msg.command_runs:
                    cursor.execute(
                        """
                        INSERT INTO cst_command_runs
                        (message_id, command, title, result, status, output, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            message_id,
                            cmd.command,
                            cmd.title,
                            cmd.result,
                            cmd.status,
                            cmd.output,
                            cmd.timestamp,
                        ),
                    )

                # Insert file changes
                for change in msg.file_changes:
                    cursor.execute(
                        """
                        INSERT INTO cst_file_changes
                        (message_id, path, diff, content, explanation, language_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            message_id,
                            change.path,
                            change.diff,
                            change.content,
                            change.explanation,
                            change.language_id,
                        ),
                    )

    def cleanup_orphaned_cst_sessions(self) -> list[str]:
        """Find and delete cst_sessions whose session_id doesn't exist in the built-in sessions table.

        Only targets CLI sessions (source_type='cli') since VS Code sessions
        only exist in cst_* tables.

        Returns:
            List of deleted session_ids.
        """
        with self._get_connection() as conn:
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
