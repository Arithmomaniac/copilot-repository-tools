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
def search_test_db(temp_db):
    """Create a database with multiple sessions for search testing."""
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
    temp_db.add_session(session1)
    
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
    temp_db.add_session(session2)
    
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
    temp_db.add_session(session3)
    
    return temp_db


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
