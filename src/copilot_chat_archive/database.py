"""Database module for storing and querying Copilot chat sessions."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .scanner import ChatMessage, ChatSession


class Database:
    """SQLite database for storing Copilot chat sessions."""

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

    CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace_name);
    CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at);
    CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
    CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);
    
    -- Full-text search for messages
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

            # Insert session
            cursor.execute(
                """
                INSERT INTO sessions 
                (session_id, workspace_name, workspace_path, created_at, updated_at, 
                 source_file, vscode_edition)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.workspace_name,
                    session.workspace_path,
                    session.created_at,
                    session.updated_at,
                    session.source_file,
                    session.vscode_edition,
                ),
            )

            # Insert messages
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

            cursor.execute(
                """
                SELECT role, content, timestamp 
                FROM messages 
                WHERE session_id = ? 
                ORDER BY message_index
                """,
                (session_id,),
            )
            messages = [
                ChatMessage(role=r["role"], content=r["content"], timestamp=r["timestamp"])
                for r in cursor.fetchall()
            ]

            return ChatSession(
                session_id=row["session_id"],
                workspace_name=row["workspace_name"],
                workspace_path=row["workspace_path"],
                messages=messages,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                source_file=row["source_file"],
                vscode_edition=row["vscode_edition"],
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

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Search messages using full-text search.

        Args:
            query: The search query.
            limit: Maximum number of results to return.

        Returns:
            List of matching messages with session info.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT 
                    m.id,
                    m.session_id,
                    m.role,
                    m.content,
                    s.workspace_name,
                    s.created_at,
                    s.vscode_edition,
                    highlight(messages_fts, 0, '<mark>', '</mark>') as highlighted
                FROM messages_fts
                JOIN messages m ON messages_fts.rowid = m.id
                JOIN sessions s ON m.session_id = s.session_id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

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
