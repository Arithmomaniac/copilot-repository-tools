# Copilot Repository Tools - CLI

Command-line interface for scanning, searching, and exporting VS Code Copilot chat history.

Built with [Typer](https://typer.tiangolo.com/) for a modern CLI experience with rich help and auto-completion.

## Quick Start

Run directly with uvx (no installation needed):

```bash
# Scan for chat sessions
uvx copilot-repository-tools-cli scan

# Search through chat history
uvx copilot-repository-tools-cli search "authentication"

# View statistics
uvx copilot-repository-tools-cli stats
```

## Installation

```bash
uv tool install copilot-repository-tools-cli
```

Or with pip:

```bash
pip install copilot-repository-tools-cli
```

## Commands

### scan

Scan VS Code workspace storage for Copilot chat sessions:

```bash
copilot-chat-archive scan                    # Scan all editions
copilot-chat-archive scan --edition stable   # Scan only VS Code Stable
copilot-chat-archive scan --full             # Force full re-import
copilot-chat-archive scan --verbose          # Show progress
```

### search

Search through chat history:

```bash
copilot-chat-archive search "query"          # Basic search
copilot-chat-archive search "error" --role assistant
copilot-chat-archive search "git" --tools-only
copilot-chat-archive search "complex" --full # Show full content
```

### stats

View database statistics:

```bash
copilot-chat-archive stats
```

### export

Export sessions:

```bash
copilot-chat-archive export --output chats.json
copilot-chat-archive export-markdown --output-dir ./archive
```

### import-json

Import sessions from JSON:

```bash
copilot-chat-archive import-json backup.json
```

### serve

Start the web server (requires `copilot-repository-tools-web`):

```bash
copilot-chat-archive serve
```

## Attribution

This package borrows patterns from [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts).
