"""Copilot Chat Archive - Create a searchable archive of VS Code GitHub Copilot chat history.

This project borrows patterns from several open-source projects:
- simonw/claude-code-transcripts: HTML transcript generation approach
- Arbuzov/copilot-chat-history: VS Code Copilot chat session data format
- jazzyalex/agent-sessions: Multi-agent session concept, SQLite indexing
- tad-hq/universal-session-viewer: FTS5 full-text search design
"""

__version__ = "0.1.0"

from .scanner import (
    ChatMessage,
    ChatSession,
    CommandRun,
    FileChange,
    ToolInvocation,
    find_copilot_chat_dirs,
    scan_chat_sessions,
)
from .database import Database
from .viewer import generate_html

__all__ = [
    "__version__",
    "ChatMessage",
    "ChatSession",
    "CommandRun",
    "FileChange",
    "ToolInvocation",
    "find_copilot_chat_dirs",
    "scan_chat_sessions",
    "Database",
    "generate_html",
]
