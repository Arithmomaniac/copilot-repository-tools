"""Tests for the segments module."""

import tempfile
from pathlib import Path

import pytest

from copilot_chat_archive.segments import (
    ChatSegment,
    generate_segments,
    _is_compaction_boundary,
    _render_message_to_markdown,
)
from copilot_chat_archive.scanner import ChatMessage, ChatSession, ContentBlock
from copilot_chat_archive.database import Database


@pytest.fixture
def sample_session():
    """Create a sample chat session for testing."""
    return ChatSession(
        session_id="test-session-segments",
        workspace_name="my-project",
        workspace_path="/home/user/projects/my-project",
        messages=[
            ChatMessage(role="user", content="How do I create a Python function?"),
            ChatMessage(
                role="assistant",
                content="Here's how to create a Python function:\n\n```python\ndef my_function():\n    pass\n```",
            ),
            ChatMessage(role="user", content="Thanks! Can you add parameters?"),
            ChatMessage(
                role="assistant",
                content="Sure! Here's a function with parameters:\n\n```python\ndef my_function(name, age=18):\n    return f'{name} is {age} years old'\n```",
            ),
        ],
        created_at="2025-01-15T10:30:00Z",
        vscode_edition="stable",
    )


@pytest.fixture
def session_with_compaction():
    """Create a session with a compaction boundary."""
    return ChatSession(
        session_id="test-session-compaction",
        workspace_name="compaction-project",
        workspace_path="/home/user/projects/compaction",
        messages=[
            ChatMessage(role="user", content="First question about Python"),
            ChatMessage(role="assistant", content="Here's the answer to your first question."),
            ChatMessage(role="user", content="Second question"),
            ChatMessage(role="assistant", content="Here's the second answer."),
            # Compaction boundary - summary prompt
            ChatMessage(
                role="user",
                content="Based on the previous conversation, I want to continue working on the Python project.",
            ),
            ChatMessage(role="assistant", content="Sure, let's continue from where we left off."),
        ],
        created_at="2025-01-15T11:00:00Z",
        vscode_edition="stable",
    )


@pytest.fixture
def session_with_thinking():
    """Create a session with thinking blocks."""
    return ChatSession(
        session_id="test-session-thinking",
        workspace_name="thinking-project",
        workspace_path="/home/user/projects/thinking",
        messages=[
            ChatMessage(role="user", content="Explain quantum computing"),
            ChatMessage(
                role="assistant",
                content="Quantum computing uses qubits...",
                content_blocks=[
                    ContentBlock(kind="thinking", content="Let me think about how to explain this..."),
                    ContentBlock(kind="text", content="Quantum computing uses qubits to perform calculations."),
                ],
            ),
        ],
        created_at="2025-01-15T12:00:00Z",
        vscode_edition="stable",
    )


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = Database(db_path)
    yield db
    Path(db_path).unlink(missing_ok=True)


class TestChatSegment:
    """Tests for the ChatSegment dataclass."""

    def test_create_segment(self):
        """Test creating a ChatSegment."""
        segment = ChatSegment(
            session_id="test-123",
            segment_index=0,
            first_message_content="Hello",
            first_message_index=0,
            last_message_content="Goodbye",
            last_message_index=3,
            markdown_content="# Chat\n\nHello...",
            message_count=4,
        )
        assert segment.session_id == "test-123"
        assert segment.segment_index == 0
        assert segment.message_count == 4


class TestCompactionBoundary:
    """Tests for compaction boundary detection."""

    def test_not_compaction_for_assistant(self):
        """Test that assistant messages are never compaction boundaries."""
        msg = ChatMessage(role="assistant", content="Based on the previous conversation...")
        assert _is_compaction_boundary(msg, None) is False

    def test_compaction_with_summary_marker(self):
        """Test detection of summary markers."""
        msg = ChatMessage(
            role="user",
            content="Based on the previous conversation, let's continue.",
        )
        assert _is_compaction_boundary(msg, None) is True

    def test_compaction_with_context_marker(self):
        """Test detection of context markers."""
        msg = ChatMessage(role="user", content="[Context] Here's what we discussed...")
        assert _is_compaction_boundary(msg, None) is True

    def test_no_compaction_for_normal_message(self):
        """Test that normal user messages are not compaction boundaries."""
        msg = ChatMessage(role="user", content="How do I write a function?")
        assert _is_compaction_boundary(msg, None) is False


class TestRenderMessage:
    """Tests for message rendering."""

    def test_render_user_message(self):
        """Test rendering a user message."""
        msg = ChatMessage(role="user", content="Hello world")
        result = _render_message_to_markdown(msg)
        assert "**User:**" in result
        assert "Hello world" in result

    def test_render_assistant_message(self):
        """Test rendering an assistant message."""
        msg = ChatMessage(role="assistant", content="Hi there!")
        result = _render_message_to_markdown(msg)
        assert "**Assistant:**" in result
        assert "Hi there!" in result

    def test_render_without_role(self):
        """Test rendering without role header."""
        msg = ChatMessage(role="user", content="Hello")
        result = _render_message_to_markdown(msg, include_role=False)
        assert "**User:**" not in result
        assert "Hello" in result

    def test_render_filters_thinking_blocks(self):
        """Test that thinking blocks are filtered out."""
        msg = ChatMessage(
            role="assistant",
            content="",
            content_blocks=[
                ContentBlock(kind="thinking", content="Let me think..."),
                ContentBlock(kind="text", content="Here's my answer."),
            ],
        )
        result = _render_message_to_markdown(msg)
        assert "Let me think..." not in result
        assert "Here's my answer." in result


class TestGenerateSegments:
    """Tests for segment generation."""

    def test_generate_single_segment(self, sample_session):
        """Test generating a single segment from a normal session."""
        segments = list(generate_segments(sample_session))
        
        assert len(segments) == 1
        assert segments[0].session_id == sample_session.session_id
        assert segments[0].segment_index == 0
        assert segments[0].message_count == 4
        assert "Python function" in segments[0].first_message_content

    def test_generate_multiple_segments_with_compaction(self, session_with_compaction):
        """Test generating multiple segments when compaction occurs."""
        segments = list(generate_segments(session_with_compaction))
        
        assert len(segments) == 2
        
        # First segment
        assert segments[0].segment_index == 0
        assert segments[0].first_message_index == 0
        assert segments[0].last_message_index == 3
        assert segments[0].message_count == 4
        
        # Second segment (after compaction)
        assert segments[1].segment_index == 1
        assert segments[1].first_message_index == 4
        assert segments[1].last_message_index == 5
        assert segments[1].message_count == 2

    def test_generate_segments_excludes_thinking(self, session_with_thinking):
        """Test that generated segments exclude thinking blocks."""
        segments = list(generate_segments(session_with_thinking))
        
        assert len(segments) == 1
        # The markdown content should not contain thinking blocks
        assert "Let me think" not in segments[0].markdown_content
        assert "qubits" in segments[0].markdown_content

    def test_empty_session_yields_nothing(self):
        """Test that an empty session yields no segments."""
        empty_session = ChatSession(
            session_id="empty",
            workspace_name=None,
            workspace_path=None,
            messages=[],
        )
        segments = list(generate_segments(empty_session))
        assert len(segments) == 0


class TestDatabaseSegments:
    """Tests for database segment operations."""

    def test_add_segment(self, temp_db, sample_session):
        """Test adding a segment to the database."""
        temp_db.add_session(sample_session)
        
        segments = list(generate_segments(sample_session))
        assert len(segments) == 1
        
        result = temp_db.add_segment(segments[0])
        assert result is True
        
        # Retrieve and verify
        stored = temp_db.get_session_segments(sample_session.session_id)
        assert len(stored) == 1
        assert stored[0]["session_id"] == sample_session.session_id

    def test_add_duplicate_segment(self, temp_db, sample_session):
        """Test that adding a duplicate segment returns False."""
        temp_db.add_session(sample_session)
        segments = list(generate_segments(sample_session))
        
        temp_db.add_segment(segments[0])
        result = temp_db.add_segment(segments[0])
        assert result is False

    def test_delete_session_segments(self, temp_db, sample_session):
        """Test deleting segments for a session."""
        temp_db.add_session(sample_session)
        segments = list(generate_segments(sample_session))
        temp_db.add_segment(segments[0])
        
        # Verify segment exists
        stored = temp_db.get_session_segments(sample_session.session_id)
        assert len(stored) == 1
        
        # Delete segments
        temp_db.delete_session_segments(sample_session.session_id)
        
        # Verify deleted
        stored = temp_db.get_session_segments(sample_session.session_id)
        assert len(stored) == 0

    def test_segment_stats(self, temp_db, sample_session):
        """Test getting segment statistics."""
        temp_db.add_session(sample_session)
        segments = list(generate_segments(sample_session))
        temp_db.add_segment(segments[0])
        
        stats = temp_db.get_segment_stats()
        assert stats["segment_count"] == 1
        assert stats["sessions_with_segments"] == 1

    def test_segment_needs_update_no_segments(self, temp_db, sample_session):
        """Test that needs_update returns True when no segments exist."""
        temp_db.add_session(sample_session)
        
        result = temp_db.segment_needs_update(sample_session.session_id)
        assert result is True

    def test_segment_needs_update_up_to_date(self, temp_db, sample_session):
        """Test that needs_update returns False when segments are current."""
        temp_db.add_session(sample_session)
        segments = list(generate_segments(sample_session))
        temp_db.add_segment(segments[0])
        
        result = temp_db.segment_needs_update(sample_session.session_id)
        assert result is False

    def test_get_sessions_needing_update(self, temp_db, sample_session):
        """Test getting list of sessions needing segment updates."""
        temp_db.add_session(sample_session)
        
        # Initially, session needs update (no segments)
        needs_update = temp_db.get_sessions_needing_segment_update()
        assert sample_session.session_id in needs_update
        
        # Add segments
        segments = list(generate_segments(sample_session))
        temp_db.add_segment(segments[0])
        
        # Now it should not need update
        needs_update = temp_db.get_sessions_needing_segment_update()
        assert sample_session.session_id not in needs_update

    def test_list_segments(self, temp_db, sample_session):
        """Test listing all segments."""
        temp_db.add_session(sample_session)
        segments = list(generate_segments(sample_session))
        temp_db.add_segment(segments[0])
        
        all_segments = temp_db.list_segments()
        assert len(all_segments) == 1
        assert all_segments[0]["workspace_name"] == "my-project"

    def test_list_segments_filter_by_workspace(self, temp_db, sample_session, session_with_compaction):
        """Test filtering segments by workspace."""
        temp_db.add_session(sample_session)
        temp_db.add_session(session_with_compaction)
        
        for seg in generate_segments(sample_session):
            temp_db.add_segment(seg)
        for seg in generate_segments(session_with_compaction):
            temp_db.add_segment(seg)
        
        # Filter by workspace
        filtered = temp_db.list_segments(workspace_name="my-project")
        assert len(filtered) == 1
        
        filtered = temp_db.list_segments(workspace_name="compaction-project")
        assert len(filtered) == 2
