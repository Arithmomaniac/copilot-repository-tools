"""Tests for the scanner module."""

import json
import tempfile
from pathlib import Path

import pytest

from copilot_chat_archive.scanner import (
    ChatMessage,
    ChatSession,
    find_copilot_chat_dirs,
    scan_chat_sessions,
)


@pytest.fixture
def mock_workspace_storage(tmp_path):
    """Create a mock VS Code workspace storage structure."""
    # Create workspace directory with hash-like name
    workspace_dir = tmp_path / "abc123def456"
    workspace_dir.mkdir()

    # Create workspace.json
    workspace_json = workspace_dir / "workspace.json"
    workspace_json.write_text(
        json.dumps({"folder": "file:///home/user/projects/test-project"})
    )

    # Create chatSessions directory
    chat_sessions_dir = workspace_dir / "chatSessions"
    chat_sessions_dir.mkdir()

    # Create a sample chat session file
    session_file = chat_sessions_dir / "session-001.json"
    session_data = {
        "sessionId": "session-001",
        "createdAt": "2025-01-15T10:00:00Z",
        "messages": [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
        ],
    }
    session_file.write_text(json.dumps(session_data))

    return tmp_path


class TestScanner:
    """Tests for the scanner module."""

    def test_find_copilot_chat_dirs(self, mock_workspace_storage):
        """Test finding Copilot chat directories."""
        storage_paths = [(str(mock_workspace_storage), "stable")]
        dirs = list(find_copilot_chat_dirs(storage_paths))

        assert len(dirs) >= 1
        # Should find the chatSessions directory
        chat_dir_found = any("chatSessions" in str(d[0]) for d in dirs)
        assert chat_dir_found

    def test_scan_chat_sessions(self, mock_workspace_storage):
        """Test scanning for chat sessions."""
        storage_paths = [(str(mock_workspace_storage), "stable")]
        sessions = list(scan_chat_sessions(storage_paths))

        assert len(sessions) >= 1
        session = sessions[0]
        assert session.session_id == "session-001"
        assert len(session.messages) == 2
        assert session.messages[0].role == "user"
        assert "Python" in session.messages[0].content

    def test_scan_empty_storage(self, tmp_path):
        """Test scanning an empty storage directory."""
        storage_paths = [(str(tmp_path), "stable")]
        sessions = list(scan_chat_sessions(storage_paths))
        assert len(sessions) == 0

    def test_scan_nonexistent_path(self, tmp_path):
        """Test scanning a nonexistent path."""
        storage_paths = [(str(tmp_path / "nonexistent"), "stable")]
        sessions = list(scan_chat_sessions(storage_paths))
        assert len(sessions) == 0


class TestChatMessage:
    """Tests for the ChatMessage dataclass."""

    def test_create_chat_message(self):
        """Test creating a ChatMessage."""
        msg = ChatMessage(role="user", content="Hello", timestamp="2025-01-15T10:00:00Z")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.timestamp == "2025-01-15T10:00:00Z"

    def test_chat_message_defaults(self):
        """Test ChatMessage default values."""
        msg = ChatMessage(role="assistant", content="Hi there")
        assert msg.timestamp is None


class TestChatSession:
    """Tests for the ChatSession dataclass."""

    def test_create_chat_session(self):
        """Test creating a ChatSession."""
        session = ChatSession(
            session_id="test-123",
            workspace_name="my-project",
            workspace_path="/home/user/my-project",
            messages=[
                ChatMessage(role="user", content="Question"),
                ChatMessage(role="assistant", content="Answer"),
            ],
        )
        assert session.session_id == "test-123"
        assert len(session.messages) == 2
        assert session.vscode_edition == "stable"

    def test_chat_session_defaults(self):
        """Test ChatSession default values."""
        session = ChatSession(
            session_id="test-456",
            workspace_name=None,
            workspace_path=None,
            messages=[],
        )
        assert session.created_at is None
        assert session.updated_at is None
        assert session.source_file is None
        assert session.vscode_edition == "stable"
