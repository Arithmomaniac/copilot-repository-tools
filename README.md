# Copilot Chat Archive

Create a searchable archive of your VS Code GitHub Copilot chat history, with a web viewer similar to [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts).

## Features

- **Scan** VS Code workspace storage to find Copilot chat sessions
- **Support** for both VS Code Stable and Insiders editions
- **Store** chat history in a SQLite database with full-text search
- **Generate** static HTML files for browsing and searching your archive
- **Export/Import** sessions as JSON for backup or migration

## Installation

```bash
pip install copilot-chat-archive
```

Or install from source:

```bash
git clone https://github.com/Arithmomaniac/copilot-repository-tools.git
cd copilot-repository-tools
pip install -e .
```

## Usage

### 1. Scan for Chat Sessions

Scan your VS Code workspace storage to import chat sessions into the database:

```bash
# Scan both VS Code Stable and Insiders
copilot-chat-archive scan

# Scan only VS Code Stable
copilot-chat-archive scan --edition stable

# Scan only VS Code Insiders
copilot-chat-archive scan --edition insider

# Use a custom database path
copilot-chat-archive scan --db my_chats.db

# Scan custom storage paths
copilot-chat-archive scan --storage-path /path/to/workspaceStorage

# Verbose output
copilot-chat-archive scan --verbose
```

### 2. Generate HTML Archive

Generate static HTML files to browse your chat archive:

```bash
# Generate to ./archive directory
copilot-chat-archive generate

# Custom output directory
copilot-chat-archive generate --output ./my-archive

# Custom title
copilot-chat-archive generate --title "My Copilot Chats"
```

Then open `./archive/index.html` in your browser.

### 3. Search Chats

Search through your chat history from the command line:

```bash
copilot-chat-archive search "authentication"
copilot-chat-archive search "React hooks" --limit 50
```

### 4. View Statistics

```bash
copilot-chat-archive stats
```

### 5. Export/Import JSON

```bash
# Export all sessions to JSON
copilot-chat-archive export --output chats.json

# Export to stdout
copilot-chat-archive export

# Import from JSON
copilot-chat-archive import-json chats.json
```

## Chat Storage Locations

VS Code stores Copilot chat history in workspace-specific storage:

| OS | Path |
|----|------|
| Windows | `%APPDATA%\Code\User\workspaceStorage\{hash}\` |
| macOS | `~/Library/Application Support/Code/User/workspaceStorage/{hash}/` |
| Linux | `~/.config/Code/User/workspaceStorage/{hash}/` |

For VS Code Insiders, replace `Code` with `Code - Insiders`.

## Database Schema

The SQLite database uses the following schema:

```sql
-- Sessions table
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    session_id TEXT UNIQUE NOT NULL,
    workspace_name TEXT,
    workspace_path TEXT,
    created_at TEXT,
    updated_at TEXT,
    source_file TEXT,
    vscode_edition TEXT,
    imported_at TIMESTAMP
);

-- Messages table with full-text search
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT
);

-- Full-text search virtual table
CREATE VIRTUAL TABLE messages_fts USING fts5(content);
```

## Web Viewer Features

The generated HTML archive includes:

- **Session list** with workspace names and message counts
- **Workspace filtering** to focus on specific projects
- **Client-side search** to filter sessions
- **Dark mode support** via CSS `prefers-color-scheme`
- **Responsive design** for mobile and desktop
- **Syntax highlighting** for code blocks

## Development

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=copilot_chat_archive
```

## Related Projects

- [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) - Inspiration for the web viewer
- [Arbuzov/copilot-chat-history](https://github.com/Arbuzov/copilot-chat-history) - VS Code extension for viewing chat history
- [microsoft/vscode-copilot-chat](https://github.com/microsoft/vscode-copilot-chat) - Official VS Code Copilot Chat extension

## License

MIT License - see [LICENSE](LICENSE) for details.
