"""Tests for the CLI module."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from copilot_chat_archive.cli import main
from copilot_chat_archive.database import Database
from copilot_chat_archive.scanner import ChatMessage, ChatSession


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_no_vscode_paths():
    """Mock get_vscode_storage_paths to return empty list for faster tests."""
    with patch("copilot_chat_archive.cli.get_vscode_storage_paths", return_value=[]):
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
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_scan_no_paths(self, runner, tmp_path, mock_no_vscode_paths):
        """Test scan command with no valid paths."""
        db_path = tmp_path / "test.db"
        result = runner.invoke(main, ["scan", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "Scanning" in result.output

    def test_stats_command(self, runner, temp_db_with_data):
        """Test stats command."""
        result = runner.invoke(main, ["stats", "--db", str(temp_db_with_data)])
        assert result.exit_code == 0
        assert "Sessions: 1" in result.output
        assert "Messages: 2" in result.output

    def test_stats_missing_db(self, runner, tmp_path):
        """Test stats command with missing database."""
        result = runner.invoke(main, ["stats", "--db", str(tmp_path / "missing.db")])
        # Click returns exit code 2 for parameter validation errors (exists=True)
        assert result.exit_code != 0
        assert "does not exist" in result.output or "not found" in result.output or "Invalid value" in result.output

    def test_search_command(self, runner, temp_db_with_data):
        """Test search command."""
        result = runner.invoke(
            main, ["search", "--db", str(temp_db_with_data), "Hello"]
        )
        assert result.exit_code == 0
        # Should find results
        assert "CLI test" in result.output or "result" in result.output.lower()

    def test_search_no_results(self, runner, temp_db_with_data):
        """Test search command with no results."""
        result = runner.invoke(
            main, ["search", "--db", str(temp_db_with_data), "nonexistent term xyz123"]
        )
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_export_command(self, runner, temp_db_with_data):
        """Test export command."""
        result = runner.invoke(main, ["export", "--db", str(temp_db_with_data)])
        assert result.exit_code == 0
        # Output should be valid JSON
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_export_to_file(self, runner, temp_db_with_data, tmp_path):
        """Test export command to file."""
        output_file = tmp_path / "export.json"
        result = runner.invoke(
            main,
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

        result = runner.invoke(
            main, ["import-json", "--db", str(db_path), str(json_file)]
        )
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
        result = runner.invoke(main, ["scan", "--db", str(db_path), "--full"])
        assert result.exit_code == 0
        assert "Full mode" in result.output or "Updated:" in result.output

    def test_scan_incremental_default(self, runner, tmp_path, mock_no_vscode_paths):
        """Test that scan command uses incremental mode by default."""
        db_path = tmp_path / "incremental_test.db"
        
        # Create a database (it will be empty)
        result = runner.invoke(main, ["scan", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "Incremental mode" in result.output

    def test_serve_missing_db(self, runner, tmp_path):
        """Test serve command with missing database."""
        result = runner.invoke(main, ["serve", "--db", str(tmp_path / "missing.db")])
        # Click returns exit code 2 for parameter validation errors (exists=True)
        assert result.exit_code != 0
        assert "does not exist" in result.output or "not found" in result.output or "Invalid value" in result.output

    def test_export_markdown_command(self, runner, temp_db_with_data, tmp_path):
        """Test export-markdown command exports sessions to markdown files."""
        output_dir = tmp_path / "markdown_output"
        
        result = runner.invoke(
            main,
            ["export-markdown", "--db", str(temp_db_with_data), "--output-dir", str(output_dir), "-v"]
        )
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
            main,
            ["export-markdown", "--db", str(temp_db_with_data), "--output-dir", str(output_dir), "--session-id", "cli-test-session"]
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
            main,
            ["export-markdown", "--db", str(temp_db_with_data), "--output-dir", str(output_dir), "--session-id", "nonexistent-session"]
        )
        assert result.exit_code == 1
        assert "not found" in result.output


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
        
        result = runner.invoke(main, ["rebuild", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "Rebuilding" in result.output
        assert "Rebuild complete" in result.output
        assert "Processed: 1" in result.output

    def test_rebuild_command_missing_db(self, runner, tmp_path):
        """Test rebuild command with non-existent database."""
        result = runner.invoke(main, ["rebuild", "--db", str(tmp_path / "nonexistent.db")])
        # Click returns exit code 2 for bad parameter (missing required file)
        assert result.exit_code != 0
        # The error could be from Click or our code depending on the check
        assert "not found" in result.output.lower() or "does not exist" in result.output.lower() or "no such file" in result.output.lower() or result.exit_code == 2

    def test_rebuild_command_empty_db(self, runner, tmp_path):
        """Test rebuild command with empty database (no raw sessions)."""
        db_path = tmp_path / "empty.db"
        # Create an empty database
        Database(db_path)
        
        result = runner.invoke(main, ["rebuild", "--db", str(db_path)])
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
        
        result = runner.invoke(main, ["rebuild", "--db", str(db_path), "--verbose"])
        assert result.exit_code == 0
        # Verbose output shows progress - check for expected patterns
        assert "Processed:" in result.output
        assert "Rebuild complete" in result.output
