"""Tests for the database module."""

import json
import tempfile
from pathlib import Path

import pytest

from copilot_repository_tools_common import Database, ChatMessage, ChatSession


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = Database(db_path)
    yield db
    # Cleanup
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def sample_session():
    """Create a sample chat session for testing."""
    return ChatSession(
        session_id="test-session-123",
        workspace_name="my-project",
        workspace_path="/home/user/projects/my-project",
        messages=[
            ChatMessage(role="user", content="How do I create a Python function?"),
            ChatMessage(
                role="assistant",
                content="Here's how to create a Python function:\n\n```python\ndef my_function():\n    pass\n```",
            ),
            ChatMessage(role="user", content="Thanks! Can you add parameters?"),
            ChatMessage(
                role="assistant",
                content="Sure! Here's a function with parameters:\n\n```python\ndef my_function(name, age=18):\n    return f'{name} is {age} years old'\n```",
            ),
        ],
        created_at="2025-01-15T10:30:00Z",
        updated_at="2025-01-15T10:35:00Z",
        source_file="/path/to/session.json",
        vscode_edition="stable",
    )


class TestDatabase:
    """Tests for the Database class."""

    def test_create_database(self, temp_db):
        """Test that database is created with correct schema."""
        assert temp_db.db_path.exists()
        stats = temp_db.get_stats()
        assert stats["session_count"] == 0
        assert stats["message_count"] == 0

    def test_add_session(self, temp_db, sample_session):
        """Test adding a session to the database."""
        result = temp_db.add_session(sample_session)
        assert result is True

        stats = temp_db.get_stats()
        assert stats["session_count"] == 1
        assert stats["message_count"] == 4

    def test_add_duplicate_session(self, temp_db, sample_session):
        """Test that adding a duplicate session returns False."""
        temp_db.add_session(sample_session)
        result = temp_db.add_session(sample_session)
        assert result is False

        stats = temp_db.get_stats()
        assert stats["session_count"] == 1

    def test_get_session(self, temp_db, sample_session):
        """Test retrieving a session from the database."""
        temp_db.add_session(sample_session)
        retrieved = temp_db.get_session(sample_session.session_id)

        assert retrieved is not None
        assert retrieved.session_id == sample_session.session_id
        assert retrieved.workspace_name == sample_session.workspace_name
        assert len(retrieved.messages) == len(sample_session.messages)
        assert retrieved.messages[0].role == "user"
        assert "Python function" in retrieved.messages[0].content

    def test_get_nonexistent_session(self, temp_db):
        """Test that getting a nonexistent session returns None."""
        result = temp_db.get_session("nonexistent-id")
        assert result is None

    def test_list_sessions(self, temp_db, sample_session):
        """Test listing sessions."""
        temp_db.add_session(sample_session)

        # Add another session
        session2 = ChatSession(
            session_id="test-session-456",
            workspace_name="other-project",
            workspace_path="/home/user/projects/other",
            messages=[ChatMessage(role="user", content="Hello")],
            vscode_edition="insider",
        )
        temp_db.add_session(session2)

        sessions = temp_db.list_sessions()
        assert len(sessions) == 2

    def test_list_sessions_filter_by_workspace(self, temp_db, sample_session):
        """Test filtering sessions by workspace."""
        temp_db.add_session(sample_session)

        session2 = ChatSession(
            session_id="test-session-456",
            workspace_name="other-project",
            workspace_path="/home/user/projects/other",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        temp_db.add_session(session2)

        sessions = temp_db.list_sessions(workspace_name="my-project")
        assert len(sessions) == 1
        assert sessions[0]["workspace_name"] == "my-project"

    def test_search_messages(self, temp_db, sample_session):
        """Test full-text search."""
        temp_db.add_session(sample_session)

        results = temp_db.search("Python function")
        assert len(results) > 0
        assert any("Python" in r["content"] for r in results)

    def test_search_no_results(self, temp_db, sample_session):
        """Test search with no matching results."""
        temp_db.add_session(sample_session)

        results = temp_db.search("JavaScript React")
        assert len(results) == 0

    def test_get_workspaces(self, temp_db, sample_session):
        """Test getting unique workspaces."""
        temp_db.add_session(sample_session)

        session2 = ChatSession(
            session_id="test-session-456",
            workspace_name="my-project",  # Same workspace
            workspace_path="/home/user/projects/my-project",
            messages=[ChatMessage(role="user", content="Another question")],
        )
        temp_db.add_session(session2)

        workspaces = temp_db.get_workspaces()
        assert len(workspaces) == 1
        assert workspaces[0]["workspace_name"] == "my-project"
        assert workspaces[0]["session_count"] == 2

    def test_export_json(self, temp_db, sample_session):
        """Test exporting database as JSON."""
        temp_db.add_session(sample_session)

        json_str = temp_db.export_json()
        data = json.loads(json_str)

        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["session_id"] == sample_session.session_id
        assert len(data[0]["messages"]) == 4

    def test_update_session(self, temp_db, sample_session):
        """Test updating an existing session."""
        temp_db.add_session(sample_session)

        # Modify the session
        updated_session = ChatSession(
            session_id=sample_session.session_id,
            workspace_name=sample_session.workspace_name,
            workspace_path=sample_session.workspace_path,
            messages=[
                ChatMessage(role="user", content="Updated message"),
            ],
            vscode_edition="stable",
        )

        temp_db.update_session(updated_session)

        retrieved = temp_db.get_session(sample_session.session_id)
        assert len(retrieved.messages) == 1
        assert retrieved.messages[0].content == "Updated message"


class TestNeedsUpdate:
    """Tests for the needs_update method."""

    def test_needs_update_new_session(self, temp_db):
        """Test that needs_update returns True for a new session."""
        result = temp_db.needs_update("nonexistent-session", 1234567890.0, 1024)
        assert result is True

    def test_needs_update_unchanged_session(self, temp_db):
        """Test that needs_update returns False for an unchanged session."""
        session = ChatSession(
            session_id="unchanged-session",
            workspace_name="test-workspace",
            workspace_path="/test/path",
            messages=[ChatMessage(role="user", content="Hello")],
            source_file="/test/session.json",
            source_file_mtime=1234567890.0,
            source_file_size=1024,
        )
        temp_db.add_session(session)

        result = temp_db.needs_update("unchanged-session", 1234567890.0, 1024)
        assert result is False

    def test_needs_update_modified_mtime(self, temp_db):
        """Test that needs_update returns True when mtime differs."""
        session = ChatSession(
            session_id="mtime-changed-session",
            workspace_name="test-workspace",
            workspace_path="/test/path",
            messages=[ChatMessage(role="user", content="Hello")],
            source_file="/test/session.json",
            source_file_mtime=1234567890.0,
            source_file_size=1024,
        )
        temp_db.add_session(session)

        # mtime changed
        result = temp_db.needs_update("mtime-changed-session", 1234567999.0, 1024)
        assert result is True

    def test_needs_update_modified_size(self, temp_db):
        """Test that needs_update returns True when size differs."""
        session = ChatSession(
            session_id="size-changed-session",
            workspace_name="test-workspace",
            workspace_path="/test/path",
            messages=[ChatMessage(role="user", content="Hello")],
            source_file="/test/session.json",
            source_file_mtime=1234567890.0,
            source_file_size=1024,
        )
        temp_db.add_session(session)

        # size changed
        result = temp_db.needs_update("size-changed-session", 1234567890.0, 2048)
        assert result is True

    def test_needs_update_null_stored_values(self, temp_db):
        """Test that needs_update returns True when stored values are NULL (migration case)."""
        session = ChatSession(
            session_id="null-values-session",
            workspace_name="test-workspace",
            workspace_path="/test/path",
            messages=[ChatMessage(role="user", content="Hello")],
            source_file="/test/session.json",
            # No mtime/size set (simulating migration case)
            source_file_mtime=None,
            source_file_size=None,
        )
        temp_db.add_session(session)

        result = temp_db.needs_update("null-values-session", 1234567890.0, 1024)
        assert result is True

    def test_session_stores_file_metadata(self, temp_db):
        """Test that file metadata is stored and retrieved correctly."""
        session = ChatSession(
            session_id="metadata-session",
            workspace_name="test-workspace",
            workspace_path="/test/path",
            messages=[ChatMessage(role="user", content="Hello")],
            source_file="/test/session.json",
            source_file_mtime=1234567890.123,
            source_file_size=2048,
        )
        temp_db.add_session(session)

        retrieved = temp_db.get_session("metadata-session")
        assert retrieved.source_file_mtime == 1234567890.123
        assert retrieved.source_file_size == 2048


class TestCLISupport:
    """Tests for CLI session support in database."""

    def test_add_cli_session(self, tmp_path):
        """Test adding a CLI session to database."""
        from copilot_repository_tools_common import Database, ChatSession, ChatMessage
        
        db = Database(tmp_path / "test.db")
        
        # Create a CLI session
        session = ChatSession(
            session_id="cli-test-123",
            workspace_name=None,
            workspace_path=None,
            messages=[
                ChatMessage(role="user", content="Hello from CLI"),
                ChatMessage(role="assistant", content="Hi there!"),
            ],
            type="cli",
        )
        
        # Add session
        result = db.add_session(session)
        assert result is True
        
        # Retrieve session
        retrieved = db.get_session("cli-test-123")
        assert retrieved is not None
        assert retrieved.type == "cli"
        assert retrieved.session_id == "cli-test-123"
        assert len(retrieved.messages) == 2

    def test_vscode_session_type_default(self, tmp_path):
        """Test that VS Code sessions default to 'vscode' type."""
        from copilot_repository_tools_common import Database, ChatSession, ChatMessage
        
        db = Database(tmp_path / "test.db")
        
        # Create a session without explicit type (should default to vscode)
        session = ChatSession(
            session_id="vscode-test-456",
            workspace_name="test-workspace",
            workspace_path="/path/to/workspace",
            messages=[
                ChatMessage(role="user", content="Hello from VS Code"),
            ],
        )
        
        # Add session
        db.add_session(session)
        
        # Retrieve session
        retrieved = db.get_session("vscode-test-456")
        assert retrieved is not None
        assert retrieved.type == "vscode"

    def test_cli_session_full_workflow(self, tmp_path):
        """Test the full workflow: parse CLI file, add to DB, retrieve."""
        from copilot_repository_tools_common.scanner import _parse_cli_jsonl_file
        from pathlib import Path
        
        # Use the real sample CLI file with event-based format
        sample_file = Path(__file__).parent / "sample_files" / "66b821d4-af6f-4518-a394-6d95a4d0f96b.jsonl"
        
        if not sample_file.exists():
            pytest.skip("Real CLI sample file not found")
        
        # Parse CLI file
        session = _parse_cli_jsonl_file(sample_file)
        assert session is not None
        assert session.session_id == "66b821d4-af6f-4518-a394-6d95a4d0f96b"
        
        # Add to database
        db = Database(tmp_path / "test.db")
        added = db.add_session(session)
        assert added is True
        
        # Retrieve and verify
        retrieved = db.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.type == "cli"
        assert len(retrieved.messages) > 0
        
        # Verify search works - search for content from the session
        results = db.search("branches")
        assert len(results) > 0


class TestSortingBehavior:
    """Tests for session sorting behavior."""

    def test_list_sessions_sorted_by_recent_message(self, tmp_path):
        """Test that sessions are sorted by most recent message timestamp."""
        from datetime import datetime, timedelta
        
        db = Database(tmp_path / "test.db")
        
        # Create base time
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        
        # Session 1: Created first, but has most recent message
        session1 = ChatSession(
            session_id="session-1",
            workspace_name="test",
            workspace_path="/test",
            messages=[
                ChatMessage(
                    role="user",
                    content="Old message",
                    timestamp=(base_time + timedelta(hours=1)).isoformat()
                ),
                ChatMessage(
                    role="assistant",
                    content="Recent message",
                    timestamp=(base_time + timedelta(hours=10)).isoformat()  # Most recent
                ),
            ],
            created_at=(base_time + timedelta(hours=0)).isoformat(),
        )
        
        # Session 2: Created second, older messages
        session2 = ChatSession(
            session_id="session-2",
            workspace_name="test",
            workspace_path="/test",
            messages=[
                ChatMessage(
                    role="user",
                    content="Older message",
                    timestamp=(base_time + timedelta(hours=2)).isoformat()
                ),
            ],
            created_at=(base_time + timedelta(hours=5)).isoformat(),
        )
        
        # Session 3: Created last, has middle-aged messages
        session3 = ChatSession(
            session_id="session-3",
            workspace_name="test",
            workspace_path="/test",
            messages=[
                ChatMessage(
                    role="user",
                    content="Middle message",
                    timestamp=(base_time + timedelta(hours=5)).isoformat()
                ),
            ],
            created_at=(base_time + timedelta(hours=8)).isoformat(),
        )
        
        # Add sessions
        db.add_session(session1)
        db.add_session(session2)
        db.add_session(session3)
        
        # List sessions - should be sorted by most recent message
        sessions = db.list_sessions()
        
        # Verify order: session-1 (hour 10), session-3 (hour 5), session-2 (hour 2)
        assert len(sessions) == 3
        assert sessions[0]['session_id'] == "session-1"
        assert sessions[1]['session_id'] == "session-3"
        assert sessions[2]['session_id'] == "session-2"
        
        # Verify last_message_at is included
        assert 'last_message_at' in sessions[0]
