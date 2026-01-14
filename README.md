# Copilot Repository Tools

Create a searchable archive of your VS Code and GitHub Copilot CLI chat history, with a web viewer similar to [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts).

This project was informed by and borrows patterns from several excellent open-source projects:

| Project | What We Borrowed |
|---------|------------------|
| [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) | HTML transcript generation, pagination approach, CLI structure |
| [Arbuzov/copilot-chat-history](https://github.com/Arbuzov/copilot-chat-history) | VS Code Copilot chat session data format, workspace organization |
| [jazzyalex/agent-sessions](https://github.com/jazzyalex/agent-sessions) | Multi-agent session concept, SQLite indexing patterns |
| [tad-hq/universal-session-viewer](https://github.com/tad-hq/universal-session-viewer) | FTS5 full-text search design, session metadata schema |

## Features

- **Scan** VS Code workspace storage to find Copilot chat sessions (format based on [Arbuzov/copilot-chat-history](https://github.com/Arbuzov/copilot-chat-history))
- **Support** for both VS Code Stable and Insiders editions
- **GitHub Copilot CLI** chat history support (JSONL format from `~/.copilot/session-state`)
- **Store** chat history in a SQLite database with FTS5 full-text search (inspired by [tad-hq/universal-session-viewer](https://github.com/tad-hq/universal-session-viewer))
- **Browse** your archive with a web interface (similar to [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts))
- **Export/Import** sessions as JSON or Markdown for backup or migration
- **Tool invocations & file changes** tracking from chat sessions

## Project Structure

This is a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/) with three packages:

| Package | Description |
|---------|-------------|
| `copilot-repository-tools-common` | Core utilities: database, scanner, markdown exporter |
| `copilot-repository-tools-cli` | Command-line interface built with [Typer](https://typer.tiangolo.com/) |
| `copilot-repository-tools-web` | Flask-based web interface for browsing chat sessions |

## Quick Start

Run the CLI directly with [uvx](https://docs.astral.sh/uv/guides/tools/) (no installation needed):

```bash
# Scan for chat sessions
uvx copilot-repository-tools-cli scan

# Start the web viewer
uvx copilot-repository-tools-web --db copilot_chats.db

# Search through chat history
uvx copilot-repository-tools-cli search "authentication"
```

## Installation

### Using uv (recommended)

```bash
# Install the CLI
uv tool install copilot-repository-tools-cli

# Install the web interface
uv tool install copilot-repository-tools-web
```

### Using pip

```bash
# Install the CLI
pip install copilot-repository-tools-cli

# Install the web interface
pip install copilot-repository-tools-web
```

### From source (development)

```bash
git clone https://github.com/Arithmomaniac/copilot-repository-tools.git
cd copilot-repository-tools

# Install uv if you haven't already
pip install uv

# Sync the workspace (installs all packages in development mode)
uv sync
```

## Usage

### 1. Scan for Chat Sessions

Scan your VS Code workspace storage and GitHub Copilot CLI sessions to import into the database:

```bash
# Scan both VS Code (Stable and Insiders) and CLI sessions
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

# Force re-import of all sessions
copilot-chat-archive scan --full
```

**Incremental Updates**: By default, the `scan` command only adds new sessions and updates changed ones based on file modification time. Use `--full` to re-import all sessions.

**CLI Support**: The scanner automatically detects and imports GitHub Copilot CLI chat sessions from `~/.copilot/session-state/` by default.

### 2. Start the Web Server

Browse your chat archive in a web interface:

```bash
# Start the web server (uses copilot_chats.db by default)
copilot-chat-web

# Custom options
copilot-chat-web --db my_chats.db --port 8080 --title "My Copilot Chats"
```

Then open `http://127.0.0.1:5000/` in your browser.

### 3. Search Chats

Search through your chat history from the command line:

```bash
# Basic search
copilot-chat-archive search "authentication"

# Limit results
copilot-chat-archive search "React hooks" --limit 50

# Filter by role
copilot-chat-archive search "error" --role assistant

# Search only tool invocations
copilot-chat-archive search "git" --tools-only

# Show full content (not truncated)
copilot-chat-archive search "complex query" --full
```

### 4. View Statistics

```bash
copilot-chat-archive stats
```

### 5. Export/Import

```bash
# Export all sessions to JSON
copilot-chat-archive export --output chats.json

# Export to stdout
copilot-chat-archive export

# Export as Markdown files
copilot-chat-archive export-markdown --output-dir ./markdown-archive

# Export a single session
copilot-chat-archive export-markdown --session-id abc123 --output-dir ./session

# Include file diffs in markdown
copilot-chat-archive export-markdown --include-diffs

# Import from JSON
copilot-chat-archive import-json chats.json
```

## Chat Storage Locations

### VS Code

VS Code stores Copilot chat history in workspace-specific storage:

| OS | Path |
|----|------|
| Windows | `%APPDATA%\Code\User\workspaceStorage\{hash}\` |
| macOS | `~/Library/Application Support/Code/User/workspaceStorage/{hash}/` |
| Linux | `~/.config/Code/User/workspaceStorage/{hash}/` |

For VS Code Insiders, replace `Code` with `Code - Insiders`.

### GitHub Copilot CLI

The GitHub Copilot CLI stores chat history in JSONL format:

| OS | Path |
|----|------|
| All | `~/.copilot/session-state/` (current format, v0.0.342+) |
| All | `~/.copilot/history-session-state/` (legacy format) |

The scanner automatically detects and imports both VS Code and CLI sessions by default.

## Database Schema

The SQLite database uses a two-layer design:

1. **`raw_sessions` table** - Stores compressed raw JSON as the source of truth
2. **Derived tables** - Can be dropped and recreated from raw_sessions without migrations

```sql
-- Raw sessions table (source of truth)
CREATE TABLE raw_sessions (
    id INTEGER PRIMARY KEY,
    session_id TEXT UNIQUE NOT NULL,
    raw_json_compressed BLOB NOT NULL,  -- zlib-compressed original JSON
    workspace_name TEXT,
    workspace_path TEXT,
    source_file TEXT,
    vscode_edition TEXT,
    source_file_mtime REAL,
    source_file_size INTEGER,
    imported_at TIMESTAMP
);

-- Derived sessions table
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    session_id TEXT UNIQUE NOT NULL,
    workspace_name TEXT,
    workspace_path TEXT,
    created_at TEXT,
    updated_at TEXT,
    source_file TEXT,
    vscode_edition TEXT,
    custom_title TEXT,
    imported_at TIMESTAMP,
    type TEXT DEFAULT 'vscode'  -- 'vscode' or 'cli'
);

-- Messages table
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT,
    cached_markdown TEXT
);

-- Full-text search virtual table
CREATE VIRTUAL TABLE messages_fts USING fts5(content);

-- Tool invocations, file changes, and command runs are also tracked
```

### Rebuilding Derived Tables

When the schema changes, you can rebuild all derived tables from the stored raw JSON:

```bash
copilot-chat-archive rebuild --db copilot_chats.db
```

This drops and recreates the sessions, messages, and related tables without needing to re-scan the original VS Code storage.

## Web Viewer Features

The web interface includes:

- **Session list** with workspace names and message counts, sorted by most recent message
- **Workspace filtering** to focus on specific projects
- **Full-text search** with highlighting
- **Dark mode support** via CSS `prefers-color-scheme`
- **Responsive design** for mobile and desktop
- **Syntax highlighting** for code blocks
- **Incremental refresh** to update without restarting

## Development

```bash
# Clone the repository
git clone https://github.com/Arithmomaniac/copilot-repository-tools.git
cd copilot-repository-tools

# Install uv
pip install uv

# Sync the workspace (installs all packages in development mode)
uv sync

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov

# Run a specific package's CLI
uv run copilot-chat-archive --help
uv run copilot-chat-web --help
```

## Related Projects

- [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) - Inspiration for the web viewer
- [Arbuzov/copilot-chat-history](https://github.com/Arbuzov/copilot-chat-history) - VS Code extension for viewing chat history
- [microsoft/vscode-copilot-chat](https://github.com/microsoft/vscode-copilot-chat) - Official VS Code Copilot Chat extension

## License

MIT License - see [LICENSE](LICENSE) for details.
