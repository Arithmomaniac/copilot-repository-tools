"""Tests for the webapp module."""

import tempfile
from pathlib import Path

import pytest

from copilot_chat_archive.database import Database
from copilot_chat_archive.scanner import ChatMessage, ChatSession
from copilot_chat_archive.webapp import create_app, _markdown_to_html


@pytest.fixture
def temp_db():
    """Create a temporary database with sample data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = Database(db_path)

    # Add sample session
    session = ChatSession(
        session_id="webapp-test-session",
        workspace_name="test-workspace",
        workspace_path="/home/user/test",
        messages=[
            ChatMessage(role="user", content="What is Python?"),
            ChatMessage(
                role="assistant",
                content="Python is a programming language.\n\n```python\nprint('Hello')\n```",
            ),
        ],
        created_at="2025-01-15T10:00:00Z",
        vscode_edition="stable",
    )
    db.add_session(session)

    yield db_path

    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def app(temp_db):
    """Create a Flask test app."""
    app = create_app(temp_db, title="Test Archive")
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """Create a Flask test client."""
    return app.test_client()


class TestMarkdownToHtml:
    """Tests for the markdown to HTML converter."""

    def test_plain_text(self):
        """Test converting plain text."""
        result = _markdown_to_html("Hello, world!")
        assert "Hello, world!" in result

    def test_inline_code(self):
        """Test converting inline code."""
        result = _markdown_to_html("Use `print()` function")
        assert "<code>print()</code>" in result

    def test_code_block(self):
        """Test converting code blocks."""
        text = "```python\nprint('hello')\n```"
        result = _markdown_to_html(text)
        assert "<pre>" in result
        assert "<code" in result
        assert "print" in result

    def test_inline_numbered_list_converted(self):
        """Test that inline numbered lists get line breaks."""
        text = "I should: 1. First do this 2. Then do that 3. Finally finish"
        result = _markdown_to_html(text)
        # Should have line breaks inserted before numbered items
        assert "<br" in result or "<li>" in result


class TestWebappRoutes:
    """Tests for the webapp routes."""

    def test_index_route(self, client):
        """Test the index route returns 200."""
        response = client.get("/")
        assert response.status_code == 200
        assert b"Test Archive" in response.data

    def test_index_shows_sessions(self, client):
        """Test the index shows sessions."""
        response = client.get("/")
        assert response.status_code == 200
        assert b"test-workspace" in response.data

    def test_index_search(self, client):
        """Test the index search functionality."""
        response = client.get("/?q=Python")
        assert response.status_code == 200
        # Should show search results or the matching session
        assert b"test-workspace" in response.data or b"Python" in response.data

    def test_session_route(self, client):
        """Test the session route returns 200."""
        response = client.get("/session/webapp-test-session")
        assert response.status_code == 200
        assert b"test-workspace" in response.data

    def test_session_shows_messages(self, client):
        """Test the session route shows messages."""
        response = client.get("/session/webapp-test-session")
        assert response.status_code == 200
        assert b"What is Python?" in response.data

    def test_session_not_found(self, client):
        """Test 404 for non-existent session."""
        response = client.get("/session/nonexistent-session-id")
        assert response.status_code == 404
        assert b"Session not found" in response.data

    def test_empty_search(self, client):
        """Test empty search query shows all sessions."""
        response = client.get("/?q=")
        assert response.status_code == 200
        assert b"test-workspace" in response.data


class TestCreateApp:
    """Tests for the create_app function."""

    def test_create_app_with_db(self, temp_db):
        """Test creating an app with a database."""
        app = create_app(temp_db)
        assert app is not None
        assert app.config["DB_PATH"] == temp_db

    def test_create_app_with_title(self, temp_db):
        """Test creating an app with a custom title."""
        app = create_app(temp_db, title="Custom Title")
        assert app.config["ARCHIVE_TITLE"] == "Custom Title"

    def test_app_has_filters(self, temp_db):
        """Test that the app has the required Jinja2 filters."""
        app = create_app(temp_db)
        assert "markdown" in app.jinja_env.filters
        assert "urldecode" in app.jinja_env.filters
        assert "format_timestamp" in app.jinja_env.filters


class TestEmptyDatabase:
    """Tests with an empty database."""

    def test_index_empty_db(self, tmp_path):
        """Test index with empty database."""
        db_path = tmp_path / "empty.db"
        _ = Database(db_path)  # Create empty database
        
        app = create_app(str(db_path))
        app.config["TESTING"] = True
        client = app.test_client()
        
        response = client.get("/")
        assert response.status_code == 200
        assert b"No sessions found" in response.data
