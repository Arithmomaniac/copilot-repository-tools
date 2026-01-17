# Mem0 Memory Layer Configuration Examples

This directory contains example configuration files for the Mem0 memory integration.

## Quick Start (Default - GitHub Copilot)

The default configuration uses GitHub Copilot models via LiteLLM. No configuration
file is needed - just run:

```bash
# Install dependencies
uv add mem0ai litellm
# or: pip install mem0ai litellm

# Index your sessions
copilot-chat-archive memory index --db copilot_chats.db

# Search semantically
copilot-chat-archive memory search "how did I handle errors?"
```

On first use, you'll be prompted to authenticate via OAuth device flow.

## Configuration Files

### `mem0-config-copilot.json` - GitHub Copilot via LiteLLM (default)

Uses GitHub Copilot's GPT-4 model for fact extraction. Requires Copilot subscription.
Authentication is handled via OAuth device flow on first use.

```bash
copilot-chat-archive memory index --config docs/memory-config-examples/mem0-config-copilot.json
```

### `mem0-config-openai.json` - OpenAI API

Uses OpenAI's GPT-4o-mini model. Requires `OPENAI_API_KEY` environment variable.

```bash
export OPENAI_API_KEY="your-key-here"
copilot-chat-archive memory index --config docs/memory-config-examples/mem0-config-openai.json
```

### `mem0-config-ollama.json` - Local Ollama (Self-hosted)

Uses Ollama for fully local, private memory extraction. No API keys needed.

1. Install Ollama: https://ollama.ai
2. Pull the required models:
   ```bash
   ollama pull llama3.2
   ollama pull nomic-embed-text
   ```
3. Run the memory commands:
   ```bash
   copilot-chat-archive memory index --config docs/memory-config-examples/mem0-config-ollama.json
   ```

## Custom Configuration

Create your own JSON configuration file with these sections:

```json
{
  "llm": {
    "provider": "litellm",  // or "openai", "ollama", "anthropic", etc.
    "config": {
      "model": "github_copilot/gpt-4",
      "temperature": 0
    }
  },
  "embedder": {
    "provider": "openai",  // Optional - defaults to llm provider
    "config": {
      "model": "text-embedding-ada-002"
    }
  },
  "vector_store": {
    "provider": "chroma",  // or "qdrant", "pinecone", etc.
    "config": {
      "collection_name": "copilot_memories",
      "path": "./copilot_memories_db"
    }
  },
  "version": "v1.1"
}
```

## LiteLLM Provider Support

When using `"provider": "litellm"`, you can access 100+ LLM providers with a unified API:

- `github_copilot/gpt-4` - GitHub Copilot (requires Copilot subscription)
- `openai/gpt-4o` - OpenAI GPT-4o
- `anthropic/claude-3-5-sonnet-20241022` - Anthropic Claude
- `bedrock/anthropic.claude-3-sonnet-20240229-v1:0` - AWS Bedrock
- `azure/gpt-4o` - Azure OpenAI

See https://docs.litellm.ai/docs/providers for the full list.

## Python API Usage

```python
from copilot_repository_tools_common import Database
from copilot_repository_tools_common.memory import MemoryManager

# Initialize with default config (Copilot via LiteLLM)
manager = MemoryManager()

# Or with custom config
config = {
    "llm": {"provider": "litellm", "config": {"model": "github_copilot/gpt-4"}},
    "vector_store": {"provider": "chroma", "config": {"path": "./memories"}}
}
manager = MemoryManager(config=config)

# Index sessions
db = Database("copilot_chats.db")
for session_info in db.list_sessions():
    session = db.get_session(session_info["session_id"])
    memories = manager.add_session(session)
    print(f"Extracted {len(memories)} memories from {session.workspace_name}")

# Semantic search
results = manager.search("error handling patterns")
for r in results:
    print(f"- {r.content} (score: {r.score:.3f})")
```
