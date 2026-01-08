"""Tests for the webapp module."""

import tempfile
from pathlib import Path

import pytest

from copilot_chat_archive.database import Database
from copilot_chat_archive.scanner import ChatMessage, ChatSession
from copilot_chat_archive.webapp import create_app, _markdown_to_html, _parse_diff_stats


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


class TestParseDiffStats:
    """Tests for the diff statistics parser."""

    def test_empty_diff(self):
        """Test parsing empty diff returns zeros."""
        result = _parse_diff_stats("")
        assert result == {"additions": 0, "deletions": 0}

    def test_none_diff(self):
        """Test parsing None diff returns zeros."""
        result = _parse_diff_stats(None)
        assert result == {"additions": 0, "deletions": 0}

    def test_additions_only(self):
        """Test parsing diff with only additions."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,5 @@
 existing line
+new line 1
+new line 2
 another existing"""
        result = _parse_diff_stats(diff)
        assert result["additions"] == 2
        assert result["deletions"] == 0

    def test_deletions_only(self):
        """Test parsing diff with only deletions."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,5 +1,3 @@
 existing line
-removed line 1
-removed line 2
 another existing"""
        result = _parse_diff_stats(diff)
        assert result["additions"] == 0
        assert result["deletions"] == 2

    def test_mixed_changes(self):
        """Test parsing diff with both additions and deletions."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,4 +1,4 @@
 existing line
-old code
+new code
+another new line
-old line removed
 final line"""
        result = _parse_diff_stats(diff)
        assert result["additions"] == 2
        assert result["deletions"] == 2


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
        assert "parse_diff_stats" in app.jinja_env.filters


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


class TestRefreshRoute:
    """Tests for the refresh database route."""

    def test_refresh_route_exists(self, client):
        """Test that the refresh route exists and accepts POST."""
        response = client.post("/refresh")
        # Should redirect to index after refresh
        assert response.status_code == 302
        assert "/" in response.headers.get("Location", "")

    def test_refresh_incremental_mode(self, client):
        """Test refresh in incremental mode (default)."""
        response = client.post("/refresh", data={"full": "false"}, follow_redirects=True)
        assert response.status_code == 200
        # Check that the notification shows incremental mode
        assert b"Incremental refresh complete" in response.data

    def test_refresh_full_mode(self, client):
        """Test refresh in full rebuild mode."""
        response = client.post("/refresh", data={"full": "true"}, follow_redirects=True)
        assert response.status_code == 200
        # Check that the notification shows full mode
        assert b"Full refresh complete" in response.data

    def test_refresh_result_display(self, client):
        """Test that refresh result is displayed after redirect."""
        # First do a refresh
        response = client.post("/refresh", data={"full": "false"}, follow_redirects=True)
        assert response.status_code == 200
        # Should contain refresh notification with results
        assert b"refresh complete" in response.data.lower()

    def test_refresh_result_shown_only_once(self, client):
        """Test that refresh result is only shown once (session flash behavior)."""
        # First do a refresh and follow redirects
        response = client.post("/refresh", data={"full": "false"}, follow_redirects=True)
        assert response.status_code == 200
        assert b"refresh complete" in response.data.lower()
        
        # Navigate to index again - notification should NOT appear
        response = client.get("/")
        assert response.status_code == 200
        assert b"refresh complete" not in response.data.lower()

    def test_index_shows_refresh_buttons(self, client):
        """Test that the index page shows refresh buttons."""
        response = client.get("/")
        assert response.status_code == 200
        # Check for refresh buttons in the HTML
        assert b"Refresh" in response.data
        assert b"Rebuild All" in response.data

    def test_refresh_get_method_not_allowed(self, client):
        """Test that GET method is not allowed for refresh route."""
        response = client.get("/refresh")
        assert response.status_code == 405  # Method Not Allowed


class TestRefreshWithTestData:
    """Tests for refresh functionality with actual test data files."""

    def test_refresh_adds_new_session_from_file(self, tmp_path):
        """Test that refresh correctly adds a new session from a test file."""
        import json
        
        # Create a temporary database
        db_path = tmp_path / "test.db"
        db = Database(str(db_path))
        
        # Create a mock chat sessions directory structure
        workspace_dir = tmp_path / "workspaceStorage" / "abc123"
        chat_dir = workspace_dir / "chatSessions"
        chat_dir.mkdir(parents=True)
        
        # Create workspace.json
        (workspace_dir / "workspace.json").write_text(json.dumps({
            "folder": f"file://{tmp_path}/myproject"
        }))
        
        # Create a chat session file
        session_file = chat_dir / "session1.json"
        session_file.write_text(json.dumps({
            "sessionId": "test-session-1",
            "createdAt": "1704110400000",
            "requests": [
                {
                    "message": {"text": "Hello, assistant!"},
                    "timestamp": 1704110400000,
                    "response": [{"value": "Hello! How can I help you?"}]
                }
            ]
        }))
        
        # Create a Flask app with custom storage paths
        app = create_app(str(db_path), title="Test Archive")
        app.config["TESTING"] = True
        
        # Verify database is initially empty
        stats = db.get_stats()
        assert stats["session_count"] == 0
        
        # Manually import the session using the scanner
        from copilot_chat_archive.scanner import scan_chat_sessions
        storage_paths = [(str(tmp_path / "workspaceStorage"), "stable")]
        sessions = list(scan_chat_sessions(storage_paths))
        
        # Verify we found the session
        assert len(sessions) == 1
        assert sessions[0].session_id == "test-session-1"
        
        # Add it to the database
        db.add_session(sessions[0])
        
        # Verify it was added
        stats = db.get_stats()
        assert stats["session_count"] == 1

    def test_refresh_updates_modified_session(self, tmp_path):
        """Test that refresh correctly updates a session when the file changes."""
        import json
        import time
        
        # Create a temporary database
        db_path = tmp_path / "test.db"
        db = Database(str(db_path))
        
        # Create a mock chat sessions directory structure
        workspace_dir = tmp_path / "workspaceStorage" / "abc123"
        chat_dir = workspace_dir / "chatSessions"
        chat_dir.mkdir(parents=True)
        
        # Create workspace.json
        (workspace_dir / "workspace.json").write_text(json.dumps({
            "folder": f"file://{tmp_path}/myproject"
        }))
        
        # Create a chat session file
        session_file = chat_dir / "session1.json"
        session_file.write_text(json.dumps({
            "sessionId": "update-test-session",
            "createdAt": "1704110400000",
            "requests": [
                {
                    "message": {"text": "First message"},
                    "timestamp": 1704110400000,
                    "response": [{"value": "First response"}]
                }
            ]
        }))
        
        # Import initial session
        from copilot_chat_archive.scanner import scan_chat_sessions
        storage_paths = [(str(tmp_path / "workspaceStorage"), "stable")]
        sessions = list(scan_chat_sessions(storage_paths))
        assert len(sessions) == 1
        db.add_session(sessions[0])
        
        # Get initial session
        initial_session = db.get_session("update-test-session")
        assert initial_session is not None
        assert len(initial_session.messages) == 2  # user + assistant
        
        # Modify the session file with an additional message
        time.sleep(0.1)  # Ensure mtime changes
        session_file.write_text(json.dumps({
            "sessionId": "update-test-session",
            "createdAt": "1704110400000",
            "requests": [
                {
                    "message": {"text": "First message"},
                    "timestamp": 1704110400000,
                    "response": [{"value": "First response"}]
                },
                {
                    "message": {"text": "Second message"},
                    "timestamp": 1704110500000,
                    "response": [{"value": "Second response"}]
                }
            ]
        }))
        
        # Re-scan and check that needs_update detects the change
        sessions = list(scan_chat_sessions(storage_paths))
        assert len(sessions) == 1
        updated_session = sessions[0]
        
        # Check that needs_update returns True for the modified file
        needs_update = db.needs_update(
            updated_session.session_id,
            updated_session.source_file_mtime,
            updated_session.source_file_size
        )
        assert needs_update, "needs_update should return True for modified file"
        
        # Update the session
        db.update_session(updated_session)
        
        # Verify the update
        updated = db.get_session("update-test-session")
        assert updated is not None
        assert len(updated.messages) == 4  # 2 user + 2 assistant messages

    def test_refresh_skips_unchanged_session(self, tmp_path):
        """Test that refresh correctly skips unchanged sessions."""
        import json
        
        # Create a temporary database
        db_path = tmp_path / "test.db"
        db = Database(str(db_path))
        
        # Create a mock chat sessions directory structure
        workspace_dir = tmp_path / "workspaceStorage" / "abc123"
        chat_dir = workspace_dir / "chatSessions"
        chat_dir.mkdir(parents=True)
        
        # Create workspace.json
        (workspace_dir / "workspace.json").write_text(json.dumps({
            "folder": f"file://{tmp_path}/myproject"
        }))
        
        # Create a chat session file
        session_file = chat_dir / "session1.json"
        session_file.write_text(json.dumps({
            "sessionId": "skip-test-session",
            "createdAt": "1704110400000",
            "requests": [
                {
                    "message": {"text": "Test message"},
                    "timestamp": 1704110400000,
                    "response": [{"value": "Test response"}]
                }
            ]
        }))
        
        # Import session
        from copilot_chat_archive.scanner import scan_chat_sessions
        storage_paths = [(str(tmp_path / "workspaceStorage"), "stable")]
        sessions = list(scan_chat_sessions(storage_paths))
        assert len(sessions) == 1
        db.add_session(sessions[0])
        
        # Re-scan WITHOUT modifying the file
        sessions = list(scan_chat_sessions(storage_paths))
        assert len(sessions) == 1
        same_session = sessions[0]
        
        # Check that needs_update returns False for unchanged file
        needs_update = db.needs_update(
            same_session.session_id,
            same_session.source_file_mtime,
            same_session.source_file_size
        )
        assert not needs_update, "needs_update should return False for unchanged file"
