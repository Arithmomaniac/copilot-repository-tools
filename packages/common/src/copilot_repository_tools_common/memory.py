"""Mem0 integration for intelligent memory management.

This module provides semantic memory capabilities using Mem0,
enabling fact extraction, semantic search, and cross-session insights
from VS Code Copilot chat history.

Features:
- Extract facts and preferences from conversations
- Semantic search across all sessions
- User-scoped memories for personalization
- Workspace-scoped memories for project context

Mem0 is an optional dependency. When not installed, the module provides
clear error messages and guidance for installation.

LLM Configuration:
By default, this module is configured to use GitHub Copilot models via LiteLLM.
You can customize the LLM provider through the config parameter.

Example config for Copilot via LiteLLM:
    {
        "llm": {
            "provider": "litellm",
            "config": {
                "model": "github_copilot/gpt-4",
                "temperature": 0
            }
        },
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": "copilot_memories",
                "path": "./copilot_memories_db"
            }
        }
    }
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scanner import ChatSession

# Optional import - Mem0 is not required for core functionality
try:
    from mem0 import Memory

    MEM0_AVAILABLE = True
except ImportError:
    MEM0_AVAILABLE = False
    Memory = None


@dataclass
class ExtractedMemory:
    """A memory extracted from chat history.

    Attributes:
        id: Unique identifier for this memory.
        content: The extracted fact or insight content.
        metadata: Additional metadata (workspace, session_id, source, etc.).
        score: Relevance score from semantic search (None if not from search).
    """

    id: str
    content: str
    metadata: dict
    score: float | None = None


def _convert_mem0_result(result: dict, include_score: bool = False) -> list[ExtractedMemory]:
    """Convert Mem0 API response to list of ExtractedMemory objects.

    Args:
        result: Response dict from Mem0 API (expected to have "results" key).
        include_score: Whether to include score field from search results.

    Returns:
        List of ExtractedMemory objects.
    """
    if not isinstance(result, dict):
        return []

    results_list = result.get("results", [])
    memories = []

    for item in results_list:
        if isinstance(item, dict):
            memory = ExtractedMemory(
                id=item.get("id", ""),
                content=item.get("memory", ""),
                metadata=item.get("metadata", {}),
                score=item.get("score") if include_score else None,
            )
            memories.append(memory)

    return memories


def get_default_config() -> dict:
    """Get the default Mem0 configuration using Copilot via LiteLLM.

    Returns:
        Default configuration dict for Mem0 with LiteLLM provider.

    Note:
        This uses GitHub Copilot models through LiteLLM. On first use,
        you may need to authenticate via OAuth device flow.
    """
    return {
        "llm": {
            "provider": "litellm",
            "config": {
                "model": "github_copilot/gpt-4",
                "temperature": 0,
            },
        },
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": "copilot_memories",
                "path": "./copilot_memories_db",
            },
        },
        "version": "v1.1",
    }


class MemoryManager:
    """Manages Mem0 integration for chat history.

    Features:
    - Extract facts and preferences from conversations
    - Semantic search across all sessions
    - User-scoped memories for personalization
    - Workspace-scoped memories for project context

    Requires Mem0 to be installed:
        uv add mem0ai litellm
        # or
        pip install mem0ai litellm

    Example:
        >>> from copilot_repository_tools_common import Database
        >>> from copilot_repository_tools_common.memory import MemoryManager
        >>>
        >>> # Initialize with default config (uses Copilot via LiteLLM)
        >>> manager = MemoryManager()
        >>>
        >>> # Or with custom config
        >>> config = {
        ...     "llm": {"provider": "litellm", "config": {"model": "github_copilot/gpt-4"}},
        ...     "vector_store": {"provider": "chroma", "config": {"path": "./memories"}}
        ... }
        >>> manager = MemoryManager(config=config)
        >>>
        >>> # Index a session
        >>> db = Database("copilot_chats.db")
        >>> session = db.get_session("session-id")
        >>> memories = manager.add_session(session)
        >>>
        >>> # Search memories
        >>> results = manager.search("error handling patterns")
        >>> for mem in results:
        ...     print(f"- {mem.content} (score: {mem.score:.3f})")
    """

    def __init__(
        self,
        config: dict | None = None,
        user_id: str = "default",
    ):
        """Initialize the memory manager.

        Args:
            config: Mem0 configuration dict. If None, uses defaults with
                GitHub Copilot via LiteLLM.
            user_id: User identifier for memory scoping.

        Raises:
            ImportError: If Mem0 is not installed.
        """
        if not MEM0_AVAILABLE:
            raise ImportError("Mem0 is not installed. Install with:\n  uv add mem0ai litellm\nor:\n  pip install mem0ai litellm")

        self.user_id = user_id
        self.config = config or get_default_config()

        # Initialize Mem0 with configuration
        self.memory = Memory.from_config(self.config)

    def add_session(
        self,
        session: ChatSession,
        extract_facts: bool = True,
    ) -> list[ExtractedMemory]:
        """Process a chat session and extract memories.

        This method takes a ChatSession object and passes it to Mem0 for
        fact extraction. Mem0 will analyze the conversation and identify
        key facts, preferences, and patterns.

        Args:
            session: The chat session to process.
            extract_facts: Whether to extract facts (vs just storing for search).
                Currently always extracts facts via Mem0.

        Returns:
            List of extracted memories from this session.
        """
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
        }

        # Add to Mem0 - this triggers fact extraction
        result = self.memory.add(
            messages=messages,
            user_id=self.user_id,
            metadata=metadata,
        )

        return _convert_mem0_result(result)

    def search(
        self,
        query: str,
        limit: int = 10,
        workspace_name: str | None = None,
    ) -> list[ExtractedMemory]:
        """Semantic search across memories.

        Unlike the FTS5-based keyword search in the Database class, this
        performs semantic search using vector embeddings, allowing natural
        language queries like "How did I handle authentication errors?"

        Args:
            query: Natural language search query.
            limit: Maximum results to return.
            workspace_name: Optional workspace filter.

        Returns:
            List of relevant memories with similarity scores.
        """
        filters = {}
        if workspace_name:
            filters["workspace_name"] = workspace_name

        results = self.memory.search(
            query=query,
            user_id=self.user_id,
            limit=limit,
            filters=filters if filters else None,
        )

        return _convert_mem0_result(results, include_score=True)

    def get_all(
        self,
        workspace_name: str | None = None,
    ) -> list[ExtractedMemory]:
        """Get all stored memories.

        Args:
            workspace_name: Optional workspace filter.

        Returns:
            List of all memories matching the filter.
        """
        filters = {}
        if workspace_name:
            filters["workspace_name"] = workspace_name

        results = self.memory.get_all(
            user_id=self.user_id,
            filters=filters if filters else None,
        )

        return _convert_mem0_result(results)

    def delete(self, memory_id: str) -> bool:
        """Delete a specific memory.

        Args:
            memory_id: The ID of the memory to delete.

        Returns:
            True if deleted successfully, False otherwise.
        """
        try:
            self.memory.delete(memory_id=memory_id)
            return True
        except (ValueError, KeyError, RuntimeError):
            # ValueError: Invalid memory_id format
            # KeyError: Memory not found
            # RuntimeError: Mem0 internal errors
            return False

    def clear(self, workspace_name: str | None = None) -> int:
        """Clear all memories for a user or workspace.

        Args:
            workspace_name: If provided, only clear memories for this workspace.
                If None, clears all memories for the user.

        Returns:
            Number of memories deleted, or -1 if deleted all (count unknown).
        """
        if workspace_name:
            # Delete memories one by one for the workspace
            memories = self.get_all(workspace_name=workspace_name)
            count = 0
            for mem in memories:
                if self.delete(mem.id):
                    count += 1
            return count
        else:
            # Delete all memories for the user
            self.memory.delete_all(user_id=self.user_id)
            return -1  # Unknown count when deleting all
