"""Data models for Copilot chat session scanning."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolInvocation:
    """Represents a tool invocation in a chat response.

    Based on ChatToolInvocation from Arbuzov/copilot-chat-history.
    """

    name: str
    input: str | None = None
    result: str | None = None
    status: str | None = None
    start_time: int | None = None
    end_time: int | None = None
    source_type: str | None = None  # 'mcp' or 'internal'
    invocation_message: str | None = None  # Pretty display message (e.g., "Reading file.txt, lines 1 to 100")


@dataclass
class FileChange:
    """Represents a file change in a chat response.

    Based on ChatFileChange from Arbuzov/copilot-chat-history.
    """

    path: str
    diff: str | None = None
    content: str | None = None
    explanation: str | None = None
    language_id: str | None = None


@dataclass
class CommandRun:
    """Represents a command execution in a chat response.

    Based on ChatCommandRun from Arbuzov/copilot-chat-history.
    """

    command: str
    title: str | None = None
    result: str | None = None
    status: str | None = None
    output: str | None = None
    timestamp: int | None = None


@dataclass
class ContentBlock:
    """Represents a content block in an assistant response.

    Each block has a kind (e.g., 'text', 'thinking', 'tool') and content.
    This allows differentiation between thinking/reasoning and regular output.
    """

    kind: str  # 'text', 'thinking', 'tool', 'promptFile', etc.
    content: str
    description: str | None = None  # Optional description (e.g., generatedTitle for thinking blocks)


@dataclass
class ChatMessage:
    """Represents a single message in a chat session.

    Enhanced based on ChatMessage/ChatResponseItem from Arbuzov/copilot-chat-history.
    """

    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str | None = None
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    file_changes: list[FileChange] = field(default_factory=list)
    command_runs: list[CommandRun] = field(default_factory=list)
    content_blocks: list[ContentBlock] = field(default_factory=list)  # Structured content with kind
    cached_markdown: str | None = None  # Pre-computed markdown for this message


@dataclass
class ChatSession:
    """Represents a Copilot chat session.

    Based on ChatSession/ChatSessionData from Arbuzov/copilot-chat-history.
    """

    session_id: str
    workspace_name: str | None
    workspace_path: str | None
    messages: list[ChatMessage]
    created_at: str | None = None
    updated_at: str | None = None
    source_file: str | None = None
    vscode_edition: str = "stable"  # 'stable' or 'insider'
    custom_title: str | None = None
    requester_username: str | None = None
    responder_username: str | None = None
    source_file_mtime: float | None = None  # File modification time for incremental refresh
    source_file_size: int | None = None  # File size in bytes for incremental refresh
    type: str = "vscode"  # 'vscode' or 'cli'
    raw_json: bytes | None = None  # Original raw JSON bytes from source file
    repository_url: str | None = None  # Git remote URL for repository-scoped memories


@dataclass
class SessionFileInfo:
    """Lightweight metadata about a session file for incremental scanning.

    This allows checking mtime/size before expensive parsing.
    """

    file_path: Path
    file_type: str  # 'json', 'vscdb', 'jsonl'
    session_type: str  # 'vscode' or 'cli'
    vscode_edition: str  # 'stable', 'insider', or 'cli'
    mtime: float
    size: int
    workspace_name: str | None = None
    workspace_path: str | None = None
