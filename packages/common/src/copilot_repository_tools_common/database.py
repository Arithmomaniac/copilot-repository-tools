"""Database module for storing and querying Copilot chat sessions.

Schema design inspired by:
- tad-hq/universal-session-viewer: FTS5 full-text search
- jazzyalex/agent-sessions: SQLite indexing patterns

The schema has two parts:
1. raw_sessions - Stores compressed raw JSON as the source of truth for rebuilding
2. Derived tables (sessions, messages, etc.) - Can be dropped and recreated from raw_sessions
"""

import contextlib
import json
import re
import sqlite3
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import orjson


@dataclass
class ParsedQuery:
    """Represents a parsed search query with extracted field filters."""

    fts_query: str  # The FTS5 query string for content search
    role: str | None = None  # Extracted role filter (user/assistant)
    workspace: str | None = None  # Extracted workspace filter
    title: str | None = None  # Extracted title filter
    edition: str | None = None  # Extracted edition filter (stable/insider/cli)


def parse_search_query(query: str) -> ParsedQuery:
    """Parse a search query to extract field prefixes and convert to FTS5 format.

    Supports:
    - Multiple words: "python function" → matches both words (AND logic)
    - Exact phrases: '"python function"' → matches exact phrase
    - Field prefixes: 'role:user workspace:myproject title:something edition:cli'

    Args:
        query: The raw search query string.

    Returns:
        ParsedQuery with extracted field filters and FTS5 query string.
    """
    if not query or not query.strip():
        return ParsedQuery(fts_query="")

    query = query.strip()

    # Extract field prefixes (role:, workspace:, title:, edition:)
    role = None
    workspace = None
    title = None
    edition = None

    # Pattern for field:value (value can be quoted or unquoted)
    field_pattern = r'\b(role|workspace|title|edition):(?:"([^"]*)"|(\S+))'

    def extract_field(match):
        nonlocal role, workspace, title, edition
        field_name = match.group(1).lower()
        # Value is either in group 2 (quoted) or group 3 (unquoted)
        value = match.group(2) if match.group(2) is not None else match.group(3)

        if field_name == "role":
            role = value.lower()
        elif field_name == "workspace":
            workspace = value
        elif field_name == "title":
            title = value
        elif field_name == "edition":
            edition = value.lower()

        return ""  # Remove the field prefix from the query

    # Remove field prefixes and extract their values
    remaining_query = re.sub(field_pattern, extract_field, query, flags=re.IGNORECASE)
    remaining_query = remaining_query.strip()

    # Now process the remaining query for FTS5
    # FTS5 by default uses AND for multiple terms, so we just need to handle:
    # 1. Quoted phrases (keep as-is)
    # 2. Unquoted words (join with spaces for implicit AND)

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
            tokens.append(token)

        # Join tokens with space (FTS5 uses implicit AND)
        fts_query = " ".join(tokens)

    return ParsedQuery(
        fts_query=fts_query,
        role=role,
        workspace=workspace,
        title=title,
        edition=edition,
    )


# Allowed sort options with their SQL ORDER BY clauses (whitelist for security)
_SORT_ORDER_CLAUSES = {
    "relevance": "ORDER BY rank",
    "date": "ORDER BY s.created_at DESC",
}

from .markdown_exporter import message_to_markdown
from .scanner import (
    ChatMessage,
    ChatSession,
    CommandRun,
    ContentBlock,
    FileChange,
    ToolInvocation,
    _extract_session_from_dict,
)


class Database:
    """SQLite database for storing Copilot chat sessions.

    Uses FTS5 for full-text search (inspired by tad-hq/universal-session-viewer).

    The database has a two-layer design:
    1. raw_sessions table stores compressed raw JSON as the source of truth
    2. Derived tables (sessions, messages, etc.) can be dropped and rebuilt
    """

    # Schema for the raw data table - source of truth
    RAW_SCHEMA = """
    CREATE TABLE IF NOT EXISTS raw_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT UNIQUE NOT NULL,
        raw_json_compressed BLOB NOT NULL,
        workspace_name TEXT,
        workspace_path TEXT,
        source_file TEXT,
        vscode_edition TEXT DEFAULT 'stable',
        source_file_mtime REAL,
        source_file_size INTEGER,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_raw_sessions_session_id ON raw_sessions(session_id);
    CREATE INDEX IF NOT EXISTS idx_raw_sessions_workspace ON raw_sessions(workspace_name);
    """

    # Schema for derived tables that can be dropped and recreated
    DERIVED_SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT UNIQUE NOT NULL,
        workspace_name TEXT,
        workspace_path TEXT,
        created_at TEXT,
        updated_at TEXT,
        source_file TEXT,
        vscode_edition TEXT DEFAULT 'stable',
        custom_title TEXT,
        requester_username TEXT,
        responder_username TEXT,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source_file_mtime REAL,
        source_file_size INTEGER,
        type TEXT DEFAULT 'vscode'
    );

    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        message_index INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp TEXT,
        cached_markdown TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
    );

    -- Tool invocations table (from Arbuzov/copilot-chat-history types)
    CREATE TABLE IF NOT EXISTS tool_invocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        input TEXT,
        result TEXT,
        status TEXT,
        start_time INTEGER,
        end_time INTEGER,
        source_type TEXT,
        invocation_message TEXT,
        FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
    );

    -- File changes table (from Arbuzov/copilot-chat-history types)
    CREATE TABLE IF NOT EXISTS file_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER NOT NULL,
        path TEXT NOT NULL,
        diff TEXT,
        content TEXT,
        explanation TEXT,
        language_id TEXT,
        FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
    );

    -- Command runs table (from Arbuzov/copilot-chat-history types)
    CREATE TABLE IF NOT EXISTS command_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER NOT NULL,
        command TEXT NOT NULL,
        title TEXT,
        result TEXT,
        status TEXT,
        output TEXT,
        timestamp INTEGER,
        FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
    );

    -- Content blocks table for structured message content with kind (thinking, text, etc.)
    CREATE TABLE IF NOT EXISTS content_blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER NOT NULL,
        block_index INTEGER NOT NULL,
        kind TEXT NOT NULL DEFAULT 'text',
        content TEXT NOT NULL,
        description TEXT,
        FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace_name);
    CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at);
    CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
    CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);
    CREATE INDEX IF NOT EXISTS idx_tool_invocations_message ON tool_invocations(message_id);
    CREATE INDEX IF NOT EXISTS idx_file_changes_message ON file_changes(message_id);
    CREATE INDEX IF NOT EXISTS idx_command_runs_message ON command_runs(message_id);
    CREATE INDEX IF NOT EXISTS idx_content_blocks_message ON content_blocks(message_id);
    
    -- Full-text search for messages (FTS5 inspired by tad-hq/universal-session-viewer)
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        content,
        content='messages',
        content_rowid='id'
    );

    -- Triggers to keep FTS in sync
    CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
    END;

    CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content) 
        VALUES ('delete', old.id, old.content);
    END;

    CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content) 
        VALUES ('delete', old.id, old.content);
        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
    END;
    """

    # List of derived tables that can be dropped and recreated
    DERIVED_TABLES: ClassVar[list[str]] = [
        "messages_fts",  # FTS table must be dropped first
        "content_blocks",
        "command_runs",
        "file_changes",
        "tool_invocations",
        "messages",
        "sessions",
    ]

    # List of triggers that need to be dropped/recreated with derived tables
    DERIVED_TRIGGERS: ClassVar[list[str]] = ["messages_ai", "messages_ad", "messages_au"]

    # Compression level for zlib (0-9, 6 is a good balance of speed and compression)
    COMPRESSION_LEVEL = 6

    def __init__(self, db_path: str | Path):
        """Initialize the database connection.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = Path(db_path)
        self._ensure_schema()

    @contextmanager
    def _get_connection(self):
        """Get a database connection context manager."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self):
        """Ensure the database schema exists.

        With the new two-layer design:
        - raw_sessions is the source of truth (never needs migration)
        - Derived tables can be dropped and rebuilt, so no migrations needed
        """
        with self._get_connection() as conn:
            # Create raw_sessions table first (source of truth)
            conn.executescript(self.RAW_SCHEMA)
            # Create derived tables
            conn.executescript(self.DERIVED_SCHEMA)

    def add_session(self, session: ChatSession) -> bool:
        """Add a chat session to the database.

        Args:
            session: The ChatSession to add.

        Returns:
            True if the session was added, False if it already exists.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Check if session already exists in raw_sessions
            cursor.execute("SELECT id FROM raw_sessions WHERE session_id = ?", (session.session_id,))
            if cursor.fetchone():
                return False

            # Store compressed raw JSON in raw_sessions table
            if session.raw_json:
                compressed_json = zlib.compress(session.raw_json, level=self.COMPRESSION_LEVEL)
            else:
                # Create minimal JSON from session data if no raw JSON available
                compressed_json = zlib.compress(b"{}", level=self.COMPRESSION_LEVEL)

            cursor.execute(
                """
                INSERT INTO raw_sessions 
                (session_id, raw_json_compressed, workspace_name, workspace_path, 
                 source_file, vscode_edition, source_file_mtime, source_file_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    compressed_json,
                    session.workspace_name,
                    session.workspace_path,
                    session.source_file,
                    session.vscode_edition,
                    session.source_file_mtime,
                    session.source_file_size,
                ),
            )

            # Insert into derived sessions table
            cursor.execute(
                """
                INSERT INTO sessions 
                (session_id, workspace_name, workspace_path, created_at, updated_at, 
                 source_file, vscode_edition, custom_title, requester_username, responder_username,
                 source_file_mtime, source_file_size, type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )

            # Insert messages and associated data
            for idx, msg in enumerate(session.messages):
                # Generate cached markdown for this message (with diffs and inputs for full fidelity)
                cached_md = message_to_markdown(
                    msg,
                    message_number=idx + 1,
                    include_diffs=True,
                    include_tool_inputs=True,
                )

                cursor.execute(
                    """
                    INSERT INTO messages 
                    (session_id, message_index, role, content, timestamp, cached_markdown)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.session_id,
                        idx,
                        msg.role,
                        msg.content,
                        msg.timestamp,
                        cached_md,
                    ),
                )
                message_id = cursor.lastrowid

                # Insert tool invocations
                for tool in msg.tool_invocations:
                    cursor.execute(
                        """
                        INSERT INTO tool_invocations
                        (message_id, name, input, result, status, start_time, end_time, source_type, invocation_message)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        ),
                    )

                # Insert file changes
                for change in msg.file_changes:
                    cursor.execute(
                        """
                        INSERT INTO file_changes
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
                        INSERT INTO command_runs
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

                # Insert content blocks (for thinking/text differentiation)
                for block_idx, block in enumerate(msg.content_blocks):
                    cursor.execute(
                        """
                        INSERT INTO content_blocks
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

            return True

    def update_session(self, session: ChatSession):
        """Update an existing session or add it if it doesn't exist.

        Args:
            session: The ChatSession to update.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Delete from raw_sessions first (source of truth)
            cursor.execute("DELETE FROM raw_sessions WHERE session_id = ?", (session.session_id,))

            # Delete existing session and messages (cascades)
            cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session.session_id,))

        # Add the session (this will add to both raw_sessions and derived tables)
        self.add_session(session)

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
            # Check raw_sessions table (source of truth)
            cursor.execute(
                "SELECT source_file_mtime, source_file_size FROM raw_sessions WHERE session_id = ?",
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

    def get_session(self, session_id: str) -> ChatSession | None:
        """Get a session by its ID.

        Args:
            session_id: The session ID to look up.

        Returns:
            ChatSession if found, None otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if not row:
                return None

            # Get messages with their IDs for fetching related data
            cursor.execute(
                """
                SELECT id, role, content, timestamp, cached_markdown 
                FROM messages 
                WHERE session_id = ? 
                ORDER BY message_index
                """,
                (session_id,),
            )
            message_rows = cursor.fetchall()

            messages = []
            for msg_row in message_rows:
                message_id = msg_row["id"]
                # Safely get cached_markdown (may be NULL in older databases)
                cached_md = msg_row["cached_markdown"] if "cached_markdown" in msg_row.keys() else None  # noqa: SIM118

                # Get tool invocations for this message
                cursor.execute(
                    "SELECT * FROM tool_invocations WHERE message_id = ?",
                    (message_id,),
                )
                tool_invocations = []
                for t in cursor.fetchall():
                    # Handle columns that may not exist in older databases
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
                        )
                    )

                # Get file changes for this message
                cursor.execute(
                    "SELECT * FROM file_changes WHERE message_id = ?",
                    (message_id,),
                )
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

                # Get command runs for this message
                cursor.execute(
                    "SELECT * FROM command_runs WHERE message_id = ?",
                    (message_id,),
                )
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

                # Get content blocks for this message (for thinking/text differentiation)
                cursor.execute(
                    "SELECT * FROM content_blocks WHERE message_id = ? ORDER BY block_index",
                    (message_id,),
                )
                content_blocks = [
                    ContentBlock(
                        kind=b["kind"],
                        content=b["content"],
                        description=b["description"] if "description" in b.keys() else None,  # noqa: SIM118
                    )
                    for b in cursor.fetchall()
                ]

                messages.append(
                    ChatMessage(
                        role=msg_row["role"],
                        content=msg_row["content"],
                        timestamp=msg_row["timestamp"],
                        tool_invocations=tool_invocations,
                        file_changes=file_changes,
                        command_runs=command_runs,
                        content_blocks=content_blocks,
                        cached_markdown=cached_md,
                    )
                )

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
            )

    def get_messages_markdown(
        self,
        session_id: str,
        start: int | None = None,
        end: int | None = None,
        include_diffs: bool = True,
        include_tool_inputs: bool = True,
    ) -> str:
        """Get markdown for specific messages or all messages in a session.

        Args:
            session_id: The session ID to get messages from.
            start: Optional 1-based start message index (inclusive).
            end: Optional 1-based end message index (inclusive).
            include_diffs: Whether to include file diffs in the markdown.
            include_tool_inputs: Whether to include tool inputs in the markdown.

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
                    FROM messages 
                    WHERE session_id = ? AND message_index >= ? AND message_index <= ?
                    ORDER BY message_index
                    """,
                    (session_id, start_idx, end_idx),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, role, content, timestamp, cached_markdown, message_index
                    FROM messages 
                    WHERE session_id = ? 
                    ORDER BY message_index
                    """,
                    (session_id,),
                )

            rows = cursor.fetchall()
            markdown_parts = []

            # If both options are enabled, use cached markdown
            if include_diffs and include_tool_inputs:
                for row in rows:
                    md = row["cached_markdown"]
                    if md:
                        markdown_parts.append(md)
            else:
                # Need to regenerate markdown with specific options
                for row in rows:
                    message_id = row["id"]
                    message_index = row["message_index"] + 1  # Convert to 1-based

                    # Get tool invocations for this message
                    cursor.execute(
                        "SELECT * FROM tool_invocations WHERE message_id = ?",
                        (message_id,),
                    )
                    tool_invocations = []
                    for t in cursor.fetchall():
                        # Handle columns that may not exist in older databases
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
                            )
                        )

                    # Get file changes for this message
                    cursor.execute(
                        "SELECT * FROM file_changes WHERE message_id = ?",
                        (message_id,),
                    )
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

                    # Get command runs for this message
                    cursor.execute(
                        "SELECT * FROM command_runs WHERE message_id = ?",
                        (message_id,),
                    )
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

                    # Get content blocks for this message
                    cursor.execute(
                        "SELECT * FROM content_blocks WHERE message_id = ? ORDER BY block_index",
                        (message_id,),
                    )
                    content_blocks = [
                        ContentBlock(
                            kind=b["kind"],
                            content=b["content"],
                            description=b["description"] if "description" in b.keys() else None,  # noqa: SIM118
                        )
                        for b in cursor.fetchall()
                    ]

                    # Create message object
                    message = ChatMessage(
                        role=row["role"],
                        content=row["content"],
                        timestamp=row["timestamp"],
                        tool_invocations=tool_invocations,
                        file_changes=file_changes,
                        command_runs=command_runs,
                        content_blocks=content_blocks,
                    )

                    # Generate markdown with specified options
                    md = message_to_markdown(
                        message,
                        message_number=message_index,
                        include_diffs=include_diffs,
                        include_tool_inputs=include_tool_inputs,
                    )
                    markdown_parts.append(md)

            return "\n".join(markdown_parts)

    def list_sessions(
        self,
        workspace_name: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """List sessions with optional filtering.

        Args:
            workspace_name: Optional workspace name filter.
            limit: Maximum number of sessions to return.
            offset: Number of sessions to skip.

        Returns:
            List of session info dictionaries.
        """
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
                    COUNT(m.id) as message_count,
                    MAX(m.timestamp) as last_message_at,
                    (SELECT content FROM messages m2 
                     WHERE m2.session_id = s.session_id AND m2.role = 'user' 
                     ORDER BY m2.message_index LIMIT 1) as first_user_prompt
                FROM sessions s
                LEFT JOIN messages m ON s.session_id = m.session_id
            """
            params = []

            if workspace_name:
                query += " WHERE s.workspace_name = ?"
                params.append(workspace_name)

            query += " GROUP BY s.session_id ORDER BY last_message_at DESC, s.created_at DESC"

            if limit:
                query += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def search(
        self,
        query: str,
        limit: int = 50,
        role: str | None = None,
        include_messages: bool = True,
        include_tool_calls: bool = True,
        include_file_changes: bool = True,
        session_title: str | None = None,
        sort_by: str = "relevance",
    ) -> list[dict]:
        """Search messages using full-text search with field filtering.

        Supports advanced query syntax:
        - Multiple words: "python function" → matches both words (AND logic)
        - Exact phrases: '"python function"' → matches exact phrase
        - Field prefixes: 'role:user', 'workspace:myproject', 'title:something'

        Args:
            query: The search query (supports field prefixes and quoted phrases).
            limit: Maximum number of results to return.
            role: Filter by message role ('user', 'assistant', or None for both).
                  Can also be specified in query as 'role:user' or 'role:assistant'.
            include_messages: Whether to search message content.
            include_tool_calls: Whether to also search tool invocations.
            include_file_changes: Whether to also search file changes.
            session_title: Filter by session title/workspace name.
                           Can also be specified in query as 'title:...' or 'workspace:...'.
            sort_by: Sort order - 'relevance' (default) or 'date'.

        Returns:
            List of matching messages with session info.
        """
        results = []

        # Parse the query to extract field filters and convert to FTS5 format
        parsed = parse_search_query(query)

        # Use parsed field filters, with explicit parameters taking precedence
        effective_role = role if role else parsed.role
        effective_title = session_title if session_title else parsed.title
        effective_workspace = parsed.workspace  # Only from query parsing
        effective_edition = parsed.edition  # Only from query parsing

        # If no FTS query after parsing, we can't do FTS search
        # But we might still have field filters to apply
        fts_query = parsed.fts_query

        # Check if we have any filters to apply (even without FTS query)
        has_filters = effective_role or effective_title or effective_workspace or effective_edition

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
                            highlight(messages_fts, 0, '<mark>', '</mark>') as highlighted,
                            'message' as match_type,
                            rank
                        FROM messages_fts
                        JOIN messages m ON messages_fts.rowid = m.id
                        JOIN sessions s ON m.session_id = s.session_id
                        WHERE messages_fts MATCH ?
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

                    if effective_edition:
                        message_query += " AND s.vscode_edition = ?"
                        params.append(effective_edition)

                    # Note: order_clause is safe because it comes from _SORT_ORDER_CLAUSES whitelist

                    message_query += f" {order_clause} LIMIT ?"
                    params.append(limit)

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
                        FROM messages m
                        JOIN sessions s ON m.session_id = s.session_id
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

                    if effective_edition:
                        message_query += " AND s.vscode_edition = ?"
                        params.append(effective_edition)

                    message_query += " ORDER BY s.created_at DESC LIMIT ?"
                    params.append(limit)

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
                    FROM tool_invocations t
                    JOIN messages m ON t.message_id = m.id
                    JOIN sessions s ON m.session_id = s.session_id
                    WHERE (t.name LIKE ? OR t.input LIKE ? OR t.result LIKE ?)
                """
                params = [f"%{search_terms}%", f"%{search_terms}%", f"%{search_terms}%"]

                if effective_workspace:
                    tool_query += " AND s.workspace_name LIKE ?"
                    params.append(f"%{effective_workspace}%")

                if effective_title:
                    tool_query += " AND (s.workspace_name LIKE ? OR s.custom_title LIKE ?)"
                    params.extend([f"%{effective_title}%", f"%{effective_title}%"])

                if effective_edition:
                    tool_query += " AND s.vscode_edition = ?"
                    params.append(effective_edition)

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
                    FROM file_changes f
                    JOIN messages m ON f.message_id = m.id
                    JOIN sessions s ON m.session_id = s.session_id
                    WHERE (f.path LIKE ? OR f.explanation LIKE ? OR f.diff LIKE ?)
                """
                params = [f"%{search_terms}%", f"%{search_terms}%", f"%{search_terms}%"]

                if effective_workspace:
                    file_query += " AND s.workspace_name LIKE ?"
                    params.append(f"%{effective_workspace}%")

                if effective_title:
                    file_query += " AND (s.workspace_name LIKE ? OR s.custom_title LIKE ?)"
                    params.extend([f"%{effective_title}%", f"%{effective_title}%"])

                if effective_edition:
                    file_query += " AND s.vscode_edition = ?"
                    params.append(effective_edition)

                file_query += " LIMIT ?"
                params.append(remaining)

                cursor.execute(file_query, params)
                results.extend([dict(row) for row in cursor.fetchall()])

        return results[:limit]

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
                FROM sessions
                WHERE workspace_name IS NOT NULL
                GROUP BY workspace_name, workspace_path
                ORDER BY last_activity DESC
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_stats(self) -> dict:
        """Get database statistics.

        Returns:
            Dictionary with stats.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM sessions")
            session_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM messages")
            message_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT workspace_name) FROM sessions")
            workspace_count = cursor.fetchone()[0]

            cursor.execute("SELECT vscode_edition, COUNT(*) FROM sessions GROUP BY vscode_edition")
            editions = dict(cursor.fetchall())

            return {
                "session_count": session_count,
                "message_count": message_count,
                "workspace_count": workspace_count,
                "editions": editions,
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

    def rebuild_derived_tables(self, progress_callback=None) -> dict:
        """Drop and recreate all derived tables from raw_sessions.

        This method allows the schema to evolve without migrations - simply
        drop the derived tables and rebuild them from the raw JSON source.

        Args:
            progress_callback: Optional callable that receives (processed, total) counts.

        Returns:
            Dictionary with rebuild statistics.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Disable foreign keys temporarily for dropping tables
            conn.execute("PRAGMA foreign_keys = OFF")

            # Drop derived tables in order (FTS first, then dependent tables)
            # Note: DERIVED_TABLES is a class constant with hardcoded table names,
            # so f-string usage is safe. Validation is additional defense-in-depth.
            for table in self.DERIVED_TABLES:
                # Validate table name is alphanumeric with underscores only
                if not all(c.isalnum() or c == "_" for c in table):
                    raise ValueError(f"Invalid table name: {table}")
                with contextlib.suppress(sqlite3.OperationalError):
                    # FTS tables might need special handling
                    cursor.execute(f"DROP TABLE IF EXISTS {table}")

            # Drop triggers (validated against DERIVED_TRIGGERS list)
            # Note: DERIVED_TRIGGERS is a class constant with hardcoded trigger names,
            # so f-string usage is safe. Validation is additional defense-in-depth.
            for trigger in self.DERIVED_TRIGGERS:
                # Validate trigger name is alphanumeric with underscores only
                if not all(c.isalnum() or c == "_" for c in trigger):
                    raise ValueError(f"Invalid trigger name: {trigger}")
                cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")

            conn.commit()

            # Recreate derived tables schema
            conn.executescript(self.DERIVED_SCHEMA)
            conn.execute("PRAGMA foreign_keys = ON")

            # Count total raw sessions
            cursor.execute("SELECT COUNT(*) FROM raw_sessions")
            total_count = cursor.fetchone()[0]

            # Rebuild from raw_sessions
            cursor.execute("""
                SELECT session_id, raw_json_compressed, workspace_name, workspace_path,
                       source_file, vscode_edition, source_file_mtime, source_file_size
                FROM raw_sessions
            """)

            processed = 0
            errors = 0

            for row in cursor.fetchall():
                try:
                    _session_id = row[0]  # Session ID from DB, used for logging if needed
                    compressed_json = row[1]
                    workspace_name = row[2]
                    workspace_path = row[3]
                    source_file = row[4]
                    vscode_edition = row[5]
                    source_file_mtime = row[6]
                    source_file_size = row[7]

                    # Decompress and parse raw JSON
                    raw_json = zlib.decompress(compressed_json)
                    data = orjson.loads(raw_json)

                    # Re-parse session from raw JSON
                    session = _extract_session_from_dict(
                        data,
                        workspace_name=workspace_name,
                        workspace_path=workspace_path,
                        edition=vscode_edition,
                        source_file=source_file,
                        raw_json=raw_json,  # Keep raw JSON for consistency
                    )

                    if session:
                        # Override metadata from raw_sessions table
                        session.source_file_mtime = source_file_mtime
                        session.source_file_size = source_file_size

                        # Insert into derived tables only (not raw_sessions)
                        self._insert_derived_session(conn, session)

                    processed += 1

                    if progress_callback:
                        progress_callback(processed, total_count)

                except (zlib.error, orjson.JSONDecodeError, KeyError, TypeError):
                    # Log error for debugging, but continue processing other sessions
                    # These errors can occur when raw JSON is malformed or cannot be parsed
                    errors += 1
                    processed += 1

            conn.commit()

        return {
            "total": total_count,
            "processed": processed,
            "errors": errors,
        }

    def _insert_derived_session(self, conn, session: ChatSession):
        """Insert a session into derived tables only (not raw_sessions).

        This is an internal method used by rebuild_derived_tables.
        """
        cursor = conn.cursor()

        # Insert into sessions table
        cursor.execute(
            """
            INSERT INTO sessions 
            (session_id, workspace_name, workspace_path, created_at, updated_at, 
             source_file, vscode_edition, custom_title, requester_username, responder_username,
             source_file_mtime, source_file_size, type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )

        # Insert messages and associated data
        for idx, msg in enumerate(session.messages):
            # Generate cached markdown for this message
            cached_md = message_to_markdown(
                msg,
                message_number=idx + 1,
                include_diffs=True,
                include_tool_inputs=True,
            )

            cursor.execute(
                """
                INSERT INTO messages 
                (session_id, message_index, role, content, timestamp, cached_markdown)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    idx,
                    msg.role,
                    msg.content,
                    msg.timestamp,
                    cached_md,
                ),
            )
            message_id = cursor.lastrowid

            # Insert tool invocations
            for tool in msg.tool_invocations:
                cursor.execute(
                    """
                    INSERT INTO tool_invocations
                    (message_id, name, input, result, status, start_time, end_time, source_type, invocation_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )

            # Insert file changes
            for change in msg.file_changes:
                cursor.execute(
                    """
                    INSERT INTO file_changes
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
                    INSERT INTO command_runs
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
                    INSERT INTO content_blocks
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

    def get_raw_session_count(self) -> int:
        """Get the count of raw sessions stored in the database.

        Returns:
            Number of sessions in raw_sessions table.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM raw_sessions")
            return cursor.fetchone()[0]

    def get_raw_json(self, session_id: str) -> bytes | None:
        """Get the decompressed raw JSON for a specific session.

        Args:
            session_id: The session ID to retrieve.

        Returns:
            Raw JSON bytes if found, None otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT raw_json_compressed FROM raw_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if row:
                return zlib.decompress(row[0])
            return None
