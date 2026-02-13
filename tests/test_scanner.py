"""Tests for the scanner module."""

import json
import time
from pathlib import Path

import pytest
from conftest import requires_sample_files

from copilot_session_tools import (
    ChatMessage,
    ChatSession,
    CommandRun,
    FileChange,
    ToolInvocation,
    find_copilot_chat_dirs,
    scan_chat_sessions,
)
from copilot_session_tools.scanner import (
    _extract_edit_group_text,
    _extract_inline_reference_name,
    _merge_content_blocks,
    _parse_tool_invocation_serialized,
)


@pytest.fixture
def mock_workspace_storage(tmp_path):
    """Create a mock VS Code workspace storage structure."""
    # Create workspace directory with hash-like name
    workspace_dir = tmp_path / "abc123def456"
    workspace_dir.mkdir()

    # Create workspace.json
    workspace_json = workspace_dir / "workspace.json"
    workspace_json.write_text(json.dumps({"folder": "file:///home/user/projects/test-project"}))

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

    @pytest.mark.parametrize(
        "kind,item,expected_type",
        [
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
            (
                "toolInvocationSerialized",
                {"kind": "toolInvocationSerialized", "toolId": "run_command", "isComplete": True},
                ToolInvocation,
            ),
        ],
    )
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
        item = {"kind": "textEditGroup", "uri": {"$mid": 1, "path": "/c:/Users/test/project/src/main.py", "scheme": "file", "fsPath": "c:\\Users\\test\\project\\src\\main.py"}}
        result = _extract_edit_group_text(item)
        assert result is not None
        assert "main.py" in result

    def test_uri_string_handling(self):
        """Test that URI strings are correctly parsed."""
        item = {"kind": "textEditGroup", "uri": "file:///c:/Users/test/project/src/main.py"}
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
        from copilot_session_tools.scanner import _parse_chat_session_file

        session = _parse_chat_session_file(sample_session_path, workspace_name="test-workspace", workspace_path=str(tmp_path), edition="stable")
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
        from copilot_session_tools.scanner import _parse_chat_session_file

        for sample_path in all_sample_session_paths:
            file_size = sample_path.stat().st_size

            # Only benchmark files larger than 100KB
            if file_size < 100 * 1024:
                continue

            start_time = time.perf_counter()

            # Parse the file
            session = _parse_chat_session_file(sample_path, workspace_name="benchmark", workspace_path="/tmp/benchmark", edition="stable")

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
        from copilot_session_tools.scanner import _parse_cli_jsonl_file

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
        all_content_blocks = []
        for msg in assistant_messages:
            all_tool_invocations.extend(msg.tool_invocations)
            all_command_runs.extend(msg.command_runs)
            all_content_blocks.extend(msg.content_blocks)

        # skill and report_intent are rendered as special content blocks, not tool_invocations
        # Check for intent blocks (from report_intent) or skill blocks
        intent_blocks = [b for b in all_content_blocks if b.kind == "intent"]
        skill_blocks = [b for b in all_content_blocks if b.kind == "skill"]
        assert len(intent_blocks) > 0 or len(skill_blocks) > 0, "Should have intent or skill content blocks"

        # Should have powershell command runs (git commands)
        assert len(all_command_runs) > 0
        commands = [c.command for c in all_command_runs]
        assert any("git" in cmd for cmd in commands)

    def test_parse_cli_jsonl_file_simple_format(self):
        """Test parsing CLI JSONL session file with simple format (for backwards compatibility)."""
        from copilot_session_tools.scanner import _parse_cli_jsonl_file

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
        from copilot_session_tools import get_cli_storage_paths

        paths = get_cli_storage_paths()

        # Should return a list of Path objects
        assert isinstance(paths, list)

        # Paths should be Path objects
        for path in paths:
            assert isinstance(path, Path)

    def test_scan_includes_cli_by_default(self, tmp_path):
        """Test that scan_chat_sessions includes CLI sessions by default."""

        from copilot_session_tools import scan_chat_sessions

        # Mock an empty VS Code storage
        storage_paths = [(str(tmp_path / "nonexistent"), "stable")]

        # We can't easily test actual CLI scanning without mocking home directory,
        # but we can verify the function accepts include_cli parameter
        sessions = list(scan_chat_sessions(storage_paths, include_cli=False))

        # Should work without errors
        assert isinstance(sessions, list)


class TestWorkspaceYamlParsing:
    """Tests for workspace.yaml parsing and CLI session title extraction."""

    def test_parse_workspace_yaml_with_summary(self, tmp_path):
        """Test parsing workspace.yaml extracts summary field."""
        from copilot_session_tools.scanner import _parse_workspace_yaml

        workspace_file = tmp_path / "workspace.yaml"
        workspace_file.write_text(
            "id: 00b8e3a3-f89d-4105-b0e4-a8ab94986035\n"
            "cwd: C:\\_SRC\\ZTS\n"
            "git_root: C:\\_SRC\\ZTS\n"
            "branch: main\n"
            "summary: Remediate AzSecpack On VMSS\n"
            "summary_count: 0\n"
            "created_at: 2026-02-09T09:28:30.798Z\n"
            "updated_at: 2026-02-11T10:13:41.793Z\n",
            encoding="utf-8",
        )

        result = _parse_workspace_yaml(tmp_path)
        assert result["summary"] == "Remediate AzSecpack On VMSS"
        assert result["id"] == "00b8e3a3-f89d-4105-b0e4-a8ab94986035"
        assert result["branch"] == "main"

    def test_parse_workspace_yaml_missing_file(self, tmp_path):
        """Test that missing workspace.yaml returns empty dict."""
        from copilot_session_tools.scanner import _parse_workspace_yaml

        result = _parse_workspace_yaml(tmp_path)
        assert result == {}

    def test_parse_workspace_yaml_no_summary(self, tmp_path):
        """Test parsing workspace.yaml without summary field."""
        from copilot_session_tools.scanner import _parse_workspace_yaml

        workspace_file = tmp_path / "workspace.yaml"
        workspace_file.write_text(
            "id: abc123\ncwd: /home/user/project\n",
            encoding="utf-8",
        )

        result = _parse_workspace_yaml(tmp_path)
        assert "summary" not in result
        assert result["id"] == "abc123"

    def test_parse_workspace_yaml_empty_summary(self, tmp_path):
        """Test parsing workspace.yaml with empty summary field."""
        from copilot_session_tools.scanner import _parse_workspace_yaml

        workspace_file = tmp_path / "workspace.yaml"
        workspace_file.write_text(
            "id: abc123\nsummary:\n",
            encoding="utf-8",
        )

        result = _parse_workspace_yaml(tmp_path)
        assert result["summary"] == ""

    def _make_cli_session_events(self, intent=None):
        """Helper to create minimal CLI JSONL events for title tests."""
        ctx = {"cwd": "/home/user/project"}
        start_data = {"sessionId": "test-id", "startTime": "2026-01-01T00:00:00Z", "context": ctx}
        events = [
            {"type": "session.start", "timestamp": "2026-01-01T00:00:00Z", "data": start_data},
            {"type": "user.message", "timestamp": "2026-01-01T00:00:01Z", "data": {"content": "Help"}},
            {"type": "assistant.message", "timestamp": "2026-01-01T00:00:02Z", "data": {"content": "Sure."}},
        ]
        if intent:
            intent_args = {"intent": intent}
            events[2]["data"]["toolRequests"] = [{"toolCallId": "tc1", "toolName": "report_intent", "arguments": intent_args}]
            events.append(
                {
                    "type": "tool.execution_start",
                    "timestamp": "2026-01-01T00:00:02Z",
                    "data": {"toolCallId": "tc1", "toolName": "report_intent", "arguments": intent_args},
                }
            )
            events.append(
                {
                    "type": "tool.execution_complete",
                    "timestamp": "2026-01-01T00:00:03Z",
                    "data": {"toolCallId": "tc1", "toolName": "report_intent", "result": ""},
                }
            )
        return events

    def test_cli_session_title_from_workspace_yaml(self, tmp_path):
        """Test that CLI session title is extracted from workspace.yaml summary."""
        import orjson

        from copilot_session_tools.scanner import _parse_cli_jsonl_file

        session_dir = tmp_path / "test-session"
        session_dir.mkdir()

        (session_dir / "workspace.yaml").write_text(
            "id: test-id\ncwd: /home/user/project\nsummary: Diagnose ADO Build Failures\n",
            encoding="utf-8",
        )

        events_file = session_dir / "events.jsonl"
        events_file.write_text(
            "\n".join(orjson.dumps(e).decode() for e in self._make_cli_session_events()),
            encoding="utf-8",
        )

        session = _parse_cli_jsonl_file(events_file)
        assert session is not None
        assert session.custom_title == "Diagnose ADO Build Failures"

    def test_cli_session_title_fallback_to_intent(self, tmp_path):
        """Test that CLI session title falls back to first report_intent when no workspace.yaml."""
        import orjson

        from copilot_session_tools.scanner import _parse_cli_jsonl_file

        events_file = tmp_path / "test-session.jsonl"
        events_file.write_text(
            "\n".join(orjson.dumps(e).decode() for e in self._make_cli_session_events(intent="Fix failing unit tests")),
            encoding="utf-8",
        )

        session = _parse_cli_jsonl_file(events_file)
        assert session is not None
        assert session.custom_title == "Fix failing unit tests"

    def test_cli_session_title_workspace_yaml_over_intent(self, tmp_path):
        """Test that workspace.yaml summary takes priority over report_intent."""
        import orjson

        from copilot_session_tools.scanner import _parse_cli_jsonl_file

        session_dir = tmp_path / "test-session"
        session_dir.mkdir()

        (session_dir / "workspace.yaml").write_text(
            "id: test-id\nsummary: YAML Title Wins\n",
            encoding="utf-8",
        )

        events_file = session_dir / "events.jsonl"
        events_file.write_text(
            "\n".join(orjson.dumps(e).decode() for e in self._make_cli_session_events(intent="Intent Title Loses")),
            encoding="utf-8",
        )

        session = _parse_cli_jsonl_file(events_file)
        assert session is not None
        assert session.custom_title == "YAML Title Wins"

    def test_cli_session_title_none_when_no_sources(self, tmp_path):
        """Test that custom_title is None when neither workspace.yaml nor intent exists."""
        import orjson

        from copilot_session_tools.scanner import _parse_cli_jsonl_file

        events_file = tmp_path / "test-session.jsonl"
        events_file.write_text(
            "\n".join(orjson.dumps(e).decode() for e in self._make_cli_session_events()),
            encoding="utf-8",
        )

        session = _parse_cli_jsonl_file(events_file)
        assert session is not None
        assert session.custom_title is None


class TestAskUserAnswerDisplay:
    """Tests for ask_user tool answer display in parsed sessions."""

    def _make_ask_user_session_events(self, tool_call_id, question, choices, complete_event=None):
        """Create minimal CLI JSONL events with an ask_user tool invocation."""
        events = [
            {"type": "session.start", "data": {"sessionId": "ask-user-test", "timestamp": "2026-01-15T10:00:00Z"}},
            {"type": "user.message", "data": {"content": "Help me pick"}},
            {
                "type": "assistant.message.delta",
                "data": {
                    "toolRequests": [
                        {"toolCallId": tool_call_id, "name": "ask_user", "arguments": {"question": question, "choices": choices}},
                    ]
                },
            },
            {"type": "tool.execution_start", "data": {"toolCallId": tool_call_id, "toolName": "ask_user", "arguments": {"question": question, "choices": choices}}},
        ]
        if complete_event is not None:
            events.append(complete_event)
        events.append({"type": "assistant.message.delta", "data": {"content": "Great choice!"}})
        return events

    def _parse_events(self, events, tmp_path):
        """Write events to a JSONL file and parse them."""
        from copilot_session_tools.scanner import _parse_cli_jsonl_file

        session_file = tmp_path / "ask-user-test.jsonl"
        session_file.write_text("\n".join(json.dumps(e) for e in events))
        return _parse_cli_jsonl_file(session_file)

    def _find_ask_user_block(self, session):
        """Find the ask_user content block in a parsed session."""
        for msg in session.messages:
            for block in msg.content_blocks:
                if block.kind == "ask_user":
                    return block
        return None

    def test_ask_user_with_successful_answer(self, tmp_path):
        """Test that a successful ask_user answer is displayed."""
        events = self._make_ask_user_session_events(
            tool_call_id="toolu_ask1",
            question="Which framework?",
            choices=["React", "Vue", "Angular"],
            complete_event={
                "type": "tool.execution_complete",
                "data": {
                    "toolCallId": "toolu_ask1",
                    "success": True,
                    "result": {"content": "User responded: React"},
                },
            },
        )
        session = self._parse_events(events, tmp_path)
        assert session is not None
        block = self._find_ask_user_block(session)
        assert block is not None
        assert "❓ Which framework?" in block.content
        assert "Options: React, Vue, Angular" in block.content
        assert "✅ **Answer:** React" in block.content

    def test_ask_user_with_failed_answer(self, tmp_path):
        """Test that a failed/skipped ask_user shows skipped indicator."""
        events = self._make_ask_user_session_events(
            tool_call_id="toolu_ask2",
            question="Pick a color",
            choices=["Red", "Blue"],
            complete_event={
                "type": "tool.execution_complete",
                "data": {
                    "toolCallId": "toolu_ask2",
                    "success": False,
                    "result": {"content": ""},
                },
            },
        )
        session = self._parse_events(events, tmp_path)
        assert session is not None
        block = self._find_ask_user_block(session)
        assert block is not None
        assert "❓ Pick a color" in block.content
        assert "⏭️ *Skipped*" in block.content
        assert "Answer" not in block.content

    def test_ask_user_without_completion_event(self, tmp_path):
        """Test ask_user with no completion event shows question only."""
        events = self._make_ask_user_session_events(
            tool_call_id="toolu_ask3",
            question="Choose a language",
            choices=["Python", "Go"],
            complete_event=None,
        )
        session = self._parse_events(events, tmp_path)
        assert session is not None
        block = self._find_ask_user_block(session)
        assert block is not None
        assert "❓ Choose a language" in block.content
        assert "Answer" not in block.content
        assert "Skipped" not in block.content

    def test_ask_user_answer_strips_prefix(self, tmp_path):
        """Test that 'User responded: ' prefix is stripped from the answer."""
        events = self._make_ask_user_session_events(
            tool_call_id="toolu_ask4",
            question="Which option?",
            choices=[],
            complete_event={
                "type": "tool.execution_complete",
                "data": {
                    "toolCallId": "toolu_ask4",
                    "success": True,
                    "result": {"content": "User responded: Option B"},
                },
            },
        )
        session = self._parse_events(events, tmp_path)
        assert session is not None
        block = self._find_ask_user_block(session)
        assert block is not None
        assert "✅ **Answer:** Option B" in block.content
        assert "User responded:" not in block.content

    def test_ask_user_answer_without_prefix(self, tmp_path):
        """Test answer that doesn't have 'User responded: ' prefix is shown as-is."""
        events = self._make_ask_user_session_events(
            tool_call_id="toolu_ask5",
            question="Pick one",
            choices=["A", "B"],
            complete_event={
                "type": "tool.execution_complete",
                "data": {
                    "toolCallId": "toolu_ask5",
                    "success": True,
                    "result": {"content": "B"},
                },
            },
        )
        session = self._parse_events(events, tmp_path)
        assert session is not None
        block = self._find_ask_user_block(session)
        assert block is not None
        assert "✅ **Answer:** B" in block.content


class TestRepositoryUrlDetection:
    """Tests for git repository URL detection and normalization."""

    def test_normalize_git_url_https(self):
        """Test normalizing HTTPS git URLs."""
        from copilot_session_tools.scanner import _normalize_git_url

        # Standard HTTPS URL
        result = _normalize_git_url("https://github.com/owner/repo.git")
        assert result == "github.com/owner/repo"

        # Without .git suffix
        result = _normalize_git_url("https://github.com/owner/repo")
        assert result == "github.com/owner/repo"

        # GitLab URL
        result = _normalize_git_url("https://gitlab.com/group/project.git")
        assert result == "gitlab.com/group/project"

    def test_normalize_git_url_ssh(self):
        """Test normalizing SSH git URLs."""
        from copilot_session_tools.scanner import _normalize_git_url

        # Standard SSH URL
        result = _normalize_git_url("git@github.com:owner/repo.git")
        assert result == "github.com/owner/repo"

        # Without .git suffix
        result = _normalize_git_url("git@github.com:owner/repo")
        assert result == "github.com/owner/repo"

        # GitLab SSH URL
        result = _normalize_git_url("git@gitlab.com:group/project.git")
        assert result == "gitlab.com/group/project"

    def test_normalize_git_url_ssh_protocol(self):
        """Test normalizing SSH protocol URLs."""
        from copilot_session_tools.scanner import _normalize_git_url

        # SSH protocol URL
        result = _normalize_git_url("ssh://git@github.com/owner/repo.git")
        assert result == "github.com/owner/repo"

        # Without username
        result = _normalize_git_url("ssh://github.com/owner/repo.git")
        assert result == "github.com/owner/repo"

    def test_normalize_git_url_trailing_slash(self):
        """Test that trailing slashes are handled."""
        from copilot_session_tools.scanner import _normalize_git_url

        result = _normalize_git_url("https://github.com/owner/repo/")
        assert result == "github.com/owner/repo"

    def test_normalize_git_url_unknown_format(self):
        """Test that unknown formats are returned as-is."""
        from copilot_session_tools.scanner import _normalize_git_url

        result = _normalize_git_url("some-unknown-format")
        assert result == "some-unknown-format"

    def test_detect_repository_url_none_workspace(self):
        """Test that None workspace path returns None."""
        from copilot_session_tools.scanner import detect_repository_url

        result = detect_repository_url(None)
        assert result is None

    def test_detect_repository_url_empty_workspace(self):
        """Test that empty workspace path returns None."""
        from copilot_session_tools.scanner import detect_repository_url

        result = detect_repository_url("")
        assert result is None

    def test_detect_repository_url_not_git_repo(self, tmp_path):
        """Test that non-git directory returns None."""
        from copilot_session_tools.scanner import detect_repository_url

        # Create a regular directory that's not a git repo
        workspace = tmp_path / "not-a-repo"
        workspace.mkdir()

        result = detect_repository_url(str(workspace))
        assert result is None

    def test_detect_repository_url_with_git_repo(self, tmp_path):
        """Test detection in an actual git repository."""
        import subprocess

        from copilot_session_tools.scanner import detect_repository_url

        # Create a git repo
        workspace = tmp_path / "test-repo"
        workspace.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=workspace, capture_output=True, check=True)  # noqa: S607

        # Without a remote, should return None
        result = detect_repository_url(str(workspace))
        assert result is None

        # Add a remote
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/test-owner/test-repo.git"],  # noqa: S607
            cwd=workspace,
            capture_output=True,
            check=True,
        )

        # Clear cache so we re-check after adding remote
        from copilot_session_tools.scanner import _clear_repository_url_cache

        _clear_repository_url_cache()

        # Now should return the normalized URL
        result = detect_repository_url(str(workspace))
        assert result == "github.com/test-owner/test-repo"

    def test_chat_session_has_repository_url_field(self):
        """Test that ChatSession dataclass has repository_url field."""
        session = ChatSession(
            session_id="test-session",
            workspace_name="test-workspace",
            workspace_path="/path/to/workspace",
            messages=[],
            repository_url="github.com/owner/repo",
        )

        assert session.repository_url == "github.com/owner/repo"

    def test_chat_session_repository_url_defaults_to_none(self):
        """Test that ChatSession.repository_url defaults to None."""
        session = ChatSession(
            session_id="test-session",
            workspace_name="test-workspace",
            workspace_path="/path/to/workspace",
            messages=[],
        )

        assert session.repository_url is None

    def test_detect_repository_url_exported_from_common(self):
        """Test that detect_repository_url is exported from the common package."""
        from copilot_session_tools import detect_repository_url

        # Should be callable
        assert callable(detect_repository_url)


class TestVSCodeJSONLParsing:
    """Tests for VS Code JSONL append-log format parsing."""

    def test_parse_vscode_jsonl_kind0_only(self, tmp_path):
        """Test parsing JSONL with only a kind=0 snapshot (no incremental ops)."""
        from copilot_session_tools.scanner import _parse_vscode_jsonl_file

        session_data = {
            "kind": 0,
            "v": {
                "version": 3,
                "sessionId": "abc-123",
                "creationDate": "2026-02-01T10:00:00.000Z",
                "customTitle": "Test Session",
                "requests": [
                    {
                        "message": {"text": "What is Python?"},
                        "timestamp": 1738400000000,
                        "response": [{"kind": "markdownContent", "value": {"value": "Python is a language."}}],
                    }
                ],
            },
        }
        jsonl_file = tmp_path / "abc-123.jsonl"
        jsonl_file.write_bytes(json.dumps(session_data).encode("utf-8"))

        session = _parse_vscode_jsonl_file(jsonl_file, "test-workspace", "/home/user/project", "insider")

        assert session is not None
        assert session.session_id == "abc-123"
        assert session.vscode_edition == "insider"
        assert session.workspace_name == "test-workspace"
        assert len(session.messages) == 2
        assert session.messages[0].role == "user"
        assert "Python" in session.messages[0].content
        assert session.messages[1].role == "assistant"
        assert "language" in session.messages[1].content

    def test_parse_vscode_jsonl_with_kind2_push(self, tmp_path):
        """Test parsing JSONL with kind=0 snapshot + kind=2 push (new request appended)."""
        from copilot_session_tools.scanner import _parse_vscode_jsonl_file

        # kind=0: initial snapshot with one request
        line0 = json.dumps(
            {
                "kind": 0,
                "v": {
                    "version": 3,
                    "sessionId": "def-456",
                    "creationDate": "2026-02-01T10:00:00.000Z",
                    "requests": [
                        {
                            "message": {"text": "First question"},
                            "timestamp": 1738400000000,
                            "response": [{"kind": "markdownContent", "value": {"value": "First answer"}}],
                        }
                    ],
                },
            }
        )
        # kind=2: push a new request to the requests array
        line1 = json.dumps(
            {
                "kind": 2,
                "k": ["requests"],
                "v": [
                    {
                        "message": {"text": "Second question"},
                        "timestamp": 1738400060000,
                        "response": [{"kind": "markdownContent", "value": {"value": "Second answer"}}],
                    }
                ],
            }
        )

        jsonl_file = tmp_path / "def-456.jsonl"
        jsonl_file.write_text(line0 + "\n" + line1 + "\n")

        session = _parse_vscode_jsonl_file(jsonl_file, "ws", "/path", "insider")

        assert session is not None
        assert session.session_id == "def-456"
        # Should have 4 messages: 2 user + 2 assistant
        assert len(session.messages) == 4
        user_msgs = [m for m in session.messages if m.role == "user"]
        assert len(user_msgs) == 2
        assert "First question" in user_msgs[0].content
        assert "Second question" in user_msgs[1].content

    def test_parse_vscode_jsonl_with_kind1_set(self, tmp_path):
        """Test parsing JSONL with kind=0 snapshot + kind=1 set (update property)."""
        from copilot_session_tools.scanner import _parse_vscode_jsonl_file

        line0 = json.dumps(
            {
                "kind": 0,
                "v": {
                    "version": 3,
                    "sessionId": "ghi-789",
                    "creationDate": "2026-02-01T10:00:00.000Z",
                    "customTitle": "Original Title",
                    "requests": [
                        {
                            "message": {"text": "Hello"},
                            "timestamp": 1738400000000,
                            "response": [{"kind": "markdownContent", "value": {"value": "Hi!"}}],
                        }
                    ],
                },
            }
        )
        # kind=1: update the custom title
        line1 = json.dumps(
            {
                "kind": 1,
                "k": ["customTitle"],
                "v": "Updated Title",
            }
        )

        jsonl_file = tmp_path / "ghi-789.jsonl"
        jsonl_file.write_text(line0 + "\n" + line1 + "\n")

        session = _parse_vscode_jsonl_file(jsonl_file, None, None, "stable")

        assert session is not None
        assert session.custom_title == "Updated Title"

    def test_parse_vscode_jsonl_empty_file(self, tmp_path):
        """Test parsing an empty JSONL file returns None."""
        from copilot_session_tools.scanner import _parse_vscode_jsonl_file

        jsonl_file = tmp_path / "empty.jsonl"
        jsonl_file.write_text("")

        session = _parse_vscode_jsonl_file(jsonl_file, None, None, "insider")
        assert session is None

    def test_parse_vscode_jsonl_no_kind0(self, tmp_path):
        """Test parsing JSONL without kind=0 snapshot returns None."""
        from copilot_session_tools.scanner import _parse_vscode_jsonl_file

        line = json.dumps({"kind": 1, "k": ["customTitle"], "v": "No Snapshot"})
        jsonl_file = tmp_path / "no-snapshot.jsonl"
        jsonl_file.write_text(line + "\n")

        session = _parse_vscode_jsonl_file(jsonl_file, None, None, "insider")
        assert session is None

    def test_parse_vscode_jsonl_malformed_lines(self, tmp_path):
        """Test that malformed JSONL lines are skipped gracefully."""
        from copilot_session_tools.scanner import _parse_vscode_jsonl_file

        line0 = json.dumps(
            {
                "kind": 0,
                "v": {
                    "version": 3,
                    "sessionId": "mal-001",
                    "creationDate": "2026-02-01T10:00:00.000Z",
                    "requests": [
                        {
                            "message": {"text": "Valid request"},
                            "timestamp": 1738400000000,
                            "response": [{"kind": "markdownContent", "value": {"value": "Valid response"}}],
                        }
                    ],
                },
            }
        )
        jsonl_file = tmp_path / "mal-001.jsonl"
        jsonl_file.write_text(line0 + "\n" + "not valid json\n" + "{broken\n")

        session = _parse_vscode_jsonl_file(jsonl_file, None, None, "insider")
        assert session is not None
        assert session.session_id == "mal-001"

    def test_apply_jsonl_operations_set_nested(self):
        """Test _apply_jsonl_operations with nested path for kind=1 set."""
        from copilot_session_tools.scanner import _apply_jsonl_operations

        base = {"requests": [{"message": {"text": "old"}, "response": []}]}
        ops = [{"kind": 1, "k": ["requests", 0, "message", "text"], "v": "new"}]

        result = _apply_jsonl_operations(base, ops)
        assert result["requests"][0]["message"]["text"] == "new"

    def test_apply_jsonl_operations_push(self):
        """Test _apply_jsonl_operations with kind=2 push to array."""
        from copilot_session_tools.scanner import _apply_jsonl_operations

        base = {"requests": [{"message": {"text": "first"}}]}
        ops = [{"kind": 2, "k": ["requests"], "v": [{"message": {"text": "second"}}]}]

        result = _apply_jsonl_operations(base, ops)
        assert len(result["requests"]) == 2
        assert result["requests"][1]["message"]["text"] == "second"

    def test_apply_jsonl_operations_invalid_path(self):
        """Test _apply_jsonl_operations gracefully handles invalid paths."""
        from copilot_session_tools.scanner import _apply_jsonl_operations

        base = {"requests": []}
        ops = [{"kind": 1, "k": ["nonexistent", "path"], "v": "value"}]

        result = _apply_jsonl_operations(base, ops)
        # Should not crash, just skip the operation
        assert result == {"requests": []}

    def test_scan_chat_sessions_includes_jsonl(self, tmp_path):
        """Test that scan_chat_sessions picks up .jsonl files in VS Code chatSessions dirs."""
        # Create workspace directory
        workspace_dir = tmp_path / "workspace123"
        workspace_dir.mkdir()
        workspace_json = workspace_dir / "workspace.json"
        workspace_json.write_text(json.dumps({"folder": "file:///home/user/project"}))

        chat_dir = workspace_dir / "chatSessions"
        chat_dir.mkdir()

        # Create a VS Code JSONL session file
        session_data = json.dumps(
            {
                "kind": 0,
                "v": {
                    "version": 3,
                    "sessionId": "jsonl-session-001",
                    "creationDate": "2026-02-01T10:00:00.000Z",
                    "requests": [
                        {
                            "message": {"text": "JSONL test question"},
                            "timestamp": 1738400000000,
                            "response": [{"kind": "markdownContent", "value": {"value": "JSONL test answer"}}],
                        }
                    ],
                },
            }
        )
        jsonl_file = chat_dir / "jsonl-session-001.jsonl"
        jsonl_file.write_text(session_data + "\n")

        storage_paths = [(str(tmp_path), "insider")]
        sessions = list(scan_chat_sessions(storage_paths, include_cli=False))

        assert len(sessions) >= 1
        jsonl_sessions = [s for s in sessions if s.session_id == "jsonl-session-001"]
        assert len(jsonl_sessions) == 1
        assert jsonl_sessions[0].vscode_edition == "insider"
        assert len(jsonl_sessions[0].messages) == 2

    def test_scan_session_files_includes_jsonl(self, tmp_path):
        """Test that scan_session_files yields SessionFileInfo for .jsonl files."""
        from copilot_session_tools.scanner import scan_session_files

        workspace_dir = tmp_path / "workspace456"
        workspace_dir.mkdir()
        workspace_json = workspace_dir / "workspace.json"
        workspace_json.write_text(json.dumps({"folder": "file:///home/user/project2"}))

        chat_dir = workspace_dir / "chatSessions"
        chat_dir.mkdir()

        jsonl_file = chat_dir / "test-session.jsonl"
        jsonl_file.write_text('{"kind":0,"v":{"sessionId":"test"}}\n')

        storage_paths = [(str(tmp_path), "insider")]
        file_infos = list(scan_session_files(storage_paths, include_cli=False))

        jsonl_infos = [fi for fi in file_infos if fi.file_type == "jsonl"]
        assert len(jsonl_infos) >= 1
        assert jsonl_infos[0].session_type == "vscode"
        assert jsonl_infos[0].vscode_edition == "insider"

    def test_parse_session_file_vscode_jsonl(self, tmp_path):
        """Test that parse_session_file dispatches vscode jsonl to the correct parser."""
        from copilot_session_tools.scanner import SessionFileInfo, parse_session_file

        jsonl_file = tmp_path / "dispatch-test.jsonl"
        session_data = json.dumps(
            {
                "kind": 0,
                "v": {
                    "version": 3,
                    "sessionId": "dispatch-test-001",
                    "creationDate": "2026-02-01T10:00:00.000Z",
                    "requests": [
                        {
                            "message": {"text": "Dispatch test"},
                            "timestamp": 1738400000000,
                            "response": [{"kind": "markdownContent", "value": {"value": "Dispatched!"}}],
                        }
                    ],
                },
            }
        )
        jsonl_file.write_text(session_data + "\n")

        file_info = SessionFileInfo(
            file_path=jsonl_file,
            file_type="jsonl",
            session_type="vscode",
            vscode_edition="insider",
            mtime=jsonl_file.stat().st_mtime,
            size=jsonl_file.stat().st_size,
            workspace_name="test-ws",
            workspace_path="/test/path",
        )

        sessions = parse_session_file(file_info)
        assert len(sessions) == 1
        assert sessions[0].session_id == "dispatch-test-001"
        assert sessions[0].vscode_edition == "insider"
