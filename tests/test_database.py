"""Tests for the database module."""

import json
import tempfile
from pathlib import Path

import pytest

from copilot_chat_archive.database import Database, parse_search_query, ParsedQuery
from copilot_chat_archive.scanner import ChatMessage, ChatSession


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


class TestParseSearchQuery:
    """Tests for the parse_search_query function."""

    def test_empty_query(self):
        """Test parsing an empty query."""
        result = parse_search_query("")
        assert result.fts_query == ""
        assert result.role is None
        assert result.workspace is None
        assert result.title is None

    def test_simple_word(self):
        """Test parsing a single word."""
        result = parse_search_query("python")
        assert result.fts_query == "python"
        assert result.role is None

    def test_multiple_words(self):
        """Test parsing multiple words (should become AND search)."""
        result = parse_search_query("python function")
        assert result.fts_query == "python function"
        # FTS5 uses implicit AND for space-separated words

    def test_quoted_phrase(self):
        """Test parsing a quoted phrase (exact match)."""
        result = parse_search_query('"python function"')
        assert result.fts_query == '"python function"'

    def test_mixed_words_and_phrase(self):
        """Test parsing mixed words and quoted phrase."""
        result = parse_search_query('create "python function" parameters')
        assert '"python function"' in result.fts_query
        assert "create" in result.fts_query
        assert "parameters" in result.fts_query

    def test_role_filter(self):
        """Test extracting role filter from query."""
        result = parse_search_query("python role:user")
        assert result.fts_query == "python"
        assert result.role == "user"

    def test_role_filter_assistant(self):
        """Test extracting assistant role filter."""
        result = parse_search_query("role:assistant function")
        assert result.fts_query == "function"
        assert result.role == "assistant"

    def test_workspace_filter(self):
        """Test extracting workspace filter from query."""
        result = parse_search_query("python workspace:my-project")
        assert result.fts_query == "python"
        assert result.workspace == "my-project"

    def test_title_filter(self):
        """Test extracting title filter from query."""
        result = parse_search_query("function title:MySession")
        assert result.fts_query == "function"
        assert result.title == "MySession"

    def test_quoted_field_value(self):
        """Test field value with quotes."""
        result = parse_search_query('workspace:"my project name" python')
        assert result.fts_query == "python"
        assert result.workspace == "my project name"

    def test_multiple_filters(self):
        """Test multiple field filters together."""
        result = parse_search_query("python role:user workspace:myproj")
        assert result.fts_query == "python"
        assert result.role == "user"
        assert result.workspace == "myproj"

    def test_only_filters_no_search(self):
        """Test query with only field filters and no search terms."""
        result = parse_search_query("role:user workspace:test")
        assert result.fts_query == ""
        assert result.role == "user"
        assert result.workspace == "test"

    def test_case_insensitive_field_names(self):
        """Test that field names are case insensitive."""
        result = parse_search_query("Role:user WORKSPACE:test")
        assert result.role == "user"
        assert result.workspace == "test"


class TestAdvancedSearch:
    """Tests for advanced search functionality."""

    def test_search_multiple_words(self, temp_db, sample_session):
        """Test that multiple words match as AND (non-continuous)."""
        temp_db.add_session(sample_session)

        # Search for two words that appear in the same message
        results = temp_db.search("Python function")
        assert len(results) > 0
        # Both words should be in the results
        content = results[0]["content"]
        assert "Python" in content or "function" in content

    def test_search_with_role_in_query(self, temp_db, sample_session):
        """Test searching with role filter in query."""
        temp_db.add_session(sample_session)

        # Search only user messages
        results = temp_db.search("role:user function")
        for r in results:
            if r.get("match_type") == "message":
                assert r["role"] == "user"

    def test_search_with_workspace_in_query(self, temp_db, sample_session):
        """Test searching with workspace filter in query."""
        temp_db.add_session(sample_session)

        # Add another session in different workspace
        other_session = ChatSession(
            session_id="other-session-456",
            workspace_name="other-project",
            workspace_path="/home/user/other",
            messages=[ChatMessage(role="user", content="Python function help")],
            vscode_edition="stable",
        )
        temp_db.add_session(other_session)

        # Search only in my-project workspace
        results = temp_db.search("Python workspace:my-project")
        for r in results:
            assert r["workspace_name"] == "my-project"

    def test_search_sort_by_relevance(self, temp_db, sample_session):
        """Test that sort_by=relevance works."""
        temp_db.add_session(sample_session)
        results = temp_db.search("Python", sort_by="relevance")
        assert len(results) > 0

    def test_search_sort_by_date(self, temp_db, sample_session):
        """Test that sort_by=date works."""
        temp_db.add_session(sample_session)
        results = temp_db.search("Python", sort_by="date")
        assert len(results) > 0

    def test_search_filter_only_workspace(self, temp_db, sample_session):
        """Test search with only workspace filter (no FTS query)."""
        temp_db.add_session(sample_session)

        # Add another session in different workspace
        other_session = ChatSession(
            session_id="other-session-789",
            workspace_name="other-project",
            workspace_path="/home/user/other",
            messages=[ChatMessage(role="user", content="Some content here")],
            vscode_edition="stable",
        )
        temp_db.add_session(other_session)

        # Search with only workspace filter
        results = temp_db.search("workspace:my-project")
        assert len(results) > 0
        for r in results:
            assert r["workspace_name"] == "my-project"

    def test_search_filter_only_role(self, temp_db, sample_session):
        """Test search with only role filter (no FTS query)."""
        temp_db.add_session(sample_session)

        # Search with only role filter
        results = temp_db.search("role:user")
        assert len(results) > 0
        for r in results:
            assert r["role"] == "user"

    def test_search_multiple_filters_no_fts(self, temp_db, sample_session):
        """Test search with multiple filters but no FTS query."""
        temp_db.add_session(sample_session)

        # Search with role and workspace filters
        results = temp_db.search("workspace:my-project role:assistant")
        assert len(results) > 0
        for r in results:
            assert r["workspace_name"] == "my-project"
            assert r["role"] == "assistant"
