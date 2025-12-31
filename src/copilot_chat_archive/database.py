"""Database module for storing and querying Copilot chat sessions.

Schema design inspired by:
- tad-hq/universal-session-viewer: FTS5 full-text search
- jazzyalex/agent-sessions: SQLite indexing patterns
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .scanner import ChatMessage, ChatSession, ToolInvocation, FileChange, CommandRun, ContentBlock


class Database:
    """SQLite database for storing Copilot chat sessions.
    
    Uses FTS5 for full-text search (inspired by tad-hq/universal-session-viewer).
    """

    SCHEMA = """
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
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        message_index INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp TEXT,
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
        """Ensure the database schema exists."""
        with self._get_connection() as conn:
            conn.executescript(self.SCHEMA)

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
            cursor.execute(
                "SELECT id FROM sessions WHERE session_id = ?", (session.session_id,)
            )
            if cursor.fetchone():
                return False

            # Insert session with new fields
            cursor.execute(
                """
                INSERT INTO sessions 
                (session_id, workspace_name, workspace_path, created_at, updated_at, 
                 source_file, vscode_edition, custom_title, requester_username, responder_username)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )

            # Insert messages and associated data
            for idx, msg in enumerate(session.messages):
                cursor.execute(
                    """
                    INSERT INTO messages 
                    (session_id, message_index, role, content, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        session.session_id,
                        idx,
                        msg.role,
                        msg.content,
                        msg.timestamp,
                    ),
                )
                message_id = cursor.lastrowid

                # Insert tool invocations
                for tool in msg.tool_invocations:
                    cursor.execute(
                        """
                        INSERT INTO tool_invocations
                        (message_id, name, input, result, status, start_time, end_time)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            message_id,
                            tool.name,
                            tool.input,
                            tool.result,
                            tool.status,
                            tool.start_time,
                            tool.end_time,
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
                        (message_id, block_index, kind, content)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            message_id,
                            block_idx,
                            block.kind,
                            block.content,
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

            # Delete existing session and messages (cascades)
            cursor.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session.session_id,)
            )

        # Add the session
        self.add_session(session)

    def get_session(self, session_id: str) -> ChatSession | None:
        """Get a session by its ID.

        Args:
            session_id: The session ID to look up.

        Returns:
            ChatSession if found, None otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            # Get messages with their IDs for fetching related data
            cursor.execute(
                """
                SELECT id, role, content, timestamp 
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

                # Get tool invocations for this message
                cursor.execute(
                    "SELECT * FROM tool_invocations WHERE message_id = ?",
                    (message_id,),
                )
                tool_invocations = [
                    ToolInvocation(
                        name=t["name"],
                        input=t["input"],
                        result=t["result"],
                        status=t["status"],
                        start_time=t["start_time"],
                        end_time=t["end_time"],
                    )
                    for t in cursor.fetchall()
                ]

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
                    )
                    for b in cursor.fetchall()
                ]

                messages.append(ChatMessage(
                    role=msg_row["role"],
                    content=msg_row["content"],
                    timestamp=msg_row["timestamp"],
                    tool_invocations=tool_invocations,
                    file_changes=file_changes,
                    command_runs=command_runs,
                    content_blocks=content_blocks,
                ))

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
            )

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
                    COUNT(m.id) as message_count
                FROM sessions s
                LEFT JOIN messages m ON s.session_id = m.session_id
            """
            params = []

            if workspace_name:
                query += " WHERE s.workspace_name = ?"
                params.append(workspace_name)

            query += " GROUP BY s.session_id ORDER BY s.created_at DESC"

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
    ) -> list[dict]:
        """Search messages using full-text search with field filtering.

        Args:
            query: The search query.
            limit: Maximum number of results to return.
            role: Filter by message role ('user', 'assistant', or None for both).
            include_messages: Whether to search message content.
            include_tool_calls: Whether to also search tool invocations.
            include_file_changes: Whether to also search file changes.
            session_title: Filter by session title/workspace name.

        Returns:
            List of matching messages with session info.
        """
        results = []

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Search messages (only if include_messages is True)
            if include_messages:
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
                    'message' as match_type
                FROM messages_fts
                JOIN messages m ON messages_fts.rowid = m.id
                JOIN sessions s ON m.session_id = s.session_id
                WHERE messages_fts MATCH ?
            """
                params = [query]

                if role:
                    message_query += " AND m.role = ?"
                    params.append(role)

                if session_title:
                    message_query += " AND (s.workspace_name LIKE ? OR s.custom_title LIKE ?)"
                    params.extend([f"%{session_title}%", f"%{session_title}%"])

                message_query += " ORDER BY rank LIMIT ?"
                params.append(limit)

                cursor.execute(message_query, params)
                results.extend([dict(row) for row in cursor.fetchall()])

            # Search tool invocations
            if include_tool_calls and len(results) < limit:
                remaining = limit - len(results)
                cursor.execute(
                    """
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
                    WHERE t.name LIKE ? OR t.input LIKE ? OR t.result LIKE ?
                    LIMIT ?
                    """,
                    (f"%{query}%", f"%{query}%", f"%{query}%", remaining),
                )
                results.extend([dict(row) for row in cursor.fetchall()])

            # Search file changes
            if include_file_changes and len(results) < limit:
                remaining = limit - len(results)
                cursor.execute(
                    """
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
                    WHERE f.path LIKE ? OR f.explanation LIKE ? OR f.diff LIKE ?
                    LIMIT ?
                    """,
                    (f"%{query}%", f"%{query}%", f"%{query}%", remaining),
                )
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

            cursor.execute(
                "SELECT vscode_edition, COUNT(*) FROM sessions GROUP BY vscode_edition"
            )
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
                sessions.append({
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
                })
        return json.dumps(sessions, indent=2)
