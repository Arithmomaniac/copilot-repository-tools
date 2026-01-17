"""Tests for the memory module (Mem0 integration)."""

import pytest
from copilot_repository_tools_common import ChatMessage, ChatSession, MEM0_AVAILABLE


class TestMemoryModule:
    """Tests for memory module components."""

    def test_mem0_available_flag_exists(self):
        """Test that MEM0_AVAILABLE flag is properly exported."""
        # MEM0_AVAILABLE should be a boolean
        assert isinstance(MEM0_AVAILABLE, bool)

    def test_extracted_memory_dataclass(self):
        """Test ExtractedMemory dataclass structure."""
        from copilot_repository_tools_common.memory import ExtractedMemory

        # Create an ExtractedMemory instance
        mem = ExtractedMemory(
            id="test-id-123",
            content="User prefers Python type hints",
            metadata={"workspace_name": "my-project", "session_id": "session-456"},
            score=0.95,
        )

        assert mem.id == "test-id-123"
        assert mem.content == "User prefers Python type hints"
        assert mem.metadata["workspace_name"] == "my-project"
        assert mem.score == 0.95

    def test_extracted_memory_optional_score(self):
        """Test ExtractedMemory with optional score."""
        from copilot_repository_tools_common.memory import ExtractedMemory

        mem = ExtractedMemory(
            id="test-id",
            content="Some fact",
            metadata={},
        )

        assert mem.score is None

    def test_get_default_config(self):
        """Test default configuration returns expected structure."""
        from copilot_repository_tools_common.memory import get_default_config

        config = get_default_config()

        # Should have llm config
        assert "llm" in config
        assert config["llm"]["provider"] == "litellm"
        assert config["llm"]["config"]["model"] == "github_copilot/gpt-4"

        # Should have vector_store config
        assert "vector_store" in config
        assert config["vector_store"]["provider"] == "chroma"

        # Should have version
        assert config.get("version") == "v1.1"

    def test_memory_manager_import_error_without_mem0(self):
        """Test that MemoryManager raises ImportError when Mem0 is not installed."""
        if MEM0_AVAILABLE:
            pytest.skip("Mem0 is installed, cannot test ImportError")

        from copilot_repository_tools_common.memory import MemoryManager

        with pytest.raises(ImportError) as excinfo:
            MemoryManager()

        assert "mem0ai" in str(excinfo.value).lower() or "Mem0" in str(excinfo.value)


@pytest.mark.skipif(not MEM0_AVAILABLE, reason="Mem0 not installed")
class TestMemoryManagerWithMem0:
    """Tests that require Mem0 to be installed."""

    @pytest.fixture
    def sample_session(self):
        """Create a sample chat session for testing."""
        return ChatSession(
            session_id="test-memory-session-123",
            workspace_name="test-project",
            workspace_path="/path/to/test-project",
            messages=[
                ChatMessage(role="user", content="How do I create a Python dataclass?"),
                ChatMessage(
                    role="assistant",
                    content="Here's how to create a Python dataclass:\n\n```python\nfrom dataclasses import dataclass\n\n@dataclass\nclass Person:\n    name: str\n    age: int\n```",
                ),
                ChatMessage(role="user", content="Can you add a method?"),
                ChatMessage(
                    role="assistant",
                    content="Sure! You can add methods like this:\n\n```python\n@dataclass\nclass Person:\n    name: str\n    age: int\n    \n    def greet(self):\n        return f'Hello, {self.name}!'\n```",
                ),
            ],
            type="vscode",
        )

    def test_memory_manager_initialization(self, tmp_path):
        """Test MemoryManager can be initialized with custom config."""
        from copilot_repository_tools_common.memory import MemoryManager

        config = {
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
                    "collection_name": "test_memories",
                    "path": str(tmp_path / "test_memories_db"),
                },
            },
        }

        # This should not raise an exception
        manager = MemoryManager(config=config, user_id="test-user")
        assert manager.user_id == "test-user"
        assert manager.config == config


class TestMemoryExports:
    """Test that memory module exports are correct."""

    def test_all_exports_available(self):
        """Test all expected exports are available from the module."""
        from copilot_repository_tools_common import (
            ExtractedMemory,
            MEM0_AVAILABLE,
            MemoryManager,
            get_default_config,
        )

        # All should be importable (even if MemoryManager raises on use)
        assert ExtractedMemory is not None
        assert isinstance(MEM0_AVAILABLE, bool)
        assert MemoryManager is not None
        assert callable(get_default_config)
