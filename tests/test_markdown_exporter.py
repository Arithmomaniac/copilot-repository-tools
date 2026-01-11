"""Tests for the markdown exporter module."""

import tempfile
from pathlib import Path

import pytest

from copilot_repository_tools_common import (
    session_to_markdown,
    export_session_to_file,
    generate_session_filename,
    ChatMessage,
    ChatSession,
    ToolInvocation,
    FileChange,
    CommandRun,
    ContentBlock,
)
from copilot_repository_tools_common.markdown_exporter import (
    _format_timestamp,
    _format_tool_summary,
    _had_thinking_content,
    _sanitize_filename,
)


@pytest.fixture
def sample_session():
    """Create a sample chat session for testing."""
    return ChatSession(
        session_id="test-session-123",
        workspace_name="my-project",
        workspace_path="/home/user/projects/my-project",
        messages=[
            ChatMessage(role="user", content="How do I create a Python function?"),
            ChatMessage(
                role="assistant",
                content="Here's how to create a Python function:\n\n```python\ndef my_function():\n    pass\n```",
            ),
        ],
        created_at="1704067200000",  # 2024-01-01 00:00:00
        updated_at="1704067260000",
        source_file="/path/to/session.json",
        vscode_edition="stable",
    )


@pytest.fixture
def session_with_thinking():
    """Create a session with thinking blocks."""
    return ChatSession(
        session_id="thinking-session-456",
        workspace_name="thinking-project",
        workspace_path="/home/user/thinking-project",
        messages=[
            ChatMessage(role="user", content="Think about this problem"),
            ChatMessage(
                role="assistant",
                content="Here's my answer after thinking.",
                content_blocks=[
                    ContentBlock(kind="thinking", content="Let me think about this..."),
                    ContentBlock(kind="text", content="Here's my answer after thinking."),
                ],
            ),
        ],
        created_at="1704067200000",
        vscode_edition="stable",
    )


@pytest.fixture
def session_with_tools():
    """Create a session with tool invocations."""
    return ChatSession(
        session_id="tools-session-789",
        workspace_name="tools-project",
        workspace_path="/home/user/tools-project",
        messages=[
            ChatMessage(role="user", content="Create a file"),
            ChatMessage(
                role="assistant",
                content="I've created the file.",
                tool_invocations=[
                    ToolInvocation(name="file_creator", input="test.py", result="Created"),
                    ToolInvocation(name="code_editor", input="edit test.py", result="Edited"),
                ],
                file_changes=[
                    FileChange(path="test.py", diff="+# New file"),
                ],
            ),
        ],
        created_at="1704067200000",
        vscode_edition="stable",
    )


class TestFormatTimestamp:
    """Tests for timestamp formatting."""

    def test_format_milliseconds_timestamp(self):
        """Test formatting a milliseconds timestamp."""
        result = _format_timestamp("1704067200000")
        assert "2024-01-01" in result

    def test_format_seconds_timestamp(self):
        """Test formatting a seconds timestamp."""
        result = _format_timestamp(1704067200)
        assert "2024-01-01" in result

    def test_format_none_timestamp(self):
        """Test formatting None timestamp."""
        result = _format_timestamp(None)
        assert result == "Unknown"

    def test_format_invalid_timestamp(self):
        """Test formatting invalid timestamp."""
        result = _format_timestamp("not-a-timestamp")
        assert result == "not-a-timestamp"


class TestSessionToMarkdown:
    """Tests for session_to_markdown function."""

    def test_basic_export(self, sample_session):
        """Test basic markdown export."""
        markdown = session_to_markdown(sample_session)
        
        assert "# Chat Session" in markdown
        assert "my-project" in markdown
        assert "test-session-123" in markdown
        assert "## Message 1: **USER**" in markdown
        assert "## Message 2: **ASSISTANT**" in markdown
        assert "How do I create a Python function?" in markdown
        assert "```python" in markdown

    def test_metadata_section(self, sample_session):
        """Test that metadata section is included."""
        markdown = session_to_markdown(sample_session)
        
        assert "## Metadata" in markdown
        assert "**Session ID:**" in markdown
        assert "**Workspace:**" in markdown
        assert "**Created:**" in markdown
        assert "**Edition:**" in markdown
        assert "**Messages:**" in markdown

    def test_horizontal_rules(self, sample_session):
        """Test that messages are separated by horizontal rules."""
        markdown = session_to_markdown(sample_session)
        
        # Count horizontal rules
        rule_count = markdown.count("\n---\n")
        # Should have at least 2: after metadata, after each message
        assert rule_count >= 2

    def test_thinking_blocks_omitted(self, session_with_thinking):
        """Test that thinking block content is omitted but noted."""
        markdown = session_to_markdown(session_with_thinking)
        
        # Thinking content should be omitted
        assert "Let me think about this" not in markdown
        
        # But there should be a notice
        assert "*[Was thinking...]*" in markdown
        
        # Non-thinking content should be present
        assert "Here's my answer after thinking." in markdown

    def test_thinking_blocks_included_when_requested(self, session_with_thinking):
        """Test that thinking blocks are included when include_thinking=True."""
        markdown = session_to_markdown(session_with_thinking, include_thinking=True)
        
        # Thinking content should be included
        assert "Let me think about this" in markdown
        
        # Should be in a blockquote with "Thinking:" label
        assert "> **Thinking:**" in markdown
        
        # Should NOT have the "[Was thinking...]" notice
        assert "*[Was thinking...]*" not in markdown
        
        # Non-thinking content should also be present
        assert "Here's my answer after thinking." in markdown

    def test_tool_summary_in_italics(self, session_with_tools):
        """Test that tool summaries are in italics."""
        markdown = session_to_markdown(session_with_tools)
        
        # Tool summary should be in italics
        assert "*Used" in markdown
        assert "file_creator" in markdown

    def test_file_changes_summary(self, session_with_tools):
        """Test that file changes are summarized."""
        markdown = session_to_markdown(session_with_tools)
        
        # File changes should be summarized in italics
        assert "*Changed file:" in markdown
        assert "test.py" in markdown

    def test_custom_title_shown(self):
        """Test that custom title is shown when available."""
        session = ChatSession(
            session_id="custom-title-session",
            workspace_name="workspace",
            workspace_path="/path",
            messages=[ChatMessage(role="user", content="Hello")],
            custom_title="My Custom Title",
        )
        markdown = session_to_markdown(session)
        
        assert "**Title:** My Custom Title" in markdown


class TestExportSessionToFile:
    """Tests for export_session_to_file function."""

    def test_export_to_file(self, sample_session, tmp_path):
        """Test exporting to a file."""
        output_path = tmp_path / "test_export.md"
        export_session_to_file(sample_session, output_path)
        
        assert output_path.exists()
        content = output_path.read_text()
        assert "# Chat Session" in content
        assert "my-project" in content


class TestGenerateSessionFilename:
    """Tests for generate_session_filename function."""

    def test_filename_with_custom_title(self):
        """Test filename generation with custom title."""
        session = ChatSession(
            session_id="test-123",
            workspace_name="workspace",
            workspace_path="/path",
            messages=[],
            custom_title="My Custom Session",
            created_at="1704067200000",
        )
        filename = generate_session_filename(session)
        
        assert filename.endswith(".md")
        assert "My_Custom_Session" in filename
        assert "test-123" in filename

    def test_filename_with_workspace(self):
        """Test filename generation with workspace name."""
        session = ChatSession(
            session_id="test-456",
            workspace_name="my-project",
            workspace_path="/path",
            messages=[],
            created_at="1704067200000",
        )
        filename = generate_session_filename(session)
        
        assert filename.endswith(".md")
        assert "my-project" in filename

    def test_filename_with_date(self):
        """Test filename includes date when available."""
        session = ChatSession(
            session_id="test-789",
            workspace_name="project",
            workspace_path="/path",
            messages=[],
            created_at="1704067200000",  # 2024-01-01
        )
        filename = generate_session_filename(session)
        
        assert "20240101" in filename

    def test_filename_sanitization(self):
        """Test that unsafe characters are removed from filename."""
        session = ChatSession(
            session_id="test-bad-chars",
            workspace_name="path/to/project:name",
            workspace_path="/path",
            messages=[],
        )
        filename = generate_session_filename(session)
        
        assert "/" not in filename
        assert ":" not in filename
        assert filename.endswith(".md")


class TestHadThinkingContent:
    """Tests for _had_thinking_content helper."""

    def test_no_content_blocks(self):
        """Test message with no content blocks."""
        message = ChatMessage(role="assistant", content="Hello")
        assert _had_thinking_content(message) is False

    def test_no_thinking_blocks(self):
        """Test message with only text blocks."""
        message = ChatMessage(
            role="assistant",
            content="Hello",
            content_blocks=[ContentBlock(kind="text", content="Hello")],
        )
        assert _had_thinking_content(message) is False

    def test_has_thinking_blocks(self):
        """Test message with thinking blocks."""
        message = ChatMessage(
            role="assistant",
            content="Hello",
            content_blocks=[
                ContentBlock(kind="thinking", content="Thinking..."),
                ContentBlock(kind="text", content="Hello"),
            ],
        )
        assert _had_thinking_content(message) is True


class TestFormatToolSummary:
    """Tests for _format_tool_summary helper."""

    def test_no_tools(self):
        """Test message with no tools."""
        message = ChatMessage(role="assistant", content="Hello")
        assert _format_tool_summary(message) == ""

    def test_single_tool(self):
        """Test message with single tool."""
        message = ChatMessage(
            role="assistant",
            content="Hello",
            tool_invocations=[ToolInvocation(name="my_tool")],
        )
        result = _format_tool_summary(message)
        assert "*Used tool: my_tool*" in result

    def test_multiple_tools(self):
        """Test message with multiple tools."""
        message = ChatMessage(
            role="assistant",
            content="Hello",
            tool_invocations=[
                ToolInvocation(name="tool1"),
                ToolInvocation(name="tool2"),
            ],
        )
        result = _format_tool_summary(message)
        assert "*Used tools:" in result
        assert "tool1" in result
        assert "tool2" in result

    def test_tool_with_input_included(self):
        """Test that tool inputs are included when flag is set."""
        message = ChatMessage(
            role="assistant",
            content="Hello",
            tool_invocations=[
                ToolInvocation(name="run_in_terminal", input="npm run test"),
            ],
        )
        result = _format_tool_summary(message, include_inputs=True)
        assert "*Used tool: run_in_terminal*" in result
        assert "**run_in_terminal input:**" in result
        assert "```" in result
        assert "npm run test" in result

    def test_tool_without_input_when_not_included(self):
        """Test that tool inputs are not included when flag is False."""
        message = ChatMessage(
            role="assistant",
            content="Hello",
            tool_invocations=[
                ToolInvocation(name="run_in_terminal", input="npm run test"),
            ],
        )
        result = _format_tool_summary(message, include_inputs=False)
        assert "*Used tool: run_in_terminal*" in result
        assert "npm run test" not in result
        assert "```" not in result


class TestFormatFileChangesSummary:
    """Tests for _format_file_changes_summary helper with diffs."""

    def test_no_file_changes(self):
        """Test message with no file changes."""
        from copilot_repository_tools_common.markdown_exporter import _format_file_changes_summary
        message = ChatMessage(role="assistant", content="Hello")
        assert _format_file_changes_summary(message) == ""

    def test_file_changes_with_diff_included(self):
        """Test that file diffs are included when flag is set."""
        from copilot_repository_tools_common.markdown_exporter import _format_file_changes_summary
        message = ChatMessage(
            role="assistant",
            content="Hello",
            file_changes=[
                FileChange(path="test.py", diff="+ def test():\n+     pass"),
            ],
        )
        result = _format_file_changes_summary(message, include_diffs=True)
        assert "*Changed file: test.py*" in result
        assert "**test.py:**" in result
        assert "```diff" in result
        assert "+ def test():" in result

    def test_file_changes_without_diff_when_not_included(self):
        """Test that file diffs are not included when flag is False."""
        from copilot_repository_tools_common.markdown_exporter import _format_file_changes_summary
        message = ChatMessage(
            role="assistant",
            content="Hello",
            file_changes=[
                FileChange(path="test.py", diff="+ def test():\n+     pass"),
            ],
        )
        result = _format_file_changes_summary(message, include_diffs=False)
        assert "*Changed file: test.py*" in result
        assert "```diff" not in result
        assert "def test():" not in result


class TestSessionToMarkdownWithOptions:
    """Tests for session_to_markdown with include_diffs and include_tool_inputs options."""

    def test_with_tool_inputs(self):
        """Test markdown export with tool inputs included."""
        session = ChatSession(
            session_id="test-session",
            workspace_name="test",
            workspace_path="/test",
            messages=[
                ChatMessage(role="user", content="Run tests"),
                ChatMessage(
                    role="assistant",
                    content="Running tests...",
                    tool_invocations=[
                        ToolInvocation(name="run_in_terminal", input="pytest"),
                    ],
                ),
            ],
        )
        markdown = session_to_markdown(session, include_tool_inputs=True)
        assert "pytest" in markdown
        assert "```" in markdown

    def test_with_file_diffs(self):
        """Test markdown export with file diffs included."""
        session = ChatSession(
            session_id="test-session",
            workspace_name="test",
            workspace_path="/test",
            messages=[
                ChatMessage(role="user", content="Create a file"),
                ChatMessage(
                    role="assistant",
                    content="Creating file...",
                    file_changes=[
                        FileChange(path="new_file.py", diff="+ # New file\n+ print('hello')"),
                    ],
                ),
            ],
        )
        markdown = session_to_markdown(session, include_diffs=True)
        assert "```diff" in markdown
        assert "print('hello')" in markdown

    def test_without_options_no_details(self):
        """Test markdown export without options doesn't include details."""
        session = ChatSession(
            session_id="test-session",
            workspace_name="test",
            workspace_path="/test",
            messages=[
                ChatMessage(role="user", content="Do something"),
                ChatMessage(
                    role="assistant",
                    content="Done!",
                    tool_invocations=[
                        ToolInvocation(name="run_in_terminal", input="ls -la"),
                    ],
                    file_changes=[
                        FileChange(path="file.py", diff="+ new content"),
                    ],
                ),
            ],
        )
        markdown = session_to_markdown(session, include_diffs=False, include_tool_inputs=False)
        assert "ls -la" not in markdown
        assert "new content" not in markdown
        # But summaries should still be there
        assert "*Used tool:" in markdown
        assert "*Changed file:" in markdown


class TestSanitizeFilename:
    """Tests for _sanitize_filename helper."""

    def test_safe_characters_unchanged(self):
        """Test that safe characters are unchanged."""
        result = _sanitize_filename("my-project_v1.0")
        assert result == "my-project_v1.0"

    def test_unsafe_characters_replaced(self):
        """Test that unsafe characters are replaced with underscores."""
        result = _sanitize_filename("path/to:project name")
        assert "/" not in result
        assert ":" not in result
        assert " " not in result
        assert "_" in result

    def test_max_length_enforced(self):
        """Test that max length is enforced."""
        long_name = "a" * 100
        result = _sanitize_filename(long_name, max_length=50)
        assert len(result) == 50

    def test_custom_max_length(self):
        """Test custom max length."""
        result = _sanitize_filename("test-project", max_length=5)
        assert len(result) == 5
        assert result == "test-"

    def test_empty_string(self):
        """Test empty string input."""
        result = _sanitize_filename("")
        assert result == ""

    def test_all_unsafe_characters(self):
        """Test string with all unsafe characters."""
        result = _sanitize_filename("!@#$%^&*()")
        # All characters should be replaced with underscores
        assert all(c == "_" for c in result)
