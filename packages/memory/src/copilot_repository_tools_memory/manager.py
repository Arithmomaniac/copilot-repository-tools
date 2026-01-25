"""Memory Manager for Mem0 integration.

This module provides the MemoryManager class for semantic memory operations
using Mem0. It handles:
- Automatic setup of Mem0 with local ChromaDB
- Fact extraction from chat sessions
- Semantic search across memories
- Incremental indexing (skips already-indexed sessions)

Memory Scoping:
- Memories are automatically scoped to repositories (if workspace is in a git repo)
  or to workspaces/folders (if not in a git repo).
- Each scope is isolated: memories from one scope cannot modify or invalidate
  memories from another scope.
- Cross-scope querying is supported via the `all_scopes` parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

# Apply LiteLLM embeddings patch before importing Memory
from copilot_repository_tools_memory.litellm_embeddings import patch_mem0_for_litellm

patch_mem0_for_litellm()

from copilot_repository_tools_common.scanner import detect_repository_url
from mem0 import Memory

if TYPE_CHECKING:
    from copilot_repository_tools_common import ChatSession


def _normalize_path(path: str) -> str:
    """Normalize a file path for consistent scope matching.

    Handles Windows path quirks:
    - Converts backslashes to forward slashes
    - Lowercases drive letters (C: -> c:)
    - Removes trailing slashes
    - Preserves the rest of the path case (some systems are case-sensitive)

    Args:
        path: The path to normalize.

    Returns:
        Normalized path string.
    """
    import sys

    # Convert backslashes to forward slashes
    normalized = path.replace("\\", "/")

    # On Windows, lowercase the drive letter for consistency
    if sys.platform == "win32" and len(normalized) >= 2 and normalized[1] == ":":
        normalized = normalized[0].lower() + normalized[1:]

    # Remove trailing slash
    normalized = normalized.rstrip("/")

    return normalized


def _get_scope_id(repository_url: str | None, workspace_path: str | None) -> str:
    """Get a scope identifier for memory isolation.

    Memories are scoped to:
    1. Repository (if workspace is in a git repo) - normalized URL
    2. Workspace/folder (if not in a git repo) - workspace path (normalized)

    Args:
        repository_url: The normalized git repository URL (e.g., 'github.com/owner/repo').
        workspace_path: The workspace path.

    Returns:
        A scope identifier string. Returns 'unknown' if neither is available.
    """
    if repository_url:
        # Use repository URL as scope (allows memories to be shared across worktrees)
        return f"repo:{repository_url}"
    elif workspace_path:
        # Normalize the path for consistent matching across sessions
        normalized_path = _normalize_path(workspace_path)
        return f"folder:{normalized_path}"
    else:
        return "unknown"


@dataclass
class ExtractedMemory:
    """A memory extracted from chat history.

    Attributes:
        id: Unique identifier for this memory.
        content: The extracted fact or insight.
        metadata: Additional context (workspace, session_id, scope, etc.).
        score: Relevance score from search (None if not from search).
    """

    id: str
    content: str
    metadata: dict
    score: float | None = None


def get_default_config(data_dir: Path | None = None) -> dict:
    """Get the default Mem0 configuration using local ChromaDB and LiteLLM.

    This configuration uses:
    - ChromaDB for local vector storage (no external services needed)
    - LiteLLM with GitHub Copilot models for embeddings and LLM

    Args:
        data_dir: Directory for storing ChromaDB data. Defaults to ~/.copilot-memory

    Returns:
        Mem0 configuration dictionary.
    """
    import litellm

    # GitHub Copilot doesn't support all OpenAI params, so drop unsupported ones
    litellm.drop_params = True

    if data_dir is None:
        data_dir = Path.home() / ".copilot-memory"

    data_dir.mkdir(parents=True, exist_ok=True)

    return {
        "llm": {
            "provider": "github_copilot",
            "config": {
                "model": "github_copilot/gpt-4o",
                "temperature": 0,
            },
        },
        "embedder": {
            "provider": "litellm",
            "config": {
                "model": "github_copilot/text-embedding-3-small",
            },
        },
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": "copilot_memories",
                "path": str(data_dir / "chroma_db"),
            },
        },
        "version": "v1.1",
    }


def _convert_mem0_result(result: dict) -> list[ExtractedMemory]:
    """Convert Mem0 result format to ExtractedMemory objects."""
    memories = []
    results_list = result.get("results", [])
    if not isinstance(results_list, list):
        results_list = []

    for mem in results_list:
        if isinstance(mem, dict):
            memories.append(
                ExtractedMemory(
                    id=mem.get("id", ""),
                    content=mem.get("memory", ""),
                    metadata=mem.get("metadata", {}),
                    score=mem.get("score"),
                )
            )
    return memories


class MemoryManager:
    """Manages Mem0 integration for chat history.

    Memory Scoping:
    - Memories are automatically scoped to repositories (if in a git repo)
      or to workspaces/folders (if not in a git repo).
    - Each scope is isolated using a unique user_id in Mem0.
    - Memories from one scope cannot modify or invalidate memories from another.

    Features:
    - Extract facts and preferences from conversations
    - Semantic search within a scope
    - Repository-scoped memories (shared across worktrees)
    - Folder-scoped memories (for non-git workspaces)
    - Incremental indexing (tracks which sessions are indexed)

    Example:
        >>> manager = MemoryManager()
        >>> # Index a session (automatically scoped)
        >>> memories = manager.add_session(session)
        >>> # Search within scope
        >>> results = manager.search("error handling patterns", scope_id="repo:github.com/owner/repo")
    """

    def __init__(
        self,
        config: dict | None = None,
        data_dir: Path | None = None,
    ):
        """Initialize the memory manager.

        Args:
            config: Mem0 configuration dict. If None, uses default local config.
            data_dir: Directory for storing data. Only used if config is None.
        """
        self.config = config or get_default_config(data_dir)

        # Initialize Mem0 with configuration
        self.memory = Memory.from_config(self.config)

    def is_session_indexed(self, session_id: str, scope_id: str) -> tuple[bool, int]:
        """Check if a session has already been indexed within a scope.

        Args:
            session_id: The session ID to check.
            scope_id: The scope identifier (from _get_scope_id).

        Returns:
            Tuple of (is_indexed, message_count) where message_count is the
            number of messages that were indexed (0 if not indexed).
        """
        try:
            results = self.memory.get_all(
                user_id=scope_id,
                filters={"session_id": session_id},
            )
            memories = results.get("results", []) if isinstance(results, dict) else []
            if memories:
                first_mem = memories[0] if memories else {}
                msg_count = first_mem.get("metadata", {}).get("message_count", 0)
                return True, msg_count
            return False, 0
        except (ValueError, KeyError, RuntimeError):
            return False, 0

    def add_session(
        self,
        session: ChatSession,
        extract_facts: bool = True,
        force: bool = False,
    ) -> list[ExtractedMemory]:
        """Process a chat session and extract memories.

        Memories are automatically scoped based on the session:
        - If the session has a repository_url, memories are scoped to that repository
        - Otherwise, memories are scoped to the workspace path (folder)

        This method takes a ChatSession object and passes it to Mem0 for
        fact extraction. Mem0 will analyze the conversation and identify
        key facts, preferences, and patterns.

        Supports incremental indexing: if a session has already been indexed
        with the same number of messages, it will be skipped (unless force=True).
        If the session has new messages, it will be re-indexed.

        Args:
            session: The chat session to process.
            extract_facts: Whether to extract facts (vs just storing for search).
                Currently always extracts facts via Mem0.
            force: If True, re-index even if already indexed.

        Returns:
            List of extracted memories from this session.
            Returns empty list if session was skipped (already indexed).
        """
        # Determine the scope for this session
        # If repository_url is missing, try to detect it from workspace path
        # This handles worktrees and sessions imported before detection was added
        repository_url = session.repository_url
        if not repository_url and session.workspace_path:
            repository_url = detect_repository_url(session.workspace_path)
        scope_id = _get_scope_id(repository_url, session.workspace_path)
        current_message_count = len(session.messages)

        # Check if already indexed (skip if same message count, unless forced)
        if not force:
            is_indexed, indexed_msg_count = self.is_session_indexed(session.session_id, scope_id)
            if is_indexed and indexed_msg_count >= current_message_count:
                return []

            # If session has new messages, clear old memories first
            if is_indexed and indexed_msg_count < current_message_count:
                self.clear(scope_id=scope_id, session_id=session.session_id)

        # Build conversation context for Mem0
        messages = []
        for msg in session.messages:
            messages.append(
                {
                    "role": msg.role,
                    "content": msg.content[:8000],  # Limit content size for API
                }
            )

        # Build metadata for the session
        metadata = {
            "session_id": session.session_id,
            "workspace_name": session.workspace_name or "unknown",
            "workspace_path": session.workspace_path or "",
            "source": session.type or "vscode",
            "message_count": current_message_count,
            "repository_url": session.repository_url or "",
            "scope_id": scope_id,
        }

        # Add to Mem0 - this triggers fact extraction
        # Use scope_id as user_id for memory isolation
        result = self.memory.add(
            messages=messages,
            user_id=scope_id,
            metadata=metadata,
        )

        return _convert_mem0_result(result)

    def search(
        self,
        query: str,
        limit: int = 10,
        scope_id: str | None = None,
        repository_url: str | None = None,
        workspace_path: str | None = None,
    ) -> list[ExtractedMemory]:
        """Semantic search across memories within a scope.

        Searches within a single scope (repository or folder). Each scope is
        isolated, so memories from different scopes are not mixed.

        Args:
            query: Natural language search query.
            limit: Maximum results to return.
            scope_id: Direct scope identifier. If provided, searches within this scope.
            repository_url: Repository URL to derive scope from.
            workspace_path: Workspace path to derive scope from (used if no repository).

        Returns:
            List of relevant memories with scores.
        """
        # Determine scope_id
        if not scope_id:
            scope_id = _get_scope_id(repository_url, workspace_path)

        if scope_id == "unknown":
            # No scope specified - return empty results
            return []

        results = self.memory.search(
            query=query,
            user_id=scope_id,
            limit=limit,
        )

        return [
            ExtractedMemory(
                id=r.get("id", ""),
                content=r.get("memory", ""),
                metadata=r.get("metadata", {}),
                score=r.get("score"),
            )
            for r in results.get("results", [])
        ]

    def get_all(
        self,
        scope_id: str | None = None,
        repository_url: str | None = None,
        workspace_path: str | None = None,
    ) -> list[ExtractedMemory]:
        """Get all stored memories within a scope.

        Each scope is isolated, so this only returns memories from the
        specified scope.

        Args:
            scope_id: Direct scope identifier.
            repository_url: Repository URL to derive scope from.
            workspace_path: Workspace path to derive scope from.

        Returns:
            List of all memories in the scope.
        """
        # Determine scope_id
        if not scope_id:
            scope_id = _get_scope_id(repository_url, workspace_path)

        if scope_id == "unknown":
            return []

        results = self.memory.get_all(user_id=scope_id)

        return [
            ExtractedMemory(
                id=r.get("id", ""),
                content=r.get("memory", ""),
                metadata=r.get("metadata", {}),
            )
            for r in results.get("results", [])
        ]

    def delete(self, memory_id: str) -> bool:
        """Delete a specific memory.

        Args:
            memory_id: The ID of the memory to delete.

        Returns:
            True if deleted successfully.
        """
        try:
            self.memory.delete(memory_id=memory_id)
            return True
        except Exception:
            return False

    def clear(
        self,
        scope_id: str | None = None,
        repository_url: str | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """Clear memories within a scope.

        Memories from one scope cannot affect memories from another scope.

        Args:
            scope_id: Direct scope identifier.
            repository_url: Repository URL to derive scope from.
            workspace_path: Workspace path to derive scope from.
            session_id: If provided, only clear memories for this specific session
                within the scope.

        Returns:
            Number of memories deleted, or -1 if deleted all in scope.
        """
        # Determine scope_id
        if not scope_id:
            scope_id = _get_scope_id(repository_url, workspace_path)

        if scope_id == "unknown":
            return 0

        if session_id:
            # Clear only memories for this session within the scope
            try:
                results = self.memory.get_all(
                    user_id=scope_id,
                    filters={"session_id": session_id},
                )
                memories_list = results.get("results", []) if isinstance(results, dict) else []
                count = 0
                for mem in memories_list:
                    if isinstance(mem, dict) and self.delete(mem.get("id", "")):
                        count += 1
                return count
            except (ValueError, KeyError, RuntimeError):
                return 0
        else:
            # Clear all memories in this scope
            self.memory.delete_all(user_id=scope_id)
            return -1

    def get_scope_stats(self, scope_id: str) -> dict:
        """Get statistics for a specific scope.

        Args:
            scope_id: The scope identifier.

        Returns:
            Dict with memory count and other stats.
        """
        memories = self.get_all(scope_id=scope_id)
        return {
            "scope_id": scope_id,
            "memory_count": len(memories),
        }
