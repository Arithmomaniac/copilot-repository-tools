"""Tests for the viewer module."""

import tempfile
from pathlib import Path

import pytest

from copilot_chat_archive.database import Database
from copilot_chat_archive.scanner import ChatMessage, ChatSession
from copilot_chat_archive.viewer import generate_html, _markdown_to_html


@pytest.fixture
def temp_db():
    """Create a temporary database with sample data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = Database(db_path)

    # Add sample session
    session = ChatSession(
        session_id="viewer-test-session",
        workspace_name="test-workspace",
        workspace_path="/home/user/test",
        messages=[
            ChatMessage(role="user", content="What is Python?"),
            ChatMessage(
                role="assistant",
                content="Python is a programming language.\n\n```python\nprint('Hello')\n```",
            ),
        ],
        created_at="2025-01-15T10:00:00Z",
        vscode_edition="stable",
    )
    db.add_session(session)

    yield db

    Path(db_path).unlink(missing_ok=True)


class TestMarkdownToHtml:
    """Tests for the markdown to HTML converter."""

    def test_plain_text(self):
        """Test converting plain text."""
        result = _markdown_to_html("Hello, world!")
        assert "Hello, world!" in result

    def test_inline_code(self):
        """Test converting inline code."""
        result = _markdown_to_html("Use `print()` function")
        assert "<code>print()</code>" in result

    def test_code_block(self):
        """Test converting code blocks."""
        text = "```python\nprint('hello')\n```"
        result = _markdown_to_html(text)
        assert "<pre>" in result
        assert "<code" in result
        assert "print(&#x27;hello&#x27;)" in result

    def test_escapes_html(self):
        """Test that HTML is escaped."""
        result = _markdown_to_html("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


class TestGenerateHtml:
    """Tests for the generate_html function."""

    def test_generate_html(self, temp_db, tmp_path):
        """Test generating HTML files."""
        output_dir = tmp_path / "output"
        index_path = generate_html(temp_db, output_dir)

        # Check index.html was created
        assert index_path.exists()
        assert index_path.name == "index.html"

        # Check sessions directory was created
        sessions_dir = output_dir / "sessions"
        assert sessions_dir.exists()

        # Check session file was created
        session_files = list(sessions_dir.glob("*.html"))
        assert len(session_files) == 1

        # Check static directory was created
        static_dir = output_dir / "static"
        assert static_dir.exists()
        assert (static_dir / "style.css").exists()
        assert (static_dir / "script.js").exists()

    def test_generate_html_content(self, temp_db, tmp_path):
        """Test that generated HTML contains expected content."""
        output_dir = tmp_path / "output"
        index_path = generate_html(temp_db, output_dir, title="My Archive")

        index_content = index_path.read_text()
        assert "My Archive" in index_content
        assert "test-workspace" in index_content
        assert "1 sessions" in index_content or "1 session" in index_content

    def test_generate_html_empty_db(self, tmp_path):
        """Test generating HTML with empty database."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            db = Database(db_path)
            output_dir = tmp_path / "output"
            index_path = generate_html(db, output_dir)

            assert index_path.exists()
            content = index_path.read_text()
            assert "No sessions found" in content
        finally:
            Path(db_path).unlink(missing_ok=True)
