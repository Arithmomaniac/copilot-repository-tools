"""Database module for storing and querying Copilot chat sessions.

Schema design inspired by:
- tad-hq/universal-session-viewer: FTS5 full-text search
- jazzyalex/agent-sessions: SQLite indexing patterns

The CST enrichment tables (cst_*) live in their own database file
(~/.copilot/copilot-session-tools.db).  The Copilot CLI's built-in
Chronicle database (~/.copilot/session-store.db) is optionally ATTACHed
read-only when needed for enrichment discovery and unenriched fallback.
"""

import contextlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

from . import db_storage
from .db_search import (  # noqa: F401 — re-exported for backward compatibility
    _ISO_TIMESTAMP_PATTERN,
    ParsedQuery,
    _build_date_filter_clause,
    parse_search_query,
)
from .db_storage import (  # noqa: F401 — re-exported for backward compatibility
    CST_FTS_SCHEMA,
    CST_SCHEMA,
    CST_SCHEMA_VERSION,
)
from .scanner import (
    ChatMessage,
    ChatSession,
)


class Database:
    """SQLite database for storing Copilot chat sessions.

    Uses FTS5 for full-text search (inspired by tad-hq/universal-session-viewer).

    CST enrichment tables live in their own database file.  The Copilot CLI's
    Chronicle database is optionally ATTACHed as ``chronicle`` for enrichment
    discovery and unenriched-session fallback.
    """

    # List of cst_* tables that can be dropped and recreated
    DERIVED_TABLES: ClassVar[list[str]] = [
        "cst_messages_fts",  # FTS table must be dropped first
        "cst_content_blocks",
        "cst_command_runs",
        "cst_file_changes",
        "cst_tool_invocations",
        "cst_root_agent_intervals",
        "cst_session_contexts",
        "cst_messages",
        "cst_sessions",
    ]

    # List of triggers that need to be dropped/recreated with derived tables
    DERIVED_TRIGGERS: ClassVar[list[str]] = [
        "cst_messages_ai",
        "cst_messages_ad",
        "cst_messages_au",
    ]

    def __init__(
        self,
        db_path: str | Path,
        *,
        unenriched_only: bool = False,
        chronicle_db_path: str | Path | None = None,
    ):
        """Initialize the database connection.

        Args:
            db_path: Path to the CST enrichment database file.
            unenriched_only: If True, disable cst_* table reads (use Chronicle tables only).
            chronicle_db_path: Path to the Copilot CLI Chronicle session-store.db.
                If ``None`` (default), auto-detects ``session-store.db`` in the
                same directory as *db_path*.
        """
        self.db_path = Path(db_path)
        self.unenriched_only = unenriched_only
        self._batch_conn: sqlite3.Connection | None = None

        # Auto-detect Chronicle DB as sibling of CST DB
        if chronicle_db_path is not None:
            self.chronicle_db_path: Path | None = Path(chronicle_db_path)
        else:
            candidate = self.db_path.parent / "session-store.db"
            # If db_path IS session-store.db (backward compat), use it as Chronicle too
            if candidate.resolve() == self.db_path.resolve():
                self.chronicle_db_path = self.db_path
            else:
                self.chronicle_db_path = candidate

        self._ensure_schema()

    @contextmanager
    def _get_connection(self):
        """Get a database connection context manager.

        If a :meth:`batch_connection` is active, reuses that connection
        (no commit/close — the batch context handles that).  Otherwise
        opens a fresh connection, commits on success, and closes.
        """
        if self._batch_conn is not None:
            yield self._batch_conn
            return
        conn = sqlite3.connect(str(self.db_path), uri=True)
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

    def _has_chronicle(self) -> bool:
        """Check whether the Chronicle DB file exists on disk."""
        return self.chronicle_db_path is not None and self.chronicle_db_path.is_file()

    def _attach_chronicle(self, conn: sqlite3.Connection) -> bool:
        """ATTACH the Chronicle DB as ``chronicle`` schema on *conn*.

        Uses a read-only URI to prevent accidental writes to Chronicle.
        The connection must be opened with ``uri=True`` for this to work.
        Returns True if attached successfully (or already attached),
        False if Chronicle DB is unavailable or attachment failed.
        """
        if not self._has_chronicle():
            return False
        # Check if chronicle is already attached (e.g., inside batch_connection)
        try:
            attached_dbs = [row[1] for row in conn.execute("PRAGMA database_list").fetchall()]
            if "chronicle" in attached_dbs:
                return True
        except sqlite3.Error:
            pass
        try:
            # ATTACH read-only via URI to prevent accidental writes.
            # Forward slashes required for URI paths on all platforms.
            chronicle_posix = str(self.chronicle_db_path).replace("\\", "/")
            conn.execute(f"ATTACH DATABASE 'file:{chronicle_posix}?mode=ro' AS chronicle")
            return True
        except sqlite3.Error:
            return False

    @staticmethod
    def _detach_chronicle(conn: sqlite3.Connection) -> None:
        """DETACH the ``chronicle`` schema from *conn*.  Safe to call even if not attached."""
        with contextlib.suppress(sqlite3.Error):
            conn.execute("DETACH DATABASE chronicle")

    @contextmanager
    def _get_chronicle_connection(self):
        """Get a connection with Chronicle ATTACHed (if available).

        Yields ``(conn, has_chronicle)`` so callers know whether
        ``chronicle.*`` tables are accessible.

        If a batch connection is active, ATTACHes on it.  DETACH is
        skipped inside a batch (SQLite rejects it mid-transaction);
        Chronicle stays attached for the batch's lifetime and is
        released when the batch connection closes.
        """
        if self._batch_conn is not None:
            attached = self._attach_chronicle(self._batch_conn)
            # Don't detach inside batch — DETACH fails mid-transaction,
            # and re-attach is idempotent (checked via pragma_database_list)
            yield self._batch_conn, attached
            return
        conn = sqlite3.connect(str(self.db_path), uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        attached = self._attach_chronicle(conn)
        try:
            yield conn, attached
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def batch_connection(self):
        """Hold a single connection open for multiple operations.

        While this context is active, all ``Database`` methods that normally
        open/commit/close per call will reuse the held connection instead.
        A single commit happens when the context exits successfully.

        Foreign key enforcement is disabled during batch operations for
        performance — with FK=ON, CASCADE checks on every DELETE/INSERT
        cause ~38x overhead on large databases.  Write paths explicitly
        delete child rows (via ``_delete_session_data``) so cascades are
        not needed.

        Not reentrant — raises RuntimeError if nested.

        Usage::

            with database.batch_connection():
                for session in sessions:
                    database.enrich_session(session)  # reuses one connection
        """
        if self._batch_conn is not None:
            raise RuntimeError("batch_connection() is not reentrant — already inside a batch")
        conn = sqlite3.connect(str(self.db_path), uri=True)
        conn.row_factory = sqlite3.Row
        # FK enforcement OFF for batch perf — CASCADE checks cause ~38x slowdown
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA busy_timeout = 5000")
        # 64 MB page cache — 27% faster bulk inserts vs default 2 MB
        conn.execute("PRAGMA cache_size = -65536")
        self._batch_conn = conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._batch_conn = None
            conn.close()

    def _ensure_schema(self):
        """Ensure the cst_* schema exists in the database."""
        with self._get_connection() as conn:
            db_storage.ensure_schema(conn)
        # Check Chronicle schema version (warns if incompatible)
        if self._has_chronicle():
            with self._get_chronicle_connection() as (conn, has_chronicle):
                if has_chronicle:
                    db_storage.check_builtin_schema_version(conn)

    def has_cst_tables(self) -> bool:
        """Check if cst_* extension tables exist in the database."""
        if self.unenriched_only:
            return False
        with self._get_connection() as conn:
            return db_storage.has_cst_tables(conn)

    def discover_sessions_needing_enrichment(self) -> list[dict]:
        """Find CLI sessions needing enrichment by comparing Chronicle turns vs cst_messages."""
        with self._get_chronicle_connection() as (conn, has_chronicle):
            return db_storage.discover_sessions_needing_enrichment(conn, has_chronicle=has_chronicle)

    def add_session(self, session: ChatSession) -> bool:
        """Add a chat session to the database.

        Returns True if the session was added, False if it already exists.
        """
        with self._get_connection() as conn:
            return db_storage.add_session(conn, session)

    def add_sessions_batch(self, sessions: list[ChatSession]) -> tuple[int, int]:
        """Add multiple sessions in a single transaction.

        Returns:
            Tuple of (added_count, skipped_count).
        """
        with self._get_connection() as conn:
            return db_storage.add_sessions_batch(conn, sessions)

    def _add_session_impl(self, cursor, session: ChatSession):
        """Delegate to db_storage.add_session_impl."""
        db_storage.add_session_impl(cursor, session)

    def update_session(self, session: ChatSession):
        """Update an existing session or add it if it doesn't exist."""
        with self._get_connection() as conn:
            db_storage.update_session(conn, session)

    def update_sessions_batch(self, sessions: list[ChatSession]) -> int:
        """Update multiple sessions in a single transaction.

        Returns:
            Number of sessions updated.
        """
        with self._get_connection() as conn:
            return db_storage.update_sessions_batch(conn, sessions)

    def get_sessions_needing_reparse(self, current_parser_version: int) -> list[dict]:
        """Find cst_sessions with parser_version < current_parser_version."""
        with self._get_connection() as conn:
            return db_storage.get_sessions_needing_reparse(conn, current_parser_version)

    def count_sessions_needing_version_refresh(self, current_version: str) -> int:
        """Count enriched sessions whose enrichment_version differs from current_version."""
        with self._get_connection() as conn:
            return db_storage.count_sessions_needing_version_refresh(conn, current_version)

    def get_sessions_needing_version_refresh(self, current_version: str) -> list[dict]:
        """Find enriched sessions whose enrichment_version is older than current_version."""
        with self._get_connection() as conn:
            return db_storage.get_sessions_needing_version_refresh(conn, current_version)

    def get_session_enrichment_version(self, session_id: str) -> str | None:
        """Get the enrichment_version for a specific session, or None if not enriched."""
        with self._get_connection() as conn:
            return db_storage.get_session_enrichment_version(conn, session_id)

    def update_enrichment_version(self, session_id: str, version: str) -> None:
        """Stamp a session's enrichment_version and parser_version, creating a stub row if needed."""
        with self._get_connection() as conn:
            db_storage.update_enrichment_version(conn, session_id, version)

    def delete_cst_session(self, session_id: str) -> bool:
        """Delete all cst_* data for a session. Returns True if session existed."""
        with self._get_connection() as conn:
            return db_storage.delete_cst_session(conn, session_id)

    def needs_update(self, session_id: str, file_mtime: float | None, file_size: int | None) -> bool:
        """Check if a session needs to be updated based on file metadata."""
        with self._get_connection() as conn:
            return db_storage.needs_update(conn, session_id, file_mtime, file_size)

    def needs_update_by_file(self, source_file: str, file_mtime: float, file_size: int) -> bool:
        """Check if a file needs to be parsed based on its metadata."""
        with self._get_connection() as conn:
            return db_storage.needs_update_by_file(conn, source_file, file_mtime, file_size)

    def get_all_file_metadata(self) -> dict[str, tuple[float, int, int]]:
        """Get all stored file metadata in one query.

        Returns a dict mapping source_file -> (mtime, size, session_count).
        """
        with self._get_connection() as conn:
            return db_storage.get_all_file_metadata(conn)

    def _reconstruct_message(self, cursor, message_id: int, msg_row) -> ChatMessage:
        """Reconstruct a ChatMessage from database rows by querying related tables."""
        from .db_retrieval import reconstruct_message

        msg, _ = reconstruct_message(cursor, message_id, msg_row, link_children=True)
        return msg

    def get_session(self, session_id: str) -> ChatSession | None:
        """Get a session by ID. Checks cst_sessions first (enriched), falls back to Chronicle (unenriched).

        Args:
            session_id: The session ID to look up.

        Returns:
            ChatSession if found, None otherwise.
        """
        from .db_retrieval import get_builtin_session_as_chat_session, get_cst_session

        # Try enriched path first
        if self.has_cst_tables():
            with self._get_connection() as conn:
                session = get_cst_session(conn, session_id)
                if session:
                    return session

        # Fall back to Chronicle (unenriched)
        with self._get_chronicle_connection() as (conn, has_chronicle):
            if has_chronicle:
                return get_builtin_session_as_chat_session(conn, session_id)
            return None

    def get_all_session_ids(self) -> list[str]:
        """Get all session IDs from cst_sessions.

        Returns:
            List of session ID strings.
        """
        if not self.has_cst_tables():
            return []
        from .db_retrieval import get_all_session_ids

        with self._get_connection() as conn:
            return get_all_session_ids(conn)

    def _get_builtin_session_as_chat_session(self, session_id: str) -> ChatSession | None:
        """Convert Chronicle session/turns data to a ChatSession."""
        from .db_retrieval import get_builtin_session_as_chat_session

        with self._get_chronicle_connection() as (conn, has_chronicle):
            if has_chronicle:
                return get_builtin_session_as_chat_session(conn, session_id)
            return None

    def _get_cst_session(self, session_id: str) -> ChatSession | None:
        """Get a session from cst_* tables by its ID."""
        from .db_retrieval import get_cst_session

        with self._get_connection() as conn:
            return get_cst_session(conn, session_id)

    def get_messages_markdown(
        self,
        session_id: str,
        start: int | None = None,
        end: int | None = None,
        content_set: set[str] | None = None,
    ) -> str:
        """Get markdown for specific messages or all messages in a session.

        Args:
            session_id: The session ID to get messages from.
            start: Optional 1-based start message index (inclusive).
            end: Optional 1-based end message index (inclusive).
            content_set: Controls which content types to include.

        Returns:
            Combined markdown string for the selected messages.
        """
        from .db_retrieval import get_messages_markdown

        with self._get_connection() as conn:
            return get_messages_markdown(conn, session_id, start=start, end=end, content_set=content_set)

    def list_sessions(
        self,
        workspace_name: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        session_type: str | None = None,
    ) -> list[dict]:
        """List sessions from cst_* tables and (optionally) Chronicle.

        Deduplicates by session_id — cst_sessions takes precedence over Chronicle.

        Args:
            workspace_name: Optional workspace name filter (cst rows only).
            limit: Maximum number of sessions to return.
            offset: Number of sessions to skip.
            session_type: Optional filter: 'cli', 'vscode', etc.

        Returns:
            List of session info dictionaries sorted by updated_at descending.
        """
        from .db_retrieval import list_sessions

        has_cst = self.has_cst_tables()
        with self._get_chronicle_connection() as (conn, has_chronicle):
            return list_sessions(
                conn,
                has_cst=has_cst,
                has_chronicle=has_chronicle,
                workspace_name=workspace_name,
                limit=limit,
                offset=offset,
                session_type=session_type,
            )

    def search(
        self,
        query: str,
        limit: int = 50,
        skip: int = 0,
        role: str | None = None,
        search_content_set: set[str] | None = None,
        include_messages: bool | None = None,
        include_tool_calls: bool | None = None,
        include_file_changes: bool | None = None,
        session_title: str | None = None,
        sort_by: str = "relevance",
        repository: str | list[str] | None = None,
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
            role: Filter by message role ('user', 'assistant', 'system', or None for non-system messages).
                  Can also be specified in query as 'role:user', 'role:assistant', or 'role:system'.
            search_content_set: Set of content type tokens controlling which
                data sources are searched.  See ``content_types.SEARCH_CONTENT_TYPES``.
            include_messages: Deprecated — use *search_content_set*.
            include_tool_calls: Deprecated — use *search_content_set*.
            include_file_changes: Deprecated — use *search_content_set*.
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
        from copilot_session_tools.content_types import SEARCH_DEFAULT_INCLUDES

        from .db_search import execute_search

        # Backward compat: build set from old booleans if provided
        if search_content_set is None and any(x is not None for x in [include_messages, include_tool_calls, include_file_changes]):
            search_content_set = set()
            if include_messages is not False:
                search_content_set.add("messages")
            if include_tool_calls is not False:
                search_content_set.update(["tools", "tool-inputs"])
            if include_file_changes is not False:
                search_content_set.update(["file-changes", "diffs"])
            search_content_set.update(["thinking", "agent-details", "commands"])

        if search_content_set is None:
            search_content_set = SEARCH_DEFAULT_INCLUDES

        if not search_content_set:
            return []

        with self._get_chronicle_connection() as (conn, has_chronicle):
            return execute_search(
                conn,
                query,
                limit=limit,
                skip=skip,
                role=role,
                search_content_set=search_content_set,
                session_title=session_title,
                sort_by=sort_by,
                repository=repository,
                start_date=start_date,
                end_date=end_date,
                has_chronicle=has_chronicle,
            )

    def get_workspaces(self) -> list[dict]:
        """Get all unique workspaces."""
        from .db_retrieval import get_workspaces

        with self._get_connection() as conn:
            return get_workspaces(conn)

    def get_repositories(self) -> list[dict]:
        """Get all unique repositories."""
        from .db_retrieval import get_repositories

        with self._get_connection() as conn:
            return get_repositories(conn)

    def get_stats(self) -> dict:
        """Get database statistics."""
        from .db_retrieval import get_stats

        has_cst = self.has_cst_tables()
        with self._get_chronicle_connection() as (conn, has_chronicle):
            return get_stats(conn, has_cst=has_cst, has_chronicle=has_chronicle)

    def export_json(self) -> str:
        """Export all data as JSON."""
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
        """Optimize the FTS5 full-text search index."""
        from .db_retrieval import optimize_fts

        with self._get_connection() as conn:
            return optimize_fts(conn)

    def get_builtin_session(self, session_id: str) -> dict | None:
        """Read a session from the Chronicle sessions table."""
        from .db_retrieval import get_builtin_session

        with self._get_chronicle_connection() as (conn, has_chronicle):
            if has_chronicle:
                return get_builtin_session(conn, session_id)
            return None

    def get_builtin_turns(self, session_id: str) -> list[dict]:
        """Read turns from the Chronicle turns table for a session."""
        from .db_retrieval import get_builtin_turns

        with self._get_chronicle_connection() as (conn, has_chronicle):
            if has_chronicle:
                return get_builtin_turns(conn, session_id)
            return []

    def get_builtin_checkpoints(self, session_id: str) -> list[dict]:
        """Read checkpoints from the Chronicle checkpoints table."""
        from .db_retrieval import get_builtin_checkpoints

        with self._get_chronicle_connection() as (conn, has_chronicle):
            if has_chronicle:
                return get_builtin_checkpoints(conn, session_id)
            return []

    def get_builtin_files(self, session_id: str) -> list[dict]:
        """Read file references from the Chronicle session_files table."""
        from .db_retrieval import get_builtin_files

        with self._get_chronicle_connection() as (conn, has_chronicle):
            if has_chronicle:
                return get_builtin_files(conn, session_id)
            return []

    def get_builtin_refs(self, session_id: str) -> list[dict]:
        """Read refs from the Chronicle session_refs table."""
        from .db_retrieval import get_builtin_refs

        with self._get_chronicle_connection() as (conn, has_chronicle):
            if has_chronicle:
                return get_builtin_refs(conn, session_id)
            return []

    def list_builtin_sessions(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """List sessions from the Chronicle sessions table."""
        from .db_retrieval import list_builtin_sessions

        with self._get_chronicle_connection() as (conn, has_chronicle):
            if has_chronicle:
                return list_builtin_sessions(conn, limit=limit, offset=offset)
            return []

    def count_builtin_turns(self, session_id: str) -> int:
        """Count turns for a session in the Chronicle turns table."""
        from .db_retrieval import count_builtin_turns

        with self._get_chronicle_connection() as (conn, has_chronicle):
            if has_chronicle:
                return count_builtin_turns(conn, session_id)
            return 0

    def enrich_session(self, session: ChatSession) -> None:
        """Write/update cst_* tables for a parsed ChatSession.

        Idempotent: deletes existing data for this session_id, then inserts fresh.
        """
        with self._get_connection() as conn:
            db_storage.enrich_session(conn, session)

    def cleanup_orphaned_cst_sessions(self) -> list[str]:
        """Find and delete cst_sessions whose session_id doesn't exist in the Chronicle sessions table."""
        with self._get_chronicle_connection() as (conn, has_chronicle):
            return db_storage.cleanup_orphaned_cst_sessions(conn, has_chronicle=has_chronicle)
