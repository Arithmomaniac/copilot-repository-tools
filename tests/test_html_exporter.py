"""Tests for the HTML exporter module."""

import pytest

from copilot_session_tools import (
    ChatMessage,
    ChatSession,
    ContentBlock,
    ToolInvocation,
    export_session_to_html_file,
    generate_session_html_filename,
    session_to_html,
)


@pytest.fixture
def sample_session():
    """Create a sample chat session for testing."""
    return ChatSession(
        session_id="html-test-session-123",
        workspace_name="my-project",
        workspace_path="/home/user/projects/my-project",
        messages=[
            ChatMessage(role="user", content="Hello, can you help me?"),
            ChatMessage(role="assistant", content="Sure, I can help!"),
        ],
        created_at="1700000000000",
        updated_at="1700001000000",
        vscode_edition="stable",
    )


@pytest.fixture
def session_with_tools():
    """Create a session with tool invocations and content blocks."""
    return ChatSession(
        session_id="tools-test-session",
        workspace_name="tools-project",
        workspace_path="/home/user/tools-project",
        messages=[
            ChatMessage(
                role="user",
                content="Fix this bug",
            ),
            ChatMessage(
                role="assistant",
                content="Let me look at the code.",
                tool_invocations=[
                    ToolInvocation(
                        name="copilot_readFile",
                        input='{"path": "src/main.py"}',
                        result="def main(): pass",
                        invocation_message="Reading src/main.py",
                    ),
                ],
                content_blocks=[
                    ContentBlock(kind="text", content="Let me look at the code."),
                    ContentBlock(kind="toolInvocation", content="Reading `copilot_readFile`"),
                ],
            ),
        ],
        vscode_edition="stable",
    )


class TestSessionToHtml:
    """Tests for session_to_html function."""

    def test_returns_complete_html_document(self, sample_session):
        """Test that output is a complete HTML document."""
        html = session_to_html(sample_session)
        assert "<!DOCTYPE html>" in html
        assert "<html lang=" in html
        assert "</html>" in html
        assert "<head>" in html
        assert "<body>" in html

    def test_contains_session_metadata(self, sample_session):
        """Test that session metadata is included."""
        html = session_to_html(sample_session)
        assert "my-project" in html
        assert "html-test-session-123" in html

    def test_contains_message_content(self, sample_session):
        """Test that message content appears in the output."""
        html = session_to_html(sample_session)
        assert "Hello, can you help me?" in html
        assert "Sure, I can help!" in html

    def test_static_mode_no_toolbar(self, sample_session):
        """Test that static HTML has no copy toolbar HTML element."""
        html = session_to_html(sample_session)
        assert '<div class="copy-markdown-toolbar">' not in html

    def test_static_mode_no_copy_buttons(self, sample_session):
        """Test that static HTML has no per-message copy buttons."""
        html = session_to_html(sample_session)
        assert 'class="message-copy-btn"' not in html

    def test_static_mode_no_font_awesome(self, sample_session):
        """Test that static HTML has no Font Awesome CDN link."""
        html = session_to_html(sample_session)
        assert "cdnjs.cloudflare.com" not in html

    def test_static_mode_no_ajax(self, sample_session):
        """Test that static HTML has no AJAX/fetch code."""
        html = session_to_html(sample_session)
        assert "buildMarkdownParams" not in html
        assert "api/markdown" not in html

    def test_static_mode_no_back_link(self, sample_session):
        """Test that static HTML has no back link."""
        html = session_to_html(sample_session)
        assert "Back to all sessions" not in html

    def test_static_mode_no_header(self, sample_session):
        """Test that static HTML has no sticky header."""
        html = session_to_html(sample_session)
        # The <header> element should not be present
        assert "<header>" not in html

    def test_static_mode_full_width(self, sample_session):
        """Test that static HTML has full-width container."""
        html = session_to_html(sample_session)
        assert "--container-max-width: none" in html

    def test_static_mode_larger_scroll_heights(self, sample_session):
        """Test that static HTML has unlimited pre max-heights."""
        html = session_to_html(sample_session)
        assert "--pre-max-height: none" in html
        assert "--cmd-max-height: none" in html

    def test_contains_shared_js(self, sample_session):
        """Test that shared JS (toggle, code-collapse) is still present."""
        html = session_to_html(sample_session)
        assert "collapsible-icon" in html
        assert "code-collapse-wrapper" in html

    def test_contains_dark_mode_css(self, sample_session):
        """Test that dark mode CSS variables are present."""
        html = session_to_html(sample_session)
        assert "prefers-color-scheme: dark" in html

    def test_with_tool_invocations(self, session_with_tools):
        """Test that tool invocations render correctly."""
        html = session_to_html(session_with_tools)
        assert "Let me look at the code" in html

    def test_message_anchors_present(self, sample_session):
        """Test that message anchors are present for navigation."""
        html = session_to_html(sample_session)
        assert 'id="msg-1"' in html
        assert 'id="msg-2"' in html


class TestExportSessionToHtmlFile:
    """Tests for export_session_to_html_file function."""

    def test_creates_html_file(self, sample_session, tmp_path):
        """Test that an HTML file is created."""
        output_path = tmp_path / "test_session.html"
        export_session_to_html_file(sample_session, output_path)
        assert output_path.exists()

    def test_file_contains_valid_html(self, sample_session, tmp_path):
        """Test that the created file contains valid HTML."""
        output_path = tmp_path / "test_session.html"
        export_session_to_html_file(sample_session, output_path)
        content = output_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "</html>" in content

    def test_file_is_utf8(self, tmp_path):
        """Test that the file is UTF-8 encoded with special characters."""
        session = ChatSession(
            session_id="utf8-test",
            workspace_name="utf8-project",
            workspace_path="/tmp/test",
            messages=[
                ChatMessage(role="user", content="Héllo wörld 日本語"),
            ],
            vscode_edition="stable",
        )
        output_path = tmp_path / "utf8_test.html"
        export_session_to_html_file(session, output_path)
        content = output_path.read_text(encoding="utf-8")
        assert "Héllo wörld 日本語" in content


class TestGenerateSessionHtmlFilename:
    """Tests for generate_session_html_filename function."""

    def test_html_extension(self, sample_session):
        """Test that filename has .html extension."""
        filename = generate_session_html_filename(sample_session)
        assert filename.endswith(".html")

    def test_contains_workspace_name(self, sample_session):
        """Test that filename contains workspace name."""
        filename = generate_session_html_filename(sample_session)
        assert "my-project" in filename or "my_project" in filename

    def test_mirrors_markdown_filename_structure(self, sample_session):
        """Test that HTML filename mirrors markdown filename structure."""
        from copilot_session_tools import generate_session_filename

        md_filename = generate_session_filename(sample_session)
        html_filename = generate_session_html_filename(sample_session)

        # Should be the same except for extension
        assert md_filename.replace(".md", ".html") == html_filename
