"""Tests for the CLI module."""

import json
import tempfile
from pathlib import Path

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

    def test_scan_no_paths(self, runner, tmp_path):
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

    def test_generate_command(self, runner, temp_db_with_data, tmp_path):
        """Test generate command."""
        output_dir = tmp_path / "output"
        result = runner.invoke(
            main,
            [
                "generate",
                "--db", str(temp_db_with_data),
                "--output", str(output_dir),
                "--title", "Test Archive",
            ],
        )
        assert result.exit_code == 0
        assert "Archive generated" in result.output
        assert (output_dir / "index.html").exists()

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
