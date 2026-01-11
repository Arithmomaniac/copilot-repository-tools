# Copilot Repository Tools - Common

Core utilities for working with VS Code GitHub Copilot chat history.

## Features

- **Database**: SQLite storage with FTS5 full-text search
- **Scanner**: Find and parse chat session files from VS Code workspace storage
- **Markdown Exporter**: Convert chat sessions to markdown format

## Installation

```bash
uv add copilot-repository-tools-common
```

Or with pip:

```bash
pip install copilot-repository-tools-common
```

## Usage

```python
from copilot_repository_tools_common import (
    Database,
    scan_chat_sessions,
    get_vscode_storage_paths,
)

# Scan for sessions
paths = get_vscode_storage_paths()
for session in scan_chat_sessions(paths):
    print(f"Found session: {session.session_id}")

# Store in database
db = Database("copilot_chats.db")
for session in scan_chat_sessions(paths):
    db.add_session(session)

# Search
results = db.search("authentication", limit=10)
```

## Attribution

This package borrows patterns from:

- [tad-hq/universal-session-viewer](https://github.com/tad-hq/universal-session-viewer) - FTS5 full-text search design
- [jazzyalex/agent-sessions](https://github.com/jazzyalex/agent-sessions) - SQLite indexing patterns
- [Arbuzov/copilot-chat-history](https://github.com/Arbuzov/copilot-chat-history) - VS Code Copilot chat session data format
