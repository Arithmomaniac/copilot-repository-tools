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


class TestToolInvocationsAndFileChanges:
    """Tests for the new tool invocation and file change data structures."""

    def test_chat_message_with_tool_invocations(self):
        """Test ChatMessage with tool invocations."""
        from copilot_chat_archive.scanner import ToolInvocation

        msg = ChatMessage(
            role="assistant",
            content="Running a command...",
            tool_invocations=[
                ToolInvocation(
                    name="run_command",
                    input="ls -la",
                    result="file1.txt\nfile2.txt",
                    status="success",
                )
            ],
        )
        assert len(msg.tool_invocations) == 1
        assert msg.tool_invocations[0].name == "run_command"
        assert msg.tool_invocations[0].status == "success"

    def test_chat_message_with_file_changes(self):
        """Test ChatMessage with file changes."""
        from copilot_chat_archive.scanner import FileChange

        msg = ChatMessage(
            role="assistant",
            content="Made some changes...",
            file_changes=[
                FileChange(
                    path="/home/user/project/main.py",
                    diff="+ added line",
                    language_id="python",
                )
            ],
        )
        assert len(msg.file_changes) == 1
        assert msg.file_changes[0].path == "/home/user/project/main.py"
        assert msg.file_changes[0].language_id == "python"

    def test_chat_message_with_command_runs(self):
        """Test ChatMessage with command runs."""
        from copilot_chat_archive.scanner import CommandRun

        msg = ChatMessage(
            role="assistant",
            content="Executing...",
            command_runs=[
                CommandRun(
                    command="npm install",
                    title="Install dependencies",
                    status="success",
                    output="added 100 packages",
                )
            ],
        )
        assert len(msg.command_runs) == 1
        assert msg.command_runs[0].command == "npm install"
        assert msg.command_runs[0].status == "success"

    def test_chat_session_with_extended_fields(self):
        """Test ChatSession with new extended fields."""
        session = ChatSession(
            session_id="test-extended",
            workspace_name="my-project",
            workspace_path="/home/user/my-project",
            messages=[],
            custom_title="My Important Chat",
            requester_username="user",
            responder_username="copilot",
        )
        assert session.custom_title == "My Important Chat"
        assert session.requester_username == "user"
        assert session.responder_username == "copilot"


class TestCloudSessions:
    """Tests for cloud session scanning functionality."""

    def test_chat_session_source_default(self):
        """Test that ChatSession defaults to local source."""
        session = ChatSession(
            session_id="test-local",
            workspace_name="my-project",
            workspace_path="/home/user/my-project",
            messages=[],
        )
        assert session.session_source == "local"

    def test_chat_session_cloud_source(self):
        """Test creating a ChatSession with cloud source."""
        session = ChatSession(
            session_id="test-cloud",
            workspace_name=None,
            workspace_path=None,
            messages=[ChatMessage(role="user", content="Hello from cloud")],
            session_source="cloud",
        )
        assert session.session_source == "cloud"
        assert session.workspace_name is None

    def test_get_vscode_global_storage_paths(self):
        """Test getting global storage paths."""
        from copilot_chat_archive.scanner import get_vscode_global_storage_paths
        
        paths = get_vscode_global_storage_paths()
        assert len(paths) >= 1
        
        # Each path should be a tuple of (path, edition)
        for path, edition in paths:
            assert isinstance(path, str)
            assert edition in ("stable", "insider")
            # Paths should include github.copilot-chat
            assert "github.copilot-chat" in path

    def test_scan_cloud_sessions_empty_path(self, tmp_path):
        """Test scanning an empty cloud storage directory."""
        from copilot_chat_archive.scanner import scan_cloud_sessions
        
        storage_paths = [(str(tmp_path), "stable")]
        sessions = list(scan_cloud_sessions(storage_paths))
        assert len(sessions) == 0

    def test_scan_cloud_sessions_nonexistent_path(self, tmp_path):
        """Test scanning a nonexistent cloud storage path."""
        from copilot_chat_archive.scanner import scan_cloud_sessions
        
        storage_paths = [(str(tmp_path / "nonexistent"), "stable")]
        sessions = list(scan_cloud_sessions(storage_paths))
        assert len(sessions) == 0

    def test_scan_cloud_sessions_with_json_files(self, tmp_path):
        """Test scanning cloud sessions from JSON files."""
        import json
        from copilot_chat_archive.scanner import scan_cloud_sessions
        
        # Create a cloud session JSON file directly in the storage dir
        session_data = {
            "sessionId": "cloud-session-001",
            "createdAt": "2025-01-15T10:00:00Z",
            "messages": [
                {"role": "user", "content": "Hello from cloud"},
                {"role": "assistant", "content": "Cloud response here"},
            ],
        }
        session_file = tmp_path / "cloud-session-001.json"
        session_file.write_text(json.dumps(session_data))
        
        storage_paths = [(str(tmp_path), "stable")]
        sessions = list(scan_cloud_sessions(storage_paths))
        
        assert len(sessions) >= 1
        session = sessions[0]
        assert session.session_id == "cloud-session-001"
        assert session.session_source == "cloud"
        assert session.workspace_name is None
        assert len(session.messages) == 2

    def test_scan_cloud_sessions_from_cloud_sessions_subdir(self, tmp_path):
        """Test scanning cloud sessions from cloudSessions subdirectory."""
        import json
        from copilot_chat_archive.scanner import scan_cloud_sessions
        
        # Create cloudSessions subdirectory
        cloud_sessions_dir = tmp_path / "cloudSessions"
        cloud_sessions_dir.mkdir()
        
        session_data = {
            "sessionId": "cloud-session-002",
            "createdAt": "2025-01-16T11:00:00Z",
            "requests": [
                {
                    "message": {"text": "Coding agent task"},
                    "response": [{"value": "Working on your task..."}],
                },
            ],
        }
        session_file = cloud_sessions_dir / "session-002.json"
        session_file.write_text(json.dumps(session_data))
        
        storage_paths = [(str(tmp_path), "insider")]
        sessions = list(scan_cloud_sessions(storage_paths))
        
        assert len(sessions) >= 1
        # Find the cloud session
        cloud_session = next((s for s in sessions if s.session_id == "cloud-session-002"), None)
        assert cloud_session is not None
        assert cloud_session.session_source == "cloud"
        assert cloud_session.vscode_edition == "insider"
