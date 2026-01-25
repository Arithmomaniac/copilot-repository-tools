"""LiteLLM Embeddings support for Mem0.

This module provides a LiteLLM-based embedding implementation for Mem0 and
monkey-patches Mem0 to support the 'litellm' provider for embeddings.

Also provides a GitHub Copilot-specific LLM implementation that includes
the required extra headers for IDE authentication.

Based on: https://github.com/nocyphr/mem0-embeddings-litellm-patch/
"""

import json
from typing import Any, Literal

import litellm
from litellm import embedding
from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.configs.llms.base import BaseLlmConfig
from mem0.embeddings.base import EmbeddingBase
from mem0.llms.base import LLMBase
from mem0.memory.utils import extract_json

# GitHub Copilot requires these headers for IDE authentication
COPILOT_HEADERS = {
    "editor-version": "vscode/1.96.0",
    "editor-plugin-version": "copilot/1.155.0",
    "Copilot-Integration-Id": "vscode-chat",
    "user-agent": "GithubCopilot/1.155.0",
}


class LiteLLMEmbedding(EmbeddingBase):
    """LiteLLM-based embedding implementation for Mem0.

    This allows using any embedding provider supported by LiteLLM,
    including GitHub Copilot models via the github_copilot/ prefix.
    """

    def __init__(self, config: BaseEmbedderConfig | None = None):
        super().__init__(config)
        self.config.api_key = self.config.api_key
        self.config.model = self.config.model
        self.config.embedding_dims = self.config.embedding_dims or None

    def embed(
        self,
        text: str,
        memory_action: Literal["add", "search", "update"] | None = None,
    ) -> list[float]:
        """Get the embedding for the given text using LiteLLM.

        Args:
            text: The text to embed.
            memory_action: The type of memory action (unused, for interface compat).

        Returns:
            The embedding vector as a list of floats.
        """
        try:
            response = embedding(
                model=self.config.model,
                input=[text],
                dimensions=self.config.embedding_dims,
                api_key=self.config.api_key,
            )
            return response["data"][0]["embedding"]
        except Exception as e:
            raise RuntimeError(f"Error generating embedding with LiteLLM: {e!s}")


class GithubCopilotLLM(LLMBase):
    """GitHub Copilot LLM implementation with required headers.

    This extends the standard LiteLLM implementation but adds the required
    extra_headers for GitHub Copilot IDE authentication.
    """

    def __init__(self, config: BaseLlmConfig | None = None):
        super().__init__(config)
        if not self.config.model:
            self.config.model = "github_copilot/gpt-4o"

    def _parse_response(self, response: Any, tools: list[dict[str, Any]] | None) -> Any:
        """Process the response based on whether tools are used or not."""
        if tools:
            processed_response: dict[str, Any] = {
                "content": response.choices[0].message.content,
                "tool_calls": [],
            }

            if response.choices[0].message.tool_calls:
                for tool_call in response.choices[0].message.tool_calls:
                    processed_response["tool_calls"].append(
                        {
                            "name": tool_call.function.name,
                            "arguments": json.loads(extract_json(tool_call.function.arguments)),
                        }
                    )

            return processed_response
        else:
            return response.choices[0].message.content

    def generate_response(
        self,
        messages: list[dict[str, str]],
        response_format: Any = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
    ) -> Any:
        """Generate a response using GitHub Copilot via LiteLLM.

        Args:
            messages: List of message dicts containing 'role' and 'content'.
            response_format: Format of the response (may not be supported by Copilot).
            tools: List of tools that the model can call.
            tool_choice: Tool choice method.

        Returns:
            The generated response.
        """
        params: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "top_p": self.config.top_p,
            "extra_headers": COPILOT_HEADERS,
        }

        # Note: response_format may not be supported by GitHub Copilot
        # litellm.drop_params should handle this
        if response_format:
            params["response_format"] = response_format
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        response = litellm.completion(**params)
        return self._parse_response(response, tools)


_patched = False


def patch_mem0_for_litellm() -> None:
    """Monkey-patch Mem0 to support LiteLLM embeddings and GitHub Copilot LLM.

    This function patches:
    1. EmbedderFactory to include 'litellm' provider
    2. LlmFactory to include 'github_copilot' provider
    3. Replaces the EmbedderConfig class with one that allows 'litellm'

    Call this before creating any Mem0 Memory instances.
    """
    global _patched
    if _patched:
        return
    _patched = True

    # Patch the EmbedderFactory
    from mem0.configs.llms.base import BaseLlmConfig
    from mem0.utils.factory import EmbedderFactory, LlmFactory

    if "litellm" not in EmbedderFactory.provider_to_class:
        EmbedderFactory.provider_to_class["litellm"] = "copilot_repository_tools_memory.litellm_embeddings.LiteLLMEmbedding"

    # Add github_copilot as an LLM provider (tuple format: class_path, config_class)
    if "github_copilot" not in LlmFactory.provider_to_class:
        LlmFactory.provider_to_class["github_copilot"] = (
            "copilot_repository_tools_memory.litellm_embeddings.GithubCopilotLLM",
            BaseLlmConfig,
        )

    # Create a new EmbedderConfig class that allows litellm
    from typing import Any

    from pydantic import BaseModel, Field, ValidationInfo, field_validator

    class PatchedEmbedderConfig(BaseModel):
        """EmbedderConfig that allows litellm provider."""

        provider: str = Field(
            description="Provider of the embedding model (e.g., 'ollama', 'openai')",
            default="openai",
        )
        config: dict | None = Field(
            description="Configuration for the specific embedding model",
            default={},
        )

        @field_validator("config")
        @classmethod
        def validate_config(cls, v: Any, info: ValidationInfo) -> Any:
            provider = info.data.get("provider")
            allowed_providers = [
                "openai",
                "ollama",
                "huggingface",
                "azure_openai",
                "gemini",
                "vertexai",
                "together",
                "lmstudio",
                "langchain",
                "aws_bedrock",
                "fastembed",
                "litellm",  # Added litellm support
            ]
            if provider in allowed_providers:
                return v
            else:
                raise ValueError(f"Unsupported embedding provider: {provider}")

    # Create a patched LlmConfig that allows github_copilot
    class PatchedLlmConfig(BaseModel):
        """LlmConfig that allows github_copilot provider."""

        provider: str = Field(
            description="Provider of the LLM (e.g., 'ollama', 'openai')",
            default="openai",
        )
        config: dict | None = Field(
            description="Configuration for the specific LLM",
            default={},
        )

        @field_validator("config")
        @classmethod
        def validate_config(cls, v: Any, info: ValidationInfo) -> Any:
            provider = info.data.get("provider")
            allowed_providers = [
                "openai",
                "ollama",
                "anthropic",
                "groq",
                "together",
                "aws_bedrock",
                "litellm",
                "azure_openai",
                "openai_structured",
                "azure_openai_structured",
                "gemini",
                "deepseek",
                "xai",
                "sarvam",
                "lmstudio",
                "vllm",
                "langchain",
                "github_copilot",  # Added github_copilot support
            ]
            if provider in allowed_providers:
                return v
            else:
                raise ValueError(f"Unsupported LLM provider: {provider}")

    # Replace the EmbedderConfig and LlmConfig in all relevant modules
    import mem0.configs.base
    import mem0.embeddings.configs
    import mem0.llms.configs

    mem0.embeddings.configs.EmbedderConfig = PatchedEmbedderConfig
    mem0.configs.base.EmbedderConfig = PatchedEmbedderConfig
    mem0.llms.configs.LlmConfig = PatchedLlmConfig
    mem0.configs.base.LlmConfig = PatchedLlmConfig

    # Now we need to recreate MemoryConfig with the patched configs
    # This is necessary because Pydantic compiles field types at class definition time

    from mem0.configs.base import (
        GraphStoreConfig,
        RerankerConfig,
        VectorStoreConfig,
    )
    from pydantic import ConfigDict

    class PatchedMemoryConfig(BaseModel):
        """Patched MemoryConfig with LiteLLM embedder and GitHub Copilot LLM support."""

        model_config = ConfigDict(
            arbitrary_types_allowed=True,
            extra="forbid",
        )

        vector_store: VectorStoreConfig = Field(
            description="Configuration for the vector store",
            default_factory=VectorStoreConfig,
        )
        llm: PatchedLlmConfig = Field(
            description="Configuration for the LLM (Language Model)",
            default_factory=PatchedLlmConfig,
        )
        embedder: PatchedEmbedderConfig = Field(
            description="Configuration for the embedding model",
            default_factory=PatchedEmbedderConfig,
        )
        history_db_path: str = Field(
            description="Path to the history database",
            default="",
        )
        graph_store: GraphStoreConfig = Field(
            description="Configuration for the graph store",
            default_factory=GraphStoreConfig,
        )
        reranker: RerankerConfig | None = Field(
            description="Configuration for the reranker",
            default=None,
        )
        version: str = Field(
            description="Version of the config",
            default="v1.1",
        )
        custom_fact_extraction_prompt: str | None = Field(
            description="Custom prompt for fact extraction",
            default=None,
        )
        custom_update_memory_prompt: str | None = Field(
            description="Custom prompt for update memory",
            default=None,
        )

    # Replace MemoryConfig
    mem0.configs.base.MemoryConfig = PatchedMemoryConfig

    # Also patch in memory.main where it's imported
    import mem0.memory.main

    mem0.memory.main.MemoryConfig = PatchedMemoryConfig
