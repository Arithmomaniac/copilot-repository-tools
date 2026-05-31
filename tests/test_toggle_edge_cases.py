"""Tests for toggle combination edge cases from the toggle specification."""

import pathlib

from copilot_session_tools.html_exporter import session_to_html
from copilot_session_tools.markdown_exporter import session_to_markdown
from copilot_session_tools.scanner.models import (
    ChatMessage,
    ChatSession,
    CommandRun,
    ContentBlock,
    FileChange,
    ToolInvocation,
)


def _make_rich_message(*, inline_tools: bool = True):
    """Create a message with all content types for toggle testing.

    Args:
        inline_tools: If True, include a toolInvocation content block (VSCode-style
            inline rendering). When inline tools are present, the markdown exporter
            skips separate tool/command summaries to avoid duplication.
    """
    content_blocks = [
        ContentBlock(kind="thinking", content="The user needs help with auth"),
        ContentBlock(kind="text", content="Let me fix that."),
    ]
    if inline_tools:
        content_blocks.append(ContentBlock(kind="toolInvocation", content="Viewing main.py"))
    return ChatMessage(
        role="assistant",
        content="Let me fix that.",
        timestamp="1700000000",
        content_blocks=content_blocks,
        tool_invocations=[
            ToolInvocation(
                name="view",
                input='{"path": "main.py"}',
                result="file contents here",
                status="success",
                invocation_message="Viewing main.py",
            ),
        ],
        file_changes=[
            FileChange(path="main.py", language_id="python", diff="- old\n+ new"),
        ],
        command_runs=[
            CommandRun(command="pytest tests/", title="pytest tests/", output="PASSED"),
        ],
    )


def _make_session(messages=None):
    """Wrap messages in a ChatSession."""
    if messages is None:
        messages = [_make_rich_message()]
    return ChatSession(
        session_id="test-toggles",
        workspace_name="test",
        workspace_path="/test",
        messages=messages,
        created_at="1700000000",
        updated_at="1700000000",
        source_file=None,
        vscode_edition="cli",
        custom_title="Toggle Test",
        type="cli",
        parser_version=1,
        source_format=None,
    )


class TestMarkdownToggleCombinations:
    """Test markdown export with various content_set combinations."""

    def test_conversation_only_mode(self):
        """T1=OFF, T5=OFF, T6=OFF, T7=OFF: only prose text remains."""
        session = _make_session()
        md = session_to_markdown(session, content_set={"agent-details"})
        assert "Let me fix that" in md
        assert "🔧" not in md  # no tool lines
        assert "⚡" not in md  # no command lines
        assert "📄" not in md  # no file lines
        assert "thinking" not in md.lower()  # no thinking

    def test_everything_on_inline_tools(self):
        """All content types included with inline tool blocks (VSCode-style).

        When inline tool content blocks are present, the exporter renders tools
        inline and skips the separate ⚡/🔧 summary lines to avoid duplication.
        """
        all_types = {
            "thinking",
            "diffs",
            "tool-inputs",
            "agent-details",
            "tools",
            "commands",
            "file-changes",
        }
        session = _make_session()
        md = session_to_markdown(session, content_set=all_types)
        assert "💭" in md or "Thinking" in md
        assert "Viewing main.py" in md  # inline tool text
        assert "📄" in md

    def test_everything_on_summary_tools(self):
        """All content types included without inline blocks (CLI-style).

        Without inline tool content blocks, the exporter renders separate
        summary lines with ⚡ and 🔧 emojis.
        """
        all_types = {
            "thinking",
            "diffs",
            "tool-inputs",
            "agent-details",
            "tools",
            "commands",
            "file-changes",
        }
        session = _make_session([_make_rich_message(inline_tools=False)])
        md = session_to_markdown(session, content_set=all_types)
        assert "💭" in md or "Thinking" in md
        assert "🔧" in md
        assert "⚡" in md
        assert "📄" in md

    def test_tools_excluded_removes_inline_and_summary(self):
        """T5=OFF: no tool invocation text of any kind."""
        session = _make_session()
        md = session_to_markdown(session, content_set={"agent-details", "commands", "file-changes"})
        assert "🔧" not in md

    def test_thinking_excluded_no_trace(self):
        """T1=OFF: thinking completely omitted, WYSIWYG."""
        session = _make_session()
        md = session_to_markdown(session, content_set={"agent-details", "tools", "commands", "file-changes"})
        assert "thinking" not in md.lower()
        assert "Was thinking" not in md
        assert "💭" not in md

    def test_diffs_excluded_files_remain(self):
        """T2=OFF, T7=ON: file names shown, diffs hidden."""
        session = _make_session()
        md = session_to_markdown(session, content_set={"agent-details", "tools", "commands", "file-changes"})
        assert "📄" in md or "main.py" in md
        assert "- old" not in md  # diff content hidden

    def test_diffs_included(self):
        """T2=ON: diff content shown."""
        session = _make_session()
        md = session_to_markdown(
            session,
            content_set={
                "agent-details",
                "tools",
                "commands",
                "file-changes",
                "diffs",
            },
        )
        assert "- old" in md or "+ new" in md


class TestHtmlToggleCombinations:
    """Test HTML export with various content_set combinations."""

    def test_html_thinking_hidden_by_css(self):
        """Thinking blocks absent from HTML when not in content_set.

        The HTML exporter strips thinking blocks server-side when
        include_thinking is false; CSS hide classes on the body provide
        an additional layer for any residual elements.
        """
        session = _make_session()
        html = session_to_html(session, content_set={"agent-details", "tools"})
        # Thinking content block elements are stripped server-side
        assert '<details class="thinking-block" open>' not in html
        # CSS hide class is still applied to body for belt-and-suspenders hiding
        assert "hide-thinking" in html

    def test_html_includes_everything(self):
        """All content present when fully included."""
        all_types = {
            "thinking",
            "diffs",
            "tool-inputs",
            "agent-details",
            "tools",
            "commands",
            "file-changes",
        }
        session = _make_session()
        html = session_to_html(session, content_set=all_types)
        assert "thinking-block" in html or "thinking" in html.lower()

    def test_html_static_no_alpine(self):
        """Static HTML has no Alpine.js interactive controls."""
        session = _make_session()
        html = session_to_html(session)  # static=True is default for exporter
        assert "alpinejs" not in html
        assert "x-data" not in html
        # The settings button element is excluded via {% if not static %},
        # but the CSS class name may still appear in style rules.
        assert '<button class="settings-btn"' not in html


class TestCssHideRulesExist:
    """Verify the template contains CSS rules needed for toggle functionality."""

    def test_hide_rules_in_template(self):
        """All 7 CSS hide rules present in session.html."""
        template = pathlib.Path("src/copilot_session_tools/web/templates/session.html").read_text(encoding="utf-8")
        assert ".hide-thinking" in template
        assert ".hide-diffs" in template
        assert ".hide-tool-inputs" in template
        assert ".hide-tools" in template
        assert ".hide-commands" in template
        assert ".hide-file-changes" in template
        assert ".hide-agent-details" in template

    def test_split_css_classes_exist(self):
        """tool-section-input and tool-summary sub-classes exist."""
        template = pathlib.Path("src/copilot_session_tools/web/templates/session.html").read_text(encoding="utf-8")
        assert "tool-section-input" in template
        # tool-section (base class) used for both input and output sections
        assert "tool-section" in template
        assert "tool-summary-tools" in template
        assert "tool-summary-files" in template
        assert "tool-summary-commands" in template
