"""Copilot Repository Tools Memory - Semantic memory layer using Mem0.

This package provides:
- Automatic Mem0 setup when first run
- Semantic search across chat history
- Fact extraction from conversations
- Integration with existing SQLite database

Usage:
    uvx copilot-repository-tools-memory setup
    uvx copilot-repository-tools-memory index --db copilot_chats.db
    uvx copilot-repository-tools-memory search "how did I handle errors?"
"""

from .cli import app, run
from .manager import ExtractedMemory, MemoryManager

__all__ = [
    "ExtractedMemory",
    "MemoryManager",
    "app",
    "run",
]
