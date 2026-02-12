"""Scanner package to find and parse VS Code Copilot chat history files.

Data structures are informed by:
- Arbuzov/copilot-chat-history (https://github.com/Arbuzov/copilot-chat-history)
- microsoft/vscode-copilot-chat (https://github.com/microsoft/vscode-copilot-chat)
"""

from .cli import _parse_cli_jsonl_file, _parse_workspace_yaml
from .content import (
    _extract_edit_group_text,
    _extract_inline_reference_name,
    _merge_content_blocks,
)
from .discovery import (
    find_copilot_chat_dirs,
    get_cli_storage_paths,
    get_vscode_storage_paths,
    parse_session_file,
    scan_chat_sessions,
    scan_session_files,
)
from .git import (
    _clear_repository_url_cache,
    _normalize_git_url,
    detect_repository_url,
)
from .models import (
    ChatMessage,
    ChatSession,
    CommandRun,
    ContentBlock,
    FileChange,
    SessionFileInfo,
    ToolInvocation,
)
from .vscode import (
    _apply_jsonl_operations,
    _extract_session_from_dict,
    _parse_chat_session_file,
    _parse_tool_invocation_serialized,
    _parse_vscdb_file,
    _parse_vscode_jsonl_file,
)

__all__ = [
    "ChatMessage",
    "ChatSession",
    "CommandRun",
    "ContentBlock",
    "FileChange",
    "SessionFileInfo",
    "ToolInvocation",
    "_apply_jsonl_operations",
    "_clear_repository_url_cache",
    "_extract_edit_group_text",
    "_extract_inline_reference_name",
    "_extract_session_from_dict",
    "_merge_content_blocks",
    "_normalize_git_url",
    "_parse_chat_session_file",
    "_parse_cli_jsonl_file",
    "_parse_tool_invocation_serialized",
    "_parse_vscdb_file",
    "_parse_vscode_jsonl_file",
    "_parse_workspace_yaml",
    "detect_repository_url",
    "find_copilot_chat_dirs",
    "get_cli_storage_paths",
    "get_vscode_storage_paths",
    "parse_session_file",
    "scan_chat_sessions",
    "scan_session_files",
]
