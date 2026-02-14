"""Copilot Session Tools - Common utilities.

This module provides shared functionality for working with VS Code Copilot chat history:
- Database: SQLite storage with FTS5 full-text search
- Scanner: Find and parse chat session files from VS Code workspace storage
- Markdown Exporter: Convert chat sessions to markdown format

This project borrows patterns from several open-source projects:
- simonw/claude-code-transcripts: HTML transcript generation approach
- Arbuzov/copilot-chat-history: VS Code Copilot chat session data format
- jazzyalex/agent-sessions: Multi-agent session concept, SQLite indexing
- tad-hq/universal-session-viewer: FTS5 full-text search design
"""

__version__ = "0.1.3"

from .database import Database, ParsedQuery, parse_search_query
from .html_exporter import (
    export_session_to_html_file,
    generate_session_html_filename,
    session_to_html,
)
from .markdown_exporter import (
    export_session_to_file,
    generate_session_filename,
    message_to_markdown,
    session_to_markdown,
)
from .scanner import (
    ChatMessage,
    ChatSession,
    CommandRun,
    ContentBlock,
    FileChange,
    ToolInvocation,
    detect_repository_url,
    find_copilot_chat_dirs,
    get_cli_storage_paths,
    get_vscode_storage_paths,
    scan_chat_sessions,
)

__all__ = [
    # Scanner - Data models
    "ChatMessage",
    "ChatSession",
    "CommandRun",
    "ContentBlock",
    # Database
    "Database",
    "FileChange",
    "ParsedQuery",
    "ToolInvocation",
    # Package
    "__version__",
    # Scanner - Discovery & parsing
    "detect_repository_url",
    # Markdown Exporter
    "export_session_to_file",
    # HTML Exporter
    "export_session_to_html_file",
    "find_copilot_chat_dirs",
    "generate_session_filename",
    "generate_session_html_filename",
    "get_cli_storage_paths",
    "get_vscode_storage_paths",
    "message_to_markdown",
    "parse_search_query",
    "scan_chat_sessions",
    "session_to_html",
    "session_to_markdown",
]
