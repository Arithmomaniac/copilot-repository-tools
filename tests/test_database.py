"""Tests for the database module."""

import json
import tempfile
from pathlib import Path

import pytest

from copilot_repository_tools_common import Database, ChatMessage, ChatSession, ParsedQuery, parse_search_query


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


class TestRawJsonStorage:
    """Tests for raw JSON storage and rebuild functionality."""

    def test_raw_json_stored_compressed(self, temp_db):
        """Test that raw JSON is stored in compressed form."""
        import zlib
        
        raw_json = b'{"sessionId": "raw-test", "requests": [{"message": {"text": "Hello"}, "response": [{"kind": "text", "value": "Hi"}]}]}'
        session = ChatSession(
            session_id="raw-test",
            workspace_name="test-workspace",
            workspace_path="/test/path",
            messages=[ChatMessage(role="user", content="Hello")],
            source_file="/test/session.json",
            raw_json=raw_json,
        )
        temp_db.add_session(session)

        # Retrieve raw JSON
        retrieved_raw = temp_db.get_raw_json("raw-test")
        assert retrieved_raw is not None
        assert retrieved_raw == raw_json

    def test_raw_session_count(self, temp_db, sample_session):
        """Test getting raw session count."""
        assert temp_db.get_raw_session_count() == 0
        temp_db.add_session(sample_session)
        assert temp_db.get_raw_session_count() == 1

    def test_rebuild_derived_tables(self, temp_db):
        """Test rebuilding derived tables from raw JSON."""
        # Create a session with raw JSON that has the VS Code format
        raw_json = b'{"sessionId": "rebuild-test", "createdAt": "2025-01-15", "requests": [{"message": {"text": "What is Python?"}, "response": [{"kind": "text", "value": "Python is a programming language."}]}]}'
        session = ChatSession(
            session_id="rebuild-test",
            workspace_name="rebuild-workspace",
            workspace_path="/rebuild/path",
            messages=[
                ChatMessage(role="user", content="What is Python?"),
                ChatMessage(role="assistant", content="Python is a programming language."),
            ],
            source_file="/rebuild/session.json",
            raw_json=raw_json,
        )
        temp_db.add_session(session)

        # Verify session exists
        assert temp_db.get_stats()["session_count"] == 1
        assert temp_db.get_stats()["message_count"] == 2

        # Rebuild derived tables
        result = temp_db.rebuild_derived_tables()
        assert result["total"] == 1
        assert result["processed"] == 1
        assert result["errors"] == 0

        # Verify session still exists after rebuild
        stats = temp_db.get_stats()
        assert stats["session_count"] == 1
        # Message count depends on parsing - the raw JSON has requests format

    def test_rebuild_preserves_raw_sessions(self, temp_db, sample_session):
        """Test that rebuild does not affect raw_sessions table."""
        temp_db.add_session(sample_session)
        
        initial_raw_count = temp_db.get_raw_session_count()
        assert initial_raw_count == 1

        # Rebuild
        temp_db.rebuild_derived_tables()

        # Raw sessions should still be there
        assert temp_db.get_raw_session_count() == initial_raw_count

    def test_update_session_updates_raw_json(self, temp_db):
        """Test that update_session also updates raw_sessions."""
        raw_json_v1 = b'{"sessionId": "update-raw-test", "requests": [{"message": {"text": "V1"}, "response": []}]}'
        session_v1 = ChatSession(
            session_id="update-raw-test",
            workspace_name="test",
            workspace_path="/test",
            messages=[ChatMessage(role="user", content="V1")],
            raw_json=raw_json_v1,
        )
        temp_db.add_session(session_v1)

        # Verify V1 is stored
        retrieved_v1 = temp_db.get_raw_json("update-raw-test")
        assert retrieved_v1 == raw_json_v1

        # Update with V2
        raw_json_v2 = b'{"sessionId": "update-raw-test", "requests": [{"message": {"text": "V2"}, "response": []}]}'
        session_v2 = ChatSession(
            session_id="update-raw-test",
            workspace_name="test",
            workspace_path="/test",
            messages=[ChatMessage(role="user", content="V2")],
            raw_json=raw_json_v2,
        )
        temp_db.update_session(session_v2)

        # Verify V2 is now stored
        retrieved_v2 = temp_db.get_raw_json("update-raw-test")
        assert retrieved_v2 == raw_json_v2

    def test_session_without_raw_json(self, temp_db, sample_session):
        """Test that sessions without raw_json still work (stores empty compressed JSON)."""
        # sample_session doesn't have raw_json set
        assert sample_session.raw_json is None
        
        result = temp_db.add_session(sample_session)
        assert result is True

        # Session should still be added and queryable
        retrieved = temp_db.get_session(sample_session.session_id)
        assert retrieved is not None
        assert len(retrieved.messages) == len(sample_session.messages)


class TestParseSearchQuery:
    """Tests for the parse_search_query function using parametrized test cases."""

    @pytest.mark.parametrize("query,expected_fts,expected_role,expected_workspace,expected_title", [
        # Empty and simple queries
        ("", "", None, None, None),
        ("python", "python", None, None, None),
        ("python function", "python function", None, None, None),
        
        # Quoted phrases (for exact match in FTS5)
        ('"python function"', '"python function"', None, None, None),
        ('create "python function" parameters', 'create "python function" parameters', None, None, None),
        
        # Field filters
        ("python role:user", "python", "user", None, None),
        ("role:assistant function", "function", "assistant", None, None),
        ("python workspace:my-project", "python", None, "my-project", None),
        ("function title:MySession", "function", None, None, "MySession"),
        
        # Quoted field values
        ('workspace:"my project name" python', "python", None, "my project name", None),
        
        # Multiple filters together
        ("python role:user workspace:myproj", "python", "user", "myproj", None),
        ("role:user workspace:test", "", "user", "test", None),
        
        # Case insensitive field names
        ("Role:user WORKSPACE:test", "", "user", "test", None),
        
        # Duplicate field values - last one wins
        ("role:user role:assistant python", "python", "assistant", None, None),
        ("workspace:first workspace:second", "", None, "second", None),
    ])
    def test_parse_search_query(self, query, expected_fts, expected_role, expected_workspace, expected_title):
        """Test parsing search queries with various formats."""
        result = parse_search_query(query)
        assert result.fts_query == expected_fts
        assert result.role == expected_role
        assert result.workspace == expected_workspace
        assert result.title == expected_title


@pytest.fixture
def search_test_db(tmp_path):
    """Create a database with multiple sessions for search testing."""
    db = Database(tmp_path / "search_test.db")
    
    # Session 1: Python project with user and assistant messages
    session1 = ChatSession(
        session_id="session-python",
        workspace_name="python-project",
        workspace_path="/home/user/python-project",
        messages=[
            ChatMessage(role="user", content="How do I create a Python function?"),
            ChatMessage(role="assistant", content="Here's how to create a Python function with def keyword."),
            ChatMessage(role="user", content="Thanks! Can you add parameters?"),
            ChatMessage(role="assistant", content="Sure! Here's a function with parameters."),
        ],
        created_at="1704067200000",  # 2024-01-01
        vscode_edition="stable",
    )
    db.add_session(session1)
    
    # Session 2: React project with different content
    session2 = ChatSession(
        session_id="session-react",
        workspace_name="react-app",
        workspace_path="/home/user/react-app",
        messages=[
            ChatMessage(role="user", content="How do I use React hooks?"),
            ChatMessage(role="assistant", content="React hooks like useState and useEffect are used in function components."),
        ],
        created_at="1704153600000",  # 2024-01-02
        vscode_edition="insider",
    )
    db.add_session(session2)
    
    # Session 3: Another Python session for testing multi-session results
    session3 = ChatSession(
        session_id="session-python-2",
        workspace_name="python-project",
        workspace_path="/home/user/python-project",
        messages=[
            ChatMessage(role="user", content="What is a Python decorator?"),
            ChatMessage(role="assistant", content="A decorator is a function that modifies another function."),
        ],
        created_at="1704240000000",  # 2024-01-03
        vscode_edition="stable",
    )
    db.add_session(session3)
    
    return db


class TestAdvancedSearchIntegration:
    """Integration tests for search functionality against actual database."""

    def test_multiple_words_match_all(self, search_test_db):
        """Test that multiple words match messages containing ALL words (AND logic)."""
        # "Python function" should match messages with both words
        results = search_test_db.search("Python function")
        assert len(results) > 0
        for r in results:
            content_lower = r["content"].lower()
            assert "python" in content_lower and "function" in content_lower

    def test_quoted_phrase_exact_match(self, search_test_db):
        """Test that quoted phrases match exactly (verbatim)."""
        # '"Python function"' should match the exact phrase
        results = search_test_db.search('"Python function"')
        assert len(results) > 0
        for r in results:
            assert "Python function" in r["content"]

    def test_mixed_words_and_quoted_phrase(self, search_test_db):
        """Test search with both unquoted words and quoted phrase."""
        # Search for 'create "Python function"' should match messages with
        # the exact phrase "Python function" and the word "create"
        results = search_test_db.search('create "Python function"')
        # Should match messages containing both "create" and the exact phrase
        for r in results:
            content = r["content"]
            assert "Python function" in content
            assert "create" in content.lower()

    @pytest.mark.parametrize("query,expected_role", [
        ("role:user Python", "user"),
        ("role:assistant function", "assistant"),
    ])
    def test_role_filter_integration(self, search_test_db, query, expected_role):
        """Test that role filter correctly filters search results."""
        results = search_test_db.search(query)
        assert len(results) > 0
        for r in results:
            if r.get("match_type") == "message":
                assert r["role"] == expected_role

    @pytest.mark.parametrize("query,expected_workspace", [
        ("workspace:python-project function", "python-project"),
        ("workspace:react-app hooks", "react-app"),
    ])
    def test_workspace_filter_integration(self, search_test_db, query, expected_workspace):
        """Test that workspace filter correctly filters search results."""
        results = search_test_db.search(query)
        assert len(results) > 0
        for r in results:
            assert r["workspace_name"] == expected_workspace

    def test_duplicate_role_filter_last_wins(self, search_test_db):
        """Test that duplicate field filters use the last value.
        
        Test data has assistant messages containing 'Python' (e.g., 
        'Here's how to create a Python function with def keyword.').
        """
        # Both role:user and role:assistant in query - last one wins
        results = search_test_db.search("role:user role:assistant Python")
        assert len(results) > 0, "Expected assistant messages with 'Python' in test data"
        for r in results:
            if r.get("match_type") == "message":
                assert r["role"] == "assistant"

    def test_filter_only_no_fts_query(self, search_test_db):
        """Test search with only field filters (no FTS query)."""
        # Search with only workspace filter
        results = search_test_db.search("workspace:python-project")
        assert len(results) > 0
        for r in results:
            assert r["workspace_name"] == "python-project"

    def test_combined_filters(self, search_test_db):
        """Test search with multiple filters combined."""
        results = search_test_db.search("workspace:python-project role:assistant")
        assert len(results) > 0
        for r in results:
            assert r["workspace_name"] == "python-project"
            assert r["role"] == "assistant"

    @pytest.mark.parametrize("sort_by", ["relevance", "date"])
    def test_sort_options(self, search_test_db, sort_by):
        """Test that sort options work correctly."""
        results = search_test_db.search("Python", sort_by=sort_by)
        assert len(results) > 0
        
    def test_sort_by_date_order(self, search_test_db):
        """Test that date sorting returns results in date order."""
        results = search_test_db.search("Python", sort_by="date")
        assert len(results) > 0
        # Results should be ordered by created_at DESC (newest first)
        dates = [r.get("created_at") for r in results if r.get("created_at")]
        # All dates should be in descending order
        for i in range(len(dates) - 1):
            assert dates[i] >= dates[i + 1]

    def test_no_results_for_non_matching_query(self, search_test_db):
        """Test that non-matching query returns empty results."""
        results = search_test_db.search("nonexistentword12345")
        assert len(results) == 0
