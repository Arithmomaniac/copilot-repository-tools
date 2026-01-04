"""Tests for the markdown exporter module."""

import tempfile
from pathlib import Path

import pytest

from copilot_chat_archive.markdown_exporter import (
    session_to_markdown,
    export_session_to_file,
    generate_session_filename,
    _format_timestamp,
    _format_tool_summary,
    _had_thinking_content,
)
from copilot_chat_archive.scanner import (
    ChatMessage,
    ChatSession,
    ToolInvocation,
    FileChange,
    CommandRun,
    ContentBlock,
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
