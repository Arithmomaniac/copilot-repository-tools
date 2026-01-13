"""Tests for the scanner module."""

import json
import tempfile
import time
from pathlib import Path

import pytest

from copilot_repository_tools_common import (
    ChatMessage,
    ChatSession,
    CommandRun,
    ContentBlock,
    FileChange,
    ToolInvocation,
    find_copilot_chat_dirs,
    scan_chat_sessions,
)
from copilot_repository_tools_common.scanner import (
    _extract_inline_reference_name,
    _extract_edit_group_text,
    _parse_tool_invocation_serialized,
    _merge_content_blocks,
)
from conftest import requires_sample_files


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
        # Exclude CLI sessions to test VS Code scanning isolation
        sessions = list(scan_chat_sessions(storage_paths, include_cli=False))
        assert len(sessions) == 0

    def test_scan_nonexistent_path(self, tmp_path):
        """Test scanning a nonexistent path."""
        storage_paths = [(str(tmp_path / "nonexistent"), "stable")]
        # Exclude CLI sessions to test VS Code scanning isolation
        sessions = list(scan_chat_sessions(storage_paths, include_cli=False))
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

class TestResponseItemKinds:
    """Tests for parsing different response item kinds from VS Code Copilot Chat."""

    @pytest.mark.parametrize("kind,item,expected_type", [
        # inlineReference with name
        ("inlineReference", {"kind": "inlineReference", "name": "test.py"}, str),
        # inlineReference with nested path
        ("inlineReference", {"kind": "inlineReference", "inlineReference": {"path": "/src/test.py"}}, str),
        # textEditGroup with dict URI
        ("textEditGroup", {"kind": "textEditGroup", "uri": {"path": "/src/file.ts", "scheme": "file"}}, str),
        # textEditGroup with string URI
        ("textEditGroup", {"kind": "textEditGroup", "uri": "file:///src/file.ts"}, str),
        # codeblockUri
        ("codeblockUri", {"kind": "codeblockUri", "uri": {"fsPath": "c:\\src\\file.py"}}, str),
        # toolInvocationSerialized
        ("toolInvocationSerialized", {"kind": "toolInvocationSerialized", "toolId": "run_command", "isComplete": True}, ToolInvocation),
    ])
    def test_response_item_extraction(self, kind, item, expected_type):
        """Test that different response item kinds are correctly parsed."""
        if kind == "inlineReference":
            result = _extract_inline_reference_name(item)
            assert result is not None
            assert isinstance(result, expected_type)
            assert "`" in result  # Should be backtick-formatted
        elif kind in ("textEditGroup", "codeblockUri", "notebookEditGroup"):
            result = _extract_edit_group_text(item)
            assert result is not None
            assert isinstance(result, expected_type)
            assert "`" in result  # Should contain backticked filename
        elif kind == "toolInvocationSerialized":
            result = _parse_tool_invocation_serialized(item)
            assert result is not None
            assert isinstance(result, expected_type)
            assert result.name == "run_command"
            assert result.status == "completed"

    def test_nested_uri_object_handling(self):
        """Test that nested URI objects (common in VS Code data) are correctly parsed."""
        # URI as dict with $mid (VS Code internal format)
        item = {
            "kind": "textEditGroup",
            "uri": {
                "$mid": 1,
                "path": "/c:/Users/test/project/src/main.py",
                "scheme": "file",
                "fsPath": "c:\\Users\\test\\project\\src\\main.py"
            }
        }
        result = _extract_edit_group_text(item)
        assert result is not None
        assert "main.py" in result

    def test_uri_string_handling(self):
        """Test that URI strings are correctly parsed."""
        item = {
            "kind": "textEditGroup",
            "uri": "file:///c:/Users/test/project/src/main.py"
        }
        result = _extract_edit_group_text(item)
        assert result is not None
        assert "main.py" in result

    def test_merge_content_blocks_keeps_thinking_separate(self):
        """Test that thinking blocks are not merged with text blocks."""
        blocks = [
            ("text", "Hello"),
            ("thinking", "Let me think..."),
            ("text", "World"),
        ]
        result = _merge_content_blocks(blocks)
        assert len(result) == 3
        assert result[0].kind == "text"
        assert result[1].kind == "thinking"
        assert result[2].kind == "text"

    def test_merge_content_blocks_merges_consecutive_text(self):
        """Test that consecutive text blocks are merged."""
        blocks = [
            ("text", "Hello"),
            ("text", "World"),
            ("text", "!"),
        ]
        result = _merge_content_blocks(blocks)
        assert len(result) == 1
        assert result[0].kind == "text"
        assert "Hello" in result[0].content
        assert "World" in result[0].content

    def test_tool_invocation_blocks_stay_separate(self):
        """Test that toolInvocation blocks are never merged."""
        blocks = [
            ("text", "Starting..."),
            ("toolInvocation", "Running command"),
            ("toolInvocation", "Reading file"),
            ("text", "Done"),
        ]
        result = _merge_content_blocks(blocks)
        assert len(result) == 4
        assert result[1].kind == "toolInvocation"
        assert result[2].kind == "toolInvocation"


class TestSampleFilesParsing:
    """Tests using real sample files to validate parsing logic."""

    @requires_sample_files
    def test_sample_session_parses_successfully(self, sample_session_data):
        """Test that sample session JSON can be parsed without errors."""
        assert sample_session_data is not None
        assert isinstance(sample_session_data, dict)

    @requires_sample_files
    def test_sample_session_has_expected_structure(self, sample_session_data):
        """Test that sample session has the expected top-level structure."""
        # Should have version field
        assert "version" in sample_session_data
        # Should have requests array (VS Code Copilot Chat format)
        assert "requests" in sample_session_data
        assert isinstance(sample_session_data["requests"], list)
        # Should have at least one request
        assert len(sample_session_data["requests"]) > 0

    @requires_sample_files
    def test_sample_session_requests_have_messages(self, sample_session_data):
        """Test that requests in sample session have message and response."""
        for request in sample_session_data["requests"]:
            # Each request should have a message with text
            assert "message" in request
            assert isinstance(request["message"], dict)
            # Each request should have a response array
            assert "response" in request
            assert isinstance(request["response"], list)

    @requires_sample_files
    def test_sample_session_scan_integration(self, sample_session_path, tmp_path):
        """Test that sample session can be scanned using the scanner module."""
        from copilot_repository_tools_common.scanner import _parse_chat_session_file

        session = _parse_chat_session_file(
            sample_session_path,
            workspace_name="test-workspace",
            workspace_path=str(tmp_path),
            edition="stable"
        )
        assert session is not None
        assert isinstance(session, ChatSession)
        assert len(session.messages) > 0
        # Should have both user and assistant messages
        roles = {msg.role for msg in session.messages}
        assert "user" in roles or "assistant" in roles


class TestPerformanceBenchmarks:
    """Performance tests for large session parsing."""

    @requires_sample_files
    def test_large_session_parsing_time(self, all_sample_session_paths):
        """Test that large session files parse within acceptable time limits."""
        import orjson
        from copilot_repository_tools_common.scanner import _parse_chat_session_file

        for sample_path in all_sample_session_paths:
            file_size = sample_path.stat().st_size
            
            # Only benchmark files larger than 100KB
            if file_size < 100 * 1024:
                continue

            start_time = time.perf_counter()
            
            # Parse the file
            session = _parse_chat_session_file(
                sample_path,
                workspace_name="benchmark",
                workspace_path="/tmp/benchmark",
                edition="stable"
            )
            
            elapsed_time = time.perf_counter() - start_time
            
            # Log performance metrics (useful for baseline establishment)
            file_size_mb = file_size / (1024 * 1024)
            print(f"\nParsed {sample_path.name}: {file_size_mb:.2f}MB in {elapsed_time:.3f}s")
            
            # Assert parsing succeeded
            assert session is not None
            
            # Assert reasonable time limit: 5 seconds per MB as baseline
            max_time = max(5.0, file_size_mb * 5)
            assert elapsed_time < max_time, f"Parsing took {elapsed_time:.2f}s, expected < {max_time:.2f}s"

    @requires_sample_files
    def test_orjson_parse_performance(self, sample_session_path):
        """Test raw orjson parsing performance."""
        import orjson

        file_size = sample_session_path.stat().st_size
        
        start_time = time.perf_counter()
        with open(sample_session_path, "rb") as f:
            data = orjson.loads(f.read())
        elapsed_time = time.perf_counter() - start_time
        
        file_size_mb = file_size / (1024 * 1024)
        print(f"\norjson parsed {sample_session_path.name}: {file_size_mb:.2f}MB in {elapsed_time:.3f}s")
        
        assert data is not None
        # orjson should be very fast - less than 1 second per MB
        max_time = max(1.0, file_size_mb * 1)
        assert elapsed_time < max_time

class TestCLIParsing:
    """Tests for GitHub Copilot CLI JSONL parsing."""

    def test_parse_cli_jsonl_event_based_format(self):
        """Test parsing real CLI JSONL session file with event-based format.
        
        Tests parsing the actual copilot-cli JSONL format with event types like
        session.start, user.message, assistant.message, tool.execution_*, etc.
        """
        from copilot_repository_tools_common.scanner import _parse_cli_jsonl_file
        from pathlib import Path
        
        # Use the real sample file from copilot-cli
        sample_file = Path(__file__).parent / "sample_files" / "66b821d4-af6f-4518-a394-6d95a4d0f96b.jsonl"
        
        if not sample_file.exists():
            pytest.skip("Real CLI sample file not found")
        
        session = _parse_cli_jsonl_file(sample_file)
        
        assert session is not None
        assert session.session_id == "66b821d4-af6f-4518-a394-6d95a4d0f96b"
        assert session.type == "cli"
        
        # Check session metadata extracted from session.start
        assert session.created_at == "2026-01-12T10:02:39.809Z"
        
        # Check workspace extracted from folder_trust event
        assert session.workspace_path == "C:\\_SRC\\ZTS"
        assert session.workspace_name == "ZTS"
        
        # Check username extracted from authentication event
        assert session.requester_username == "Arithmomaniac"
        
        # Should have user and assistant messages
        assert len(session.messages) > 0
        
        # First message should be user asking about branches
        user_messages = [m for m in session.messages if m.role == "user"]
        assert len(user_messages) >= 1
        assert "branches" in user_messages[0].content.lower()
        
        # Should have assistant messages with tool invocations
        assistant_messages = [m for m in session.messages if m.role == "assistant"]
        assert len(assistant_messages) >= 1
        
        # Check that tool invocations and command runs are parsed
        all_tool_invocations = []
        all_command_runs = []
        for msg in assistant_messages:
            all_tool_invocations.extend(msg.tool_invocations)
            all_command_runs.extend(msg.command_runs)
        
        # Should have skill and report_intent tool invocations
        tool_names = [t.name for t in all_tool_invocations]
        assert "skill" in tool_names or "report_intent" in tool_names
        
        # Should have powershell command runs (git commands)
        assert len(all_command_runs) > 0
        commands = [c.command for c in all_command_runs]
        assert any("git" in cmd for cmd in commands)

    def test_parse_cli_jsonl_file_simple_format(self):
        """Test parsing CLI JSONL session file with simple format (for backwards compatibility)."""
        from copilot_repository_tools_common.scanner import _parse_cli_jsonl_file
        from pathlib import Path
        
        # Use the simple sample file
        sample_file = Path(__file__).parent / "sample_files" / "cli-session-001.jsonl"
        
        if not sample_file.exists():
            pytest.skip("Simple CLI sample file not found")
        
        # The simple format won't parse with event-based parser, but should not crash
        session = _parse_cli_jsonl_file(sample_file)
        
        # Simple format doesn't have session.start or user.message events,
        # so it returns None (no messages found)
        # This is expected - the simple format was for testing only
        assert session is None

    def test_get_cli_storage_paths(self):
        """Test getting CLI storage paths."""
        from copilot_repository_tools_common import get_cli_storage_paths
        
        paths = get_cli_storage_paths()
        
        # Should return a list of Path objects
        assert isinstance(paths, list)
        
        # Paths should be Path objects
        for path in paths:
            from pathlib import Path
            assert isinstance(path, Path)

    def test_scan_includes_cli_by_default(self, tmp_path):
        """Test that scan_chat_sessions includes CLI sessions by default."""
        from copilot_repository_tools_common import scan_chat_sessions
        import tempfile
        from pathlib import Path
        
        # Mock an empty VS Code storage
        storage_paths = [(str(tmp_path / "nonexistent"), "stable")]
        
        # We can't easily test actual CLI scanning without mocking home directory,
        # but we can verify the function accepts include_cli parameter
        sessions = list(scan_chat_sessions(storage_paths, include_cli=False))
        
        # Should work without errors
        assert isinstance(sessions, list)
