"""Tests for the CLI module."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from copilot_session_tools import ChatMessage, ChatSession, Database, __version__
from copilot_session_tools.cli import _default_db_path, _ensure_db_exists, app


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_no_vscode_paths():
    """Mock storage paths to return empty for faster tests.

    We patch at the CLI module level since that's where scan_chat_sessions is imported.
    """
    with patch("copilot_session_tools.cli.get_vscode_storage_paths", return_value=[]), patch("copilot_session_tools.cli.scan_chat_sessions", return_value=iter([])):
        yield


@pytest.fixture
def temp_db_with_data(tmp_path):
    """Create a temporary database with sample data."""
    db_path = tmp_path / "test.db"
    db = Database(db_path)

    session = ChatSession(
        session_id="cli-test-session",
        workspace_name="cli-workspace",
        workspace_path="/home/user/cli-test",
        messages=[
            ChatMessage(role="user", content="Hello from CLI test"),
            ChatMessage(role="assistant", content="Hi there!"),
        ],
        created_at="2025-01-15T10:00:00Z",
        vscode_edition="stable",
    )
    db.add_session(session)

    return db_path


class TestCLI:
    """Tests for the CLI commands."""

    def test_version(self, runner):
        """Test --version flag."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_scan_no_paths(self, runner, tmp_path, mock_no_vscode_paths):
        """Test scan command with no valid paths."""
        db_path = tmp_path / "test.db"
        result = runner.invoke(app, ["scan", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "Scanning" in result.output

    def test_stats_command(self, runner, temp_db_with_data):
        """Test stats command."""
        result = runner.invoke(app, ["stats", "--db", str(temp_db_with_data)])
        assert result.exit_code == 0
        assert "Sessions: 1" in result.output
        assert "Messages: 2" in result.output

    def test_stats_missing_db(self, runner, tmp_path):
        """Test stats command with missing database."""
        result = runner.invoke(app, ["stats", "--db", str(tmp_path / "missing.db")])
        # Typer returns non-zero exit code for parameter validation errors (exists=True)
        assert result.exit_code != 0
        assert "does not exist" in result.output or "not found" in result.output or "Invalid value" in result.output

    def test_search_command(self, runner, temp_db_with_data):
        """Test search command."""
        result = runner.invoke(app, ["search", "--db", str(temp_db_with_data), "Hello"])
        assert result.exit_code == 0
        # Should find results
        assert "CLI test" in result.output or "result" in result.output.lower()

    def test_search_no_results(self, runner, temp_db_with_data):
        """Test search command with no results."""
        result = runner.invoke(app, ["search", "--db", str(temp_db_with_data), "nonexistent term xyz123"])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_search_with_unicode_content(self, runner, tmp_path):
        """Test search works with Unicode content (regression test for cp1252 crash).

        When stdout is piped on Windows, Rich falls back to the system encoding
        (cp1252) which can't handle Unicode. This test ensures the CLI handles
        Unicode content without crashing in non-TTY contexts.
        """
        db_path = tmp_path / "unicode_test.db"
        db = Database(db_path)

        session = ChatSession(
            session_id="unicode-test-session",
            workspace_name="unicode-workspace",
            workspace_path="/home/user/test",
            messages=[
                ChatMessage(role="user", content="Show me emojis 🎉 and symbols ━━━"),
                ChatMessage(role="assistant", content="Here: café, naïve, 日本語, 🚀✨"),
            ],
            created_at="2025-01-15T10:00:00Z",
            vscode_edition="stable",
        )
        db.add_session(session)

        result = runner.invoke(app, ["search", "--db", str(db_path), "emojis", "--full"])
        assert result.exit_code == 0
        assert "Result" in result.output

    def test_export_command(self, runner, temp_db_with_data):
        """Test export command."""
        result = runner.invoke(app, ["export", "--db", str(temp_db_with_data)])
        assert result.exit_code == 0
        # Output should be valid JSON
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_export_to_file(self, runner, temp_db_with_data, tmp_path):
        """Test export command to file."""
        output_file = tmp_path / "export.json"
        result = runner.invoke(
            app,
            ["export", "--db", str(temp_db_with_data), "--output", str(output_file)],
        )
        assert result.exit_code == 0
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert len(data) == 1

    def test_import_json_command(self, runner, tmp_path):
        """Test import-json command."""
        db_path = tmp_path / "import.db"
        json_file = tmp_path / "sessions.json"

        # Create sample JSON
        sessions = [
            {
                "session_id": "imported-session",
                "workspace_name": "imported-workspace",
                "messages": [
                    {"role": "user", "content": "Imported message"},
                ],
            }
        ]
        json_file.write_text(json.dumps(sessions))

        result = runner.invoke(app, ["import-json", "--db", str(db_path), str(json_file)])
        assert result.exit_code == 0
        assert "Added: 1" in result.output

        # Verify import
        db = Database(db_path)
        stats = db.get_stats()
        assert stats["session_count"] == 1

    def test_scan_full_flag(self, runner, tmp_path, mock_no_vscode_paths):
        """Test scan command with --full flag."""
        db_path = tmp_path / "full_test.db"

        # First, create a database with an existing session
        db = Database(db_path)
        session = ChatSession(
            session_id="full-test-session",
            workspace_name="full-workspace",
            workspace_path="/home/user/full-test",
            messages=[
                ChatMessage(role="user", content="Original message"),
            ],
            created_at="2025-01-15T10:00:00Z",
            vscode_edition="stable",
        )
        db.add_session(session)

        # Test that --full flag is recognized
        result = runner.invoke(app, ["scan", "--db", str(db_path), "--full"])
        assert result.exit_code == 0
        assert "Full mode" in result.output or "Updated:" in result.output

    def test_scan_incremental_default(self, runner, tmp_path, mock_no_vscode_paths):
        """Test that scan command uses incremental mode by default."""
        db_path = tmp_path / "incremental_test.db"

        # Create a database (it will be empty)
        result = runner.invoke(app, ["scan", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "Incremental mode" in result.output

    def test_export_markdown_command(self, runner, temp_db_with_data, tmp_path):
        """Test export-markdown command exports sessions to markdown files."""
        output_dir = tmp_path / "markdown_output"

        result = runner.invoke(app, ["export-markdown", "--db", str(temp_db_with_data), "--output-dir", str(output_dir), "-v"])
        assert result.exit_code == 0
        assert "Exported 1 sessions" in result.output

        # Check that a markdown file was created
        md_files = list(output_dir.glob("*.md"))
        assert len(md_files) == 1

        # Check content of the markdown file
        content = md_files[0].read_text()
        assert "# Chat Session" in content
        assert "cli-workspace" in content
        assert "cli-test-session" in content
        assert "Message 1:" in content
        assert "USER" in content
        assert "ASSISTANT" in content

    def test_export_markdown_single_session(self, runner, temp_db_with_data, tmp_path):
        """Test export-markdown command with specific session ID."""
        output_dir = tmp_path / "markdown_output"

        result = runner.invoke(
            app,
            [
                "export-markdown",
                "--db",
                str(temp_db_with_data),
                "--output-dir",
                str(output_dir),
                "--session-id",
                "cli-test-session",
            ],
        )
        assert result.exit_code == 0
        assert "Exported:" in result.output

        # Check that exactly one markdown file was created
        md_files = list(output_dir.glob("*.md"))
        assert len(md_files) == 1

    def test_export_markdown_missing_session(self, runner, temp_db_with_data, tmp_path):
        """Test export-markdown command with non-existent session ID."""
        output_dir = tmp_path / "markdown_output"

        result = runner.invoke(
            app,
            [
                "export-markdown",
                "--db",
                str(temp_db_with_data),
                "--output-dir",
                str(output_dir),
                "--session-id",
                "nonexistent-session",
            ],
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_export_html_command(self, runner, temp_db_with_data, tmp_path):
        """Test export-html command exports sessions to HTML files."""
        output_dir = tmp_path / "html_output"

        result = runner.invoke(app, ["export-html", "--db", str(temp_db_with_data), "--output-dir", str(output_dir), "-v"])
        assert result.exit_code == 0
        assert "Exported 1 sessions" in result.output

        # Check that an HTML file was created
        html_files = list(output_dir.glob("*.html"))
        assert len(html_files) == 1

        # Check content of the HTML file
        content = html_files[0].read_text()
        assert "<!DOCTYPE html>" in content
        assert "cli-workspace" in content
        assert "cli-test-session" in content

    def test_export_html_single_session(self, runner, temp_db_with_data, tmp_path):
        """Test export-html command with specific session ID."""
        output_dir = tmp_path / "html_output"

        result = runner.invoke(
            app,
            [
                "export-html",
                "--db",
                str(temp_db_with_data),
                "--output-dir",
                str(output_dir),
                "--session-id",
                "cli-test-session",
            ],
        )
        assert result.exit_code == 0
        assert "Exported:" in result.output

        html_files = list(output_dir.glob("*.html"))
        assert len(html_files) == 1

    def test_export_html_missing_session(self, runner, temp_db_with_data, tmp_path):
        """Test export-html command with non-existent session ID."""
        output_dir = tmp_path / "html_output"

        result = runner.invoke(
            app,
            [
                "export-html",
                "--db",
                str(temp_db_with_data),
                "--output-dir",
                str(output_dir),
                "--session-id",
                "nonexistent-session",
            ],
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_export_html_static_mode(self, runner, temp_db_with_data, tmp_path):
        """Test that export-html output has no interactive elements."""
        output_dir = tmp_path / "html_output"

        runner.invoke(app, ["export-html", "--db", str(temp_db_with_data), "--output-dir", str(output_dir)])

        html_files = list(output_dir.glob("*.html"))
        content = html_files[0].read_text()

        # Should NOT contain interactive elements
        assert '<div class="copy-markdown-toolbar">' not in content
        assert 'class="message-copy-btn"' not in content
        assert "cdnjs.cloudflare.com" not in content
        assert "buildMarkdownParams" not in content
        assert "Back to all sessions" not in content

        # SHOULD contain core content
        assert "<!DOCTYPE html>" in content
        assert "message-content" in content
        assert "--container-max-width: none" in content


class TestRebuildCommand:
    """Tests for the rebuild CLI command."""

    def test_rebuild_command_success(self, runner, tmp_path):
        """Test rebuild command with valid database."""
        db_path = tmp_path / "rebuild_test.db"
        db = Database(db_path)

        # Add a session with raw JSON
        raw_json = b'{"sessionId": "rebuild-cli-test", "requests": [{"message": {"text": "Hello"}, "response": [{"kind": "text", "value": "Hi"}]}]}'
        session = ChatSession(
            session_id="rebuild-cli-test",
            workspace_name="rebuild-workspace",
            workspace_path="/rebuild/path",
            messages=[
                ChatMessage(role="user", content="Hello"),
                ChatMessage(role="assistant", content="Hi"),
            ],
            raw_json=raw_json,
        )
        db.add_session(session)

        result = runner.invoke(app, ["rebuild", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "Rebuilding" in result.output
        assert "Rebuild complete" in result.output
        assert "Processed: 1" in result.output

    def test_rebuild_command_missing_db(self, runner, tmp_path):
        """Test rebuild command with non-existent database."""
        result = runner.invoke(app, ["rebuild", "--db", str(tmp_path / "nonexistent.db")])
        # Typer returns exit code 2 for validation errors (exists=True on file path)
        assert result.exit_code == 2

    def test_rebuild_command_empty_db(self, runner, tmp_path):
        """Test rebuild command with empty database (no raw sessions)."""
        db_path = tmp_path / "empty.db"
        # Create an empty database
        Database(db_path)

        result = runner.invoke(app, ["rebuild", "--db", str(db_path)])
        assert result.exit_code == 1
        assert "No raw sessions found" in result.output

    def test_rebuild_command_verbose(self, runner, tmp_path):
        """Test rebuild command with verbose flag."""
        db_path = tmp_path / "verbose_test.db"
        db = Database(db_path)

        raw_json = b'{"sessionId": "verbose-test", "requests": [{"message": {"text": "Test"}, "response": []}]}'
        session = ChatSession(
            session_id="verbose-test",
            workspace_name="test",
            workspace_path="/test",
            messages=[ChatMessage(role="user", content="Test")],
            raw_json=raw_json,
        )
        db.add_session(session)

        result = runner.invoke(app, ["rebuild", "--db", str(db_path), "--verbose"])
        assert result.exit_code == 0
        # Verbose output shows progress - check for expected patterns
        assert "Processed:" in result.output
        assert "Rebuild complete" in result.output


class TestOptimizeCommand:
    """Tests for the optimize CLI command."""

    def test_optimize_command_success(self, runner, tmp_path):
        """Test optimize command with valid database."""
        db_path = tmp_path / "optimize_test.db"
        db = Database(db_path)

        # Add a session to have some data in FTS index
        session = ChatSession(
            session_id="optimize-cli-test",
            workspace_name="optimize-workspace",
            workspace_path="/optimize/path",
            messages=[
                ChatMessage(role="user", content="Test for optimization"),
                ChatMessage(role="assistant", content="Response for optimization"),
            ],
        )
        db.add_session(session)

        result = runner.invoke(app, ["optimize", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "Optimizing" in result.output
        assert "Optimization complete" in result.output
        assert "Index segments" in result.output

    def test_optimize_command_missing_db(self, runner, tmp_path):
        """Test optimize command with non-existent database."""
        result = runner.invoke(app, ["optimize", "--db", str(tmp_path / "nonexistent.db")])
        # Typer returns exit code 2 for validation errors (exists=True on file path)
        assert result.exit_code == 2


class TestDefaultDbPath:
    """Tests for _default_db_path() helper."""

    def test_returns_path_in_home_directory(self):
        """Default DB path should be under user's home directory."""
        result = _default_db_path()
        assert result.parent.parent == Path.home()
        assert result.name == "copilot_chats.db"
        assert result.parent.name == ".copilot-session-tools"

    def test_returns_path_object(self):
        """Should return a Path object, not a string."""
        result = _default_db_path()
        assert isinstance(result, Path)


class TestEnsureDbExists:
    """Tests for _ensure_db_exists() helper."""

    def test_raises_exit_when_db_missing(self, tmp_path):
        """Should raise typer.Exit when database doesn't exist."""
        import typer

        nonexistent = tmp_path / "nonexistent.db"
        with pytest.raises(typer.Exit):
            _ensure_db_exists(nonexistent)

    def test_no_error_when_db_exists(self, tmp_path):
        """Should not raise when database exists."""
        existing = tmp_path / "test.db"
        existing.touch()
        _ensure_db_exists(existing)  # Should not raise
