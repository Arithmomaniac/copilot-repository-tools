"""Tests for the webapp module."""

import tempfile
from pathlib import Path

import pytest
from copilot_repository_tools_common import ChatMessage, ChatSession, Database
from copilot_repository_tools_web import create_app
from copilot_repository_tools_web.webapp import _extract_filename, _markdown_to_html, _parse_diff_stats


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
    """Create a Flask test app with empty storage paths for fast tests."""
    app = create_app(temp_db, title="Test Archive", storage_paths=[], include_cli=False)
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

    def test_skips_hunk_headers(self):
        """Test that hunk headers (@@ lines) are not counted as deletions."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,5 +1,6 @@
 line 1
+added line
@@ -10,3 +11,4 @@
 line 10
+another added"""
        result = _parse_diff_stats(diff)
        # Should have 2 additions, 0 deletions (hunk headers should be skipped)
        assert result["additions"] == 2
        assert result["deletions"] == 0


class TestExtractFilename:
    """Tests for the filename extractor."""

    def test_empty_path(self):
        """Test extracting from empty path."""
        assert _extract_filename("") == ""
        assert _extract_filename(None) == ""

    def test_unix_path(self):
        """Test extracting from Unix-style path."""
        assert _extract_filename("/home/user/project/file.py") == "file.py"
        assert _extract_filename("src/main.py") == "main.py"

    def test_windows_path(self):
        """Test extracting from Windows-style path."""
        assert _extract_filename("C:\\Users\\user\\file.py") == "file.py"
        assert _extract_filename("src\\main.py") == "main.py"

    def test_filename_only(self):
        """Test extracting when input is just a filename."""
        assert _extract_filename("file.py") == "file.py"


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

    def test_search_with_sort_by_relevance(self, client):
        """Test search with relevance sorting."""
        response = client.get("/?q=Python&sort=relevance")
        assert response.status_code == 200
        # Should show the sort dropdown
        assert b'select name="sort"' in response.data

    def test_search_with_sort_by_date(self, client):
        """Test search with date sorting."""
        response = client.get("/?q=Python&sort=date")
        assert response.status_code == 200
        assert b'<option value="date"' in response.data

    def test_search_help_tips_shown(self, client):
        """Test that search help tips are shown."""
        response = client.get("/")
        assert response.status_code == 200
        assert b"exact phrase" in response.data or b"role:user" in response.data


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
        assert "extract_filename" in app.jinja_env.filters


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
        (workspace_dir / "workspace.json").write_text(json.dumps({"folder": f"file://{tmp_path}/myproject"}))

        # Create a chat session file
        session_file = chat_dir / "session1.json"
        session_file.write_text(
            json.dumps(
                {
                    "sessionId": "test-session-1",
                    "createdAt": "1704110400000",
                    "requests": [{"message": {"text": "Hello, assistant!"}, "timestamp": 1704110400000, "response": [{"value": "Hello! How can I help you?"}]}],
                }
            )
        )

        # Create a Flask app with custom storage paths
        app = create_app(str(db_path), title="Test Archive")
        app.config["TESTING"] = True

        # Verify database is initially empty
        stats = db.get_stats()
        assert stats["session_count"] == 0

        # Manually import the session using the scanner
        from copilot_repository_tools_common.scanner import scan_chat_sessions

        storage_paths = [(str(tmp_path / "workspaceStorage"), "stable")]
        sessions = list(scan_chat_sessions(storage_paths, include_cli=False))

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
        (workspace_dir / "workspace.json").write_text(json.dumps({"folder": f"file://{tmp_path}/myproject"}))

        # Create a chat session file
        session_file = chat_dir / "session1.json"
        session_file.write_text(
            json.dumps(
                {
                    "sessionId": "update-test-session",
                    "createdAt": "1704110400000",
                    "requests": [{"message": {"text": "First message"}, "timestamp": 1704110400000, "response": [{"value": "First response"}]}],
                }
            )
        )

        # Import initial session
        from copilot_repository_tools_common.scanner import scan_chat_sessions

        storage_paths = [(str(tmp_path / "workspaceStorage"), "stable")]
        sessions = list(scan_chat_sessions(storage_paths, include_cli=False))
        assert len(sessions) == 1
        db.add_session(sessions[0])

        # Get initial session
        initial_session = db.get_session("update-test-session")
        assert initial_session is not None
        assert len(initial_session.messages) == 2  # user + assistant

        # Modify the session file with an additional message
        time.sleep(0.1)  # Ensure mtime changes
        session_file.write_text(
            json.dumps(
                {
                    "sessionId": "update-test-session",
                    "createdAt": "1704110400000",
                    "requests": [
                        {"message": {"text": "First message"}, "timestamp": 1704110400000, "response": [{"value": "First response"}]},
                        {"message": {"text": "Second message"}, "timestamp": 1704110500000, "response": [{"value": "Second response"}]},
                    ],
                }
            )
        )

        # Re-scan and check that needs_update detects the change
        sessions = list(scan_chat_sessions(storage_paths, include_cli=False))
        assert len(sessions) == 1
        updated_session = sessions[0]

        # Check that needs_update returns True for the modified file
        needs_update = db.needs_update(updated_session.session_id, updated_session.source_file_mtime, updated_session.source_file_size)
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
        (workspace_dir / "workspace.json").write_text(json.dumps({"folder": f"file://{tmp_path}/myproject"}))

        # Create a chat session file
        session_file = chat_dir / "session1.json"
        session_file.write_text(
            json.dumps(
                {
                    "sessionId": "skip-test-session",
                    "createdAt": "1704110400000",
                    "requests": [{"message": {"text": "Test message"}, "timestamp": 1704110400000, "response": [{"value": "Test response"}]}],
                }
            )
        )

        # Import session
        from copilot_repository_tools_common.scanner import scan_chat_sessions

        storage_paths = [(str(tmp_path / "workspaceStorage"), "stable")]
        sessions = list(scan_chat_sessions(storage_paths, include_cli=False))
        assert len(sessions) == 1
        db.add_session(sessions[0])

        # Re-scan WITHOUT modifying the file
        sessions = list(scan_chat_sessions(storage_paths, include_cli=False))
        assert len(sessions) == 1
        same_session = sessions[0]

        # Check that needs_update returns False for unchanged file
        needs_update = db.needs_update(same_session.session_id, same_session.source_file_mtime, same_session.source_file_size)
        assert not needs_update, "needs_update should return False for unchanged file"


class TestMarkdownFileUriConversion:
    """Tests for file:// URI to filename conversion in markdown."""

    def test_file_uri_to_monospace(self):
        """Test that file:// URIs with empty link text become monospace filenames."""
        result = _markdown_to_html("Reading [](file:///c%3A/Users/test/file.py)")
        assert "<code>file.py</code>" in result
        assert "[](" not in result  # Empty link should be replaced

    def test_file_uri_with_anchor(self):
        """Test that file:// URIs with anchors extract filename correctly."""
        result = _markdown_to_html("Reading [](file:///c%3A/path/to/SKILL.md#1-1), lines 1 to 100")
        assert "<code>SKILL.md</code>" in result
        assert "lines 1 to 100" in result

    def test_file_uri_url_encoded(self):
        """Test that URL-encoded paths are decoded properly."""
        result = _markdown_to_html("Reading [](file:///c%3A/Users/test%20user/my%20file.py)")
        assert "<code>my file.py</code>" in result

    def test_file_uri_unix_path(self):
        """Test Unix-style file:// URIs."""
        result = _markdown_to_html("Created [](file:///home/user/project/main.py)")
        assert "<code>main.py</code>" in result

    def test_multiple_file_uris(self):
        """Test multiple file:// URIs in the same text."""
        result = _markdown_to_html("Read [](file:///a/b.py) and [](file:///c/d.py)")
        assert "<code>b.py</code>" in result
        assert "<code>d.py</code>" in result


class TestHtmlOutputToolInvocations:
    """Tests for HTML output of tool invocation rendering."""

    @pytest.fixture
    def session_with_tools(self, tmp_path):
        """Create a session with various tool invocations for testing."""
        from copilot_repository_tools_common.scanner import ChatMessage, ChatSession, ContentBlock, ToolInvocation

        db_path = tmp_path / "test_tools.db"
        db = Database(str(db_path))

        # Create session with tool invocations
        session = ChatSession(
            session_id="tool-test-session",
            workspace_name="test-workspace",
            workspace_path="/home/user/test",
            messages=[
                ChatMessage(role="user", content="Run some tools"),
                ChatMessage(
                    role="assistant",
                    content="I'll run some tools for you.",
                    content_blocks=[
                        ContentBlock(kind="text", content="Let me help."),
                        ContentBlock(kind="toolInvocation", content="Running `test_mcp_tool`"),
                        ContentBlock(kind="toolInvocation", content="Reading [](file:///path/to/file.py)"),
                        ContentBlock(kind="toolInvocation", content='Using "Run in Terminal"'),
                    ],
                    tool_invocations=[
                        ToolInvocation(
                            name="mcp_test_tool",
                            status="completed",
                            input='{"query": "test input"}',
                            result="Tool output result",
                            source_type="mcp",
                            invocation_message="Running `test_mcp_tool`",
                        ),
                        ToolInvocation(
                            name="copilot_readFile",
                            status="completed",
                            input=None,
                            result=None,
                            source_type="internal",
                            invocation_message="Reading [](file:///path/to/file.py)",
                        ),
                        ToolInvocation(
                            name="run_in_terminal",
                            status="completed",
                            input="ls -la",
                            result="total 42\ndrwxr-xr-x 5 user user 4096 Jan 10 file.txt",
                            source_type="internal",
                            invocation_message='Using "Run in Terminal"',
                        ),
                    ],
                ),
            ],
            created_at="2025-01-15T10:00:00Z",
            vscode_edition="stable",
        )
        db.add_session(session)

        return db_path

    def test_mcp_tool_renders_collapsible(self, session_with_tools):
        """Test that MCP tools render with collapsible details."""
        app = create_app(str(session_with_tools))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/tool-test-session")
        html = response.data.decode("utf-8")

        # MCP tool should have collapsible wrapper
        assert "tool-invocation-wrapper" in html
        # Should show the tool name inside
        assert "mcp_test_tool" in html
        # Should have input/output sections
        assert "test input" in html
        assert "Tool output result" in html

    def test_internal_file_tool_renders_inline(self, session_with_tools):
        """Test that internal file tools render inline without collapsible."""
        app = create_app(str(session_with_tools))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/tool-test-session")
        html = response.data.decode("utf-8")

        # Should render the filename in monospace
        assert "<code>file.py</code>" in html

    def test_terminal_tool_renders_collapsible(self, session_with_tools):
        """Test that run_in_terminal renders with collapsible output."""
        app = create_app(str(session_with_tools))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/tool-test-session")
        html = response.data.decode("utf-8")

        # Terminal tool should have collapsible wrapper
        assert "run_in_terminal" in html
        # Should show the command
        assert "ls -la" in html
        # Should have the output
        assert "total 42" in html

    def test_tool_status_badge_rendered(self, session_with_tools):
        """Test that tool status badges are rendered."""
        app = create_app(str(session_with_tools))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/tool-test-session")
        html = response.data.decode("utf-8")

        # Status badges should appear
        assert "status-badge" in html
        assert "completed" in html


class TestHtmlOutputThinkingBlocks:
    """Tests for HTML output of thinking block rendering."""

    @pytest.fixture
    def session_with_thinking(self, tmp_path):
        """Create a session with thinking blocks for testing."""
        from copilot_repository_tools_common.scanner import ChatMessage, ChatSession, ContentBlock

        db_path = tmp_path / "test_thinking.db"
        db = Database(str(db_path))

        session = ChatSession(
            session_id="thinking-test-session",
            workspace_name="test-workspace",
            workspace_path="/home/user/test",
            messages=[
                ChatMessage(role="user", content="Think about something"),
                ChatMessage(
                    role="assistant",
                    content="Here's my thought process...",
                    content_blocks=[
                        ContentBlock(
                            kind="thinking",
                            content="Let me analyze this carefully...",
                            description="Analyzing the request",
                        ),
                        ContentBlock(kind="text", content="Based on my analysis, here's the answer."),
                    ],
                ),
            ],
            created_at="2025-01-15T10:00:00Z",
            vscode_edition="stable",
        )
        db.add_session(session)

        return db_path

    def test_thinking_block_renders_collapsible(self, session_with_thinking):
        """Test that thinking blocks render as collapsible sections."""
        app = create_app(str(session_with_thinking))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/thinking-test-session")
        html = response.data.decode("utf-8")

        # Should have thinking block structure
        assert "thinking-block" in html
        assert "Thinking" in html

    def test_thinking_block_shows_description(self, session_with_thinking):
        """Test that thinking block description is shown in header."""
        app = create_app(str(session_with_thinking))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/thinking-test-session")
        html = response.data.decode("utf-8")

        # Description should be in the summary
        assert "Analyzing the request" in html

    def test_thinking_block_content_inside_details(self, session_with_thinking):
        """Test that thinking content is inside the collapsible."""
        app = create_app(str(session_with_thinking))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/thinking-test-session")
        html = response.data.decode("utf-8")

        # Content should be in thinking-content div
        assert "thinking-content" in html
        assert "analyze this carefully" in html


class TestHtmlOutputFileChanges:
    """Tests for HTML output of file changes rendering."""

    @pytest.fixture
    def session_with_file_changes(self, tmp_path):
        """Create a session with file changes for testing."""
        from copilot_repository_tools_common.scanner import ChatMessage, ChatSession, FileChange

        db_path = tmp_path / "test_files.db"
        db = Database(str(db_path))

        session = ChatSession(
            session_id="files-test-session",
            workspace_name="test-workspace",
            workspace_path="/home/user/test",
            messages=[
                ChatMessage(role="user", content="Edit some files"),
                ChatMessage(
                    role="assistant",
                    content="I'll edit the files.",
                    file_changes=[
                        FileChange(
                            path="/src/main.py",
                            language_id="python",
                            explanation="Added error handling",
                            diff="+try:\n+    result = process()\n+except Exception as e:\n+    log_error(e)",
                        ),
                        FileChange(
                            path="/src/utils.js",
                            language_id="javascript",
                            explanation="Fixed bug in helper",
                            diff="-const old = 1;\n+const fixed = 2;",
                        ),
                    ],
                ),
            ],
            created_at="2025-01-15T10:00:00Z",
            vscode_edition="stable",
        )
        db.add_session(session)

        return db_path

    def test_file_changes_renders_collapsible(self, session_with_file_changes):
        """Test that file changes render in a collapsible section."""
        app = create_app(str(session_with_file_changes))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/files-test-session")
        html = response.data.decode("utf-8")

        # Should have file changes section
        assert "File Changes" in html
        assert "file-change" in html

    def test_file_changes_shows_filename(self, session_with_file_changes):
        """Test that file changes show the filename."""
        app = create_app(str(session_with_file_changes))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/files-test-session")
        html = response.data.decode("utf-8")

        # Filenames should appear
        assert "main.py" in html
        assert "utils.js" in html

    def test_file_changes_shows_language_badge(self, session_with_file_changes):
        """Test that file changes show language badges."""
        app = create_app(str(session_with_file_changes))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/files-test-session")
        html = response.data.decode("utf-8")

        # Language badges should appear
        assert "language-badge" in html
        assert "python" in html
        assert "javascript" in html

    def test_file_changes_shows_diff_stats(self, session_with_file_changes):
        """Test that file changes show addition/deletion statistics."""
        app = create_app(str(session_with_file_changes))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/files-test-session")
        html = response.data.decode("utf-8")

        # Diff stats should be shown
        assert "file-stat-add" in html or "file-stat-del" in html

    def test_file_changes_shows_explanation(self, session_with_file_changes):
        """Test that file changes show explanations."""
        app = create_app(str(session_with_file_changes))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/files-test-session")
        html = response.data.decode("utf-8")

        # Explanations should appear
        assert "Added error handling" in html
        assert "Fixed bug in helper" in html

    def test_file_changes_shows_diff(self, session_with_file_changes):
        """Test that file changes show the diff content."""
        app = create_app(str(session_with_file_changes))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/session/files-test-session")
        html = response.data.decode("utf-8")

        # Diff content should appear
        assert "process()" in html
        assert "log_error" in html


class TestHtmlOutputCodeBlocks:
    """Tests for HTML output of code block rendering."""

    def test_code_block_renders_with_pre(self, client):
        """Test that code blocks render with pre tag."""
        response = client.get("/session/webapp-test-session")
        html = response.data.decode("utf-8")

        # Code block should have pre and code tags
        assert "<pre>" in html or "<pre " in html
        assert "<code" in html

    def test_inline_code_renders_with_code(self):
        """Test that inline code renders with code tag."""
        result = _markdown_to_html("Use the `foo()` function")
        assert "<code>foo()</code>" in result


class TestHtmlOutputMessageStructure:
    """Tests for the overall message HTML structure."""

    def test_messages_have_role_class(self, client):
        """Test that messages have role-based CSS classes."""
        response = client.get("/session/webapp-test-session")
        html = response.data.decode("utf-8")

        # Should have role-based classes
        assert 'class="message user"' in html
        assert 'class="message assistant"' in html

    def test_messages_have_anchors(self, client):
        """Test that messages have anchor links."""
        response = client.get("/session/webapp-test-session")
        html = response.data.decode("utf-8")

        # Should have message anchors
        assert 'id="msg-1"' in html
        assert 'id="msg-2"' in html
        assert 'href="#msg-1"' in html

    def test_session_header_shows_metadata(self, client):
        """Test that session header shows metadata."""
        response = client.get("/session/webapp-test-session")
        html = response.data.decode("utf-8")

        # Should show workspace name
        assert "test-workspace" in html
        # Should show edition badge
        assert "stable" in html


class TestWebappPagination:
    """Tests for pagination in the web interface."""

    @pytest.fixture
    def db_with_many_sessions(self, tmp_path):
        """Create a database with many sessions for pagination testing."""
        db_path = tmp_path / "pagination_test.db"
        db = Database(db_path)

        # Create 25 sessions (more than one page of 20)
        for i in range(25):
            session = ChatSession(
                session_id=f"pagination-session-{i}",
                workspace_name=f"project-{i}",
                workspace_path=f"/path/to/project-{i}",
                messages=[ChatMessage(role="user", content=f"Message {i}")],
                created_at=f"2024-01-{(i % 28) + 1:02d}T10:00:00Z",
                vscode_edition="stable",
            )
            db.add_session(session)

        return db_path

    def test_pagination_shows_on_index(self, db_with_many_sessions):
        """Test that pagination controls appear when there are many sessions."""
        app = create_app(str(db_with_many_sessions))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/")
        html = response.data.decode("utf-8")

        # Should show pagination controls
        assert "pagination" in html
        assert "Showing" in html

    def test_pagination_page_parameter(self, db_with_many_sessions):
        """Test that page parameter works correctly."""
        app = create_app(str(db_with_many_sessions))
        app.config["TESTING"] = True
        client = app.test_client()

        # Page 1
        response = client.get("/?page=1")
        assert response.status_code == 200

        # Page 2
        response = client.get("/?page=2")
        assert response.status_code == 200

    def test_pagination_preserves_query_params(self, db_with_many_sessions):
        """Test that pagination preserves search and filter parameters."""
        app = create_app(str(db_with_many_sessions))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/?q=Message&sort=date&page=1")
        html = response.data.decode("utf-8")

        # Pagination links should preserve query params
        assert response.status_code == 200

    def test_search_help_includes_date_filters(self, db_with_many_sessions):
        """Test that search help tips include date filter documentation."""
        app = create_app(str(db_with_many_sessions))
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/")
        html = response.data.decode("utf-8")

        # Should include date filter documentation
        assert "start_date" in html
        assert "end_date" in html
