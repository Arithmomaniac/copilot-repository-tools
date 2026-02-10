"""Copilot Repository Tools Memory - Semantic memory layer using Mem0.

This package provides:
- Automatic Mem0 setup when first run
- Semantic search across chat history
- Fact extraction from conversations
- Integration with existing SQLite database

Memory Scoping:
- Memories are automatically scoped to repositories (if in a git repo)
  or to folders (if not in a git repo).
- Each scope is isolated: memories from one scope cannot affect another.

Usage:
    uvx copilot-repository-tools-memory index --db copilot_chats.db
    uvx copilot-repository-tools-memory search "how did I handle errors?" --repository github.com/owner/repo
    uvx copilot-repository-tools-memory list --repository github.com/owner/repo
"""

from .cli import app, run
from .manager import ExtractedMemory, MemoryManager, _get_scope_id

__all__ = [
    "ExtractedMemory",
    "MemoryManager",
    "_get_scope_id",
    "app",
    "run",
]
