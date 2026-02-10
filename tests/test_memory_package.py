"""Tests for the memory package (Mem0 integration)."""


class TestMemoryPackageImports:
    """Test that memory package can be imported correctly."""

    def test_package_imports(self):
        """Test that the package can be imported."""
        from copilot_repository_tools_memory import ExtractedMemory, MemoryManager, app, run

        assert MemoryManager is not None
        assert ExtractedMemory is not None
        assert app is not None
        assert callable(run)

    def test_extracted_memory_dataclass(self):
        """Test ExtractedMemory dataclass structure."""
        from copilot_repository_tools_memory import ExtractedMemory

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
        from copilot_repository_tools_memory import ExtractedMemory

        mem = ExtractedMemory(
            id="test-id",
            content="Some fact",
            metadata={},
        )

        assert mem.score is None

    def test_get_default_config(self):
        """Test default configuration returns expected structure."""
        from copilot_repository_tools_memory.manager import get_default_config

        config = get_default_config()

        # Should have llm config (github_copilot via our patch)
        assert "llm" in config
        assert config["llm"]["provider"] == "github_copilot"
        assert config["llm"]["config"]["model"] == "github_copilot/gpt-4o"

        # Should have embedder config (litellm for embeddings)
        assert "embedder" in config
        assert config["embedder"]["provider"] == "litellm"

        # Should have vector_store config
        assert "vector_store" in config
        assert config["vector_store"]["provider"] == "chroma"

        # Should have version
        assert config.get("version") == "v1.1"

    def test_get_default_config_custom_data_dir(self, tmp_path):
        """Test default configuration with custom data directory."""
        from copilot_repository_tools_memory.manager import get_default_config

        config = get_default_config(data_dir=tmp_path)

        # ChromaDB path should be under custom directory
        assert str(tmp_path) in config["vector_store"]["config"]["path"]


class TestCLICommands:
    """Test CLI command structure."""

    def test_cli_app_has_commands(self):
        """Test that CLI app has expected commands."""
        from copilot_repository_tools_memory.cli import app

        # Get registered commands
        command_names = [cmd.name for cmd in app.registered_commands]

        assert "setup" in command_names
        assert "index" in command_names
        assert "search" in command_names
        assert "list" in command_names
        assert "clear" in command_names
        assert "stats" in command_names
