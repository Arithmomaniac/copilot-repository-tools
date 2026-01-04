"""Module for generating markdown chat segments from sessions.

Segments are portions of chat sessions that can be used for analysis,
golden samples, and coding style extraction. Each segment:
- Is keyed by session ID and segment index
- Contains markdown-only content (no thinking blocks)
- Starts with either the first message or a summarization/compaction point
- Has metadata about the first and last messages
"""

from dataclasses import dataclass, field
from typing import Iterator

from .scanner import ChatSession, ChatMessage, ContentBlock


@dataclass
class ChatSegment:
    """Represents a segment of a chat session.
    
    A segment is a portion of a chat that starts with a user message
    and contains the following messages until a compaction/summarization occurs.
    """
    
    session_id: str
    segment_index: int  # 0-based index within the session
    first_message_content: str  # Content of the first user message
    first_message_index: int  # Index of the first message in the original session
    last_message_content: str  # Content of the last message
    last_message_index: int  # Index of the last message in the original session
    markdown_content: str  # Full markdown content of the segment (without thinking blocks)
    message_count: int  # Number of messages in this segment
    
    # Optional metadata
    workspace_name: str | None = None
    workspace_path: str | None = None
    created_at: str | None = None


def _is_compaction_boundary(message: ChatMessage, prev_message: ChatMessage | None) -> bool:
    """Detect if a message represents a compaction/summarization boundary.
    
    A compaction boundary is detected when:
    1. The message is from the user (compaction results in a new user prompt)
    2. There's a sudden context shift (e.g., mentions summarization, fresh start)
    3. The message contains summary-like markers
    
    Common patterns that indicate compaction:
    - "Based on the previous conversation..." or similar phrasing
    - Explicit summary markers
    - References to prior context being condensed
    
    Args:
        message: The current message to check
        prev_message: The previous message in the session
        
    Returns:
        True if this message starts a new segment due to compaction
    """
    if message.role != "user":
        return False
    
    content_lower = message.content.lower()
    
    # Detect summary/compaction markers
    compaction_markers = [
        "summarizing the conversation",
        "summary of our discussion",
        "based on the previous conversation",
        "continuing from our discussion",
        "picking up from where we left off",
        "context summary:",
        "previous context:",
        "conversation summary:",
        "to recap:",
        "resuming our conversation",
        "[context]",
        "[summary]",
    ]
    
    for marker in compaction_markers:
        if marker in content_lower:
            return True
    
    return False


def _render_message_to_markdown(message: ChatMessage, include_role: bool = True) -> str:
    """Render a message to markdown format, excluding thinking blocks.
    
    Args:
        message: The message to render
        include_role: Whether to include the role header
        
    Returns:
        Markdown string representation of the message
    """
    parts = []
    
    if include_role:
        role_display = "**User:**" if message.role == "user" else "**Assistant:**"
        parts.append(role_display)
        parts.append("")  # Empty line after role
    
    # If message has content blocks, filter out thinking blocks
    if message.content_blocks:
        for block in message.content_blocks:
            if block.kind != "thinking":
                parts.append(block.content)
    else:
        # Use the flat content
        parts.append(message.content)
    
    return "\n".join(parts)


def generate_segments(session: ChatSession) -> Iterator[ChatSegment]:
    """Generate chat segments from a session.
    
    Segments are created by:
    1. Starting a new segment at the first message
    2. Starting a new segment when a compaction/summarization is detected
    
    Args:
        session: The chat session to segment
        
    Yields:
        ChatSegment objects for each segment in the session
    """
    if not session.messages:
        return
    
    segment_index = 0
    segment_start_index = 0
    segment_messages = []
    
    for i, message in enumerate(session.messages):
        prev_message = session.messages[i - 1] if i > 0 else None
        
        # Check if this message starts a new segment (compaction boundary)
        if i > 0 and _is_compaction_boundary(message, prev_message):
            # Yield the current segment if it has messages
            if segment_messages:
                yield _create_segment(
                    session=session,
                    segment_index=segment_index,
                    messages=segment_messages,
                    start_index=segment_start_index,
                )
                segment_index += 1
            
            # Start a new segment
            segment_start_index = i
            segment_messages = [message]
        else:
            segment_messages.append(message)
    
    # Yield the final segment
    if segment_messages:
        yield _create_segment(
            session=session,
            segment_index=segment_index,
            messages=segment_messages,
            start_index=segment_start_index,
        )


def _create_segment(
    session: ChatSession,
    segment_index: int,
    messages: list[ChatMessage],
    start_index: int,
) -> ChatSegment:
    """Create a ChatSegment from a list of messages.
    
    Args:
        session: The parent session
        segment_index: The index of this segment within the session
        messages: The messages in this segment
        start_index: The index of the first message in the original session
        
    Returns:
        A ChatSegment object
    """
    # Render all messages to markdown (without thinking blocks)
    markdown_parts = []
    for msg in messages:
        rendered = _render_message_to_markdown(msg)
        markdown_parts.append(rendered)
        markdown_parts.append("")  # Empty line between messages
        markdown_parts.append("---")  # Separator
        markdown_parts.append("")
    
    markdown_content = "\n".join(markdown_parts).strip()
    
    # Get first and last message content
    first_msg = messages[0]
    last_msg = messages[-1]
    
    # For first message, use the raw content (for keying purposes)
    first_content = first_msg.content
    
    # For last message, also use raw content
    last_content = last_msg.content
    
    return ChatSegment(
        session_id=session.session_id,
        segment_index=segment_index,
        first_message_content=first_content,
        first_message_index=start_index,
        last_message_content=last_content,
        last_message_index=start_index + len(messages) - 1,
        markdown_content=markdown_content,
        message_count=len(messages),
        workspace_name=session.workspace_name,
        workspace_path=session.workspace_path,
        created_at=session.created_at,
    )
