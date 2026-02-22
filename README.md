# Copilot Session Tools

[![CI](https://github.com/Arithmomaniac/copilot-session-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/Arithmomaniac/copilot-session-tools/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/copilot-session-tools)](https://pypi.org/project/copilot-session-tools/) [![Python](https://img.shields.io/pypi/pyversions/copilot-session-tools)](https://pypi.org/project/copilot-session-tools/) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Extend the GitHub Copilot CLI's built-in session store with searchable, enriched chat history — plus VS Code session support and a web viewer similar to [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts).

This project was informed by and borrows patterns from several excellent open-source projects:

| Project | What We Borrowed |
|---------|------------------|
| [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) | HTML transcript generation, pagination approach, CLI structure |
| [Arbuzov/copilot-chat-history](https://github.com/Arbuzov/copilot-chat-history) | VS Code Copilot chat session data format, workspace organization |
| [jazzyalex/agent-sessions](https://github.com/jazzyalex/agent-sessions) | Multi-agent session concept, SQLite indexing patterns |
| [tad-hq/universal-session-viewer](https://github.com/tad-hq/universal-session-viewer) | FTS5 full-text search design, session metadata schema |

## How It Works

The default database is **`~/.copilot/session-store.db`** — the Copilot CLI's own built-in session store. This tool extends it by adding `cst_*` tables for enriched data, without modifying the CLI's built-in tables.

**Two-tier architecture:**

1. **Built-in data (immediate):** CLI sessions are visible immediately from the Copilot CLI's session store — basic session metadata, turns, and messages.
2. **Enriched data (after `scan`):** Running `scan` parses the raw session files and adds full detail to the `cst_*` tables — tool invocations, file changes, thinking blocks, command runs, and more.

**VS Code sessions** are only available after scanning (they are not part of the Copilot CLI's built-in session store).

## Features

- **Extend** the Copilot CLI's built-in session store with enriched metadata
- **Scan** VS Code workspace storage to find Copilot chat sessions (format based on [Arbuzov/copilot-chat-history](https://github.com/Arbuzov/copilot-chat-history))
- **Support** for both VS Code Stable and Insiders editions
- **GitHub Copilot CLI** chat history support (JSONL format from `~/.copilot/session-state`)
- **Store** enriched data in `cst_*` tables with FTS5 full-text search (inspired by [tad-hq/universal-session-viewer](https://github.com/tad-hq/universal-session-viewer))
- **Browse** your archive with a web interface (similar to [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts))
- **Export/Import** sessions as JSON, Markdown, or self-contained HTML for backup or migration
- **Tool invocations & file changes** tracking from chat sessions

## Project Structure

This is a Python package with optional extras for CLI and web interfaces:

- **Base package**: Core utilities (database, scanner, markdown exporter)
- **[cli] extra**: Command-line interface built with Typer
- **[web] extra**: Flask-based web interface for browsing chat sessions
- **[all] extra**: Both CLI and web interfaces

## Installation

```bash
# Install with CLI
pip install copilot-session-tools[cli]

# Install with web interface
pip install copilot-session-tools[web]

# Install everything (CLI + web)
pip install copilot-session-tools[all]
```

> **Tip:** Also works with `pipx install` or `uvx --with` if you prefer isolated tool environments.

### From source (development)

```bash
git clone https://github.com/Arithmomaniac/copilot-session-tools.git
cd copilot-session-tools

# Install uv if you haven't already
pip install uv

# Install with all dependencies
uv sync --all-extras
```

## Usage

### Default Database

The tool uses `~/.copilot/session-store.db` by default — the Copilot CLI's built-in database. The Copilot CLI must be installed and used at least once before this tool can work. Override with `--db`:

```bash
copilot-session-tools --db custom.db scan
```

### Global Flags

```bash
# Disable cst_* table reads; show only built-in session store data
copilot-session-tools --unenriched-only search "auth"
```

### 1. Scan and Enrich Sessions

Scan VS Code workspace storage and enrich CLI sessions with full detail (tool invocations, file changes, thinking blocks, etc.):

```bash
# Scan both VS Code (Stable and Insiders) and enrich CLI sessions
copilot-session-tools scan

# Scan only VS Code Stable
copilot-session-tools scan --edition stable

# Scan only VS Code Insiders
copilot-session-tools scan --edition insider

# Scan custom storage paths
copilot-session-tools scan --storage-path /path/to/workspaceStorage

# Verbose output
copilot-session-tools scan --verbose

# Force re-import of all sessions
copilot-session-tools scan --full
```

**Incremental Updates**: By default, the `scan` command only adds new sessions and updates changed ones based on file modification time. Use `--full` to re-import all sessions.

**CLI Sessions**: CLI sessions are already visible from the built-in store without scanning. Running `scan` enriches them with parsed detail (tool invocations, file changes, etc.).

**VS Code Sessions**: Only available after scanning — they are not part of the Copilot CLI's built-in session store.

### 2. Start the Web Server

Browse your chat archive in a web interface:

```bash
# Start the web server (uses ~/.copilot/session-store.db by default)
copilot-session-tools web

# Custom options
copilot-session-tools web --db my_chats.db --port 8080 --title "My Copilot Chats"
```

Then open `http://127.0.0.1:5000/` in your browser.

### 3. Search Chats

Search through your chat history from the command line:

```bash
# Basic search
copilot-session-tools search "authentication"

# Limit results
copilot-session-tools search "React hooks" --limit 50

# Filter by role
copilot-session-tools search "error" --role assistant

# Search only tool invocations
copilot-session-tools search "git" --tools-only

# Show full content (not truncated)
copilot-session-tools search "complex query" --full
```

**Advanced Search Syntax:**

The search supports powerful query syntax:

- **Multiple words:** `python function` matches messages containing both words (AND logic)
- **Exact phrases:** `"python function"` matches the exact phrase
- **Field filters:** Filter by specific fields directly in the query:
  - `role:user` - Filter to user messages only
  - `role:assistant` - Filter to assistant messages only
  - `workspace:my-project` - Filter to a specific workspace
  - `title:session-name` - Filter by session title

```bash
# Search for "function" only in user messages
copilot-session-tools search "role:user function"

# Search in a specific workspace
copilot-session-tools search "workspace:my-project python"

# Combine filters
copilot-session-tools search "workspace:react role:assistant hooks"

# Sort by date instead of relevance
copilot-session-tools search "python" --sort date
```

### 4. View Statistics

```bash
copilot-session-tools stats
```

### 5. Export/Import

```bash
# Export all sessions to JSON
copilot-session-tools export --output chats.json

# Export to stdout
copilot-session-tools export

# Export as Markdown files
copilot-session-tools export-markdown --output-dir ./markdown-archive

# Export a single session
copilot-session-tools export-markdown --session-id abc123 --output-dir ./session

# Include file diffs in markdown
copilot-session-tools export-markdown --include-diffs

# Export as self-contained HTML (same rendering as web viewer, no server needed)
copilot-session-tools export-html --output-dir ./html-archive

# Export a single session as HTML
copilot-session-tools export-html --session-id abc123 --output-dir ./session

# Import from JSON
copilot-session-tools import-json chats.json
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

The tool extends the Copilot CLI's built-in session store (`~/.copilot/session-store.db`) with `cst_*` enrichment tables. The built-in tables are **never modified** by this tool.

**Built-in tables** (managed by Copilot CLI):
- `sessions`, `turns`, `checkpoints`, `session_files`, `session_refs`, `search_index` — basic session data, immediately available

**Enrichment tables** (managed by this tool, prefixed `cst_`):
- `cst_raw_sessions` — Compressed raw JSON from session files (source of truth for enrichment)
- `cst_sessions`, `cst_messages` — Enriched session and message data with parsed detail
- `cst_messages_fts` — FTS5 full-text search index
- `cst_tool_invocations`, `cst_file_changes`, `cst_command_runs` — Extracted structured data

### Recovery

If the `cst_*` tables get corrupted, you can safely delete them and re-scan:

```bash
# Delete all cst_* tables (built-in data is unaffected)
sqlite3 ~/.copilot/session-store.db "SELECT 'DROP TABLE ' || name || ';' FROM sqlite_master WHERE name LIKE 'cst_%';" | sqlite3 ~/.copilot/session-store.db

# Re-scan to rebuild enrichment data
copilot-session-tools scan --full
```

The built-in session data from the Copilot CLI is never modified and remains intact.

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
git clone https://github.com/Arithmomaniac/copilot-session-tools.git
cd copilot-session-tools

# Install uv
pip install uv

# Sync the workspace (installs all packages in development mode)
uv sync

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov

# Run the CLI
uv run copilot-session-tools --help
```

## Agent Skills

This repository includes [Agent Skills](https://claude-plugins.dev) for AI coding agents (Claude Code, Cursor, VS Code, Codex, and more):

| Skill | Description |
|-------|-------------|
| **search-copilot-chats** | Search, browse, and export archived Copilot chat sessions using this tool's CLI |
| **scanner-refresh** | Research recent changes in Copilot repos and update the scanner for new event types |

### Install a skill

```bash
# Install the search skill (Claude Code is the default client)
npx skills-installer install @Arithmomaniac/copilot-session-tools/search-copilot-chats

# For other clients
npx skills-installer install @Arithmomaniac/copilot-session-tools/search-copilot-chats --client cursor
```

Skills are automatically available when working in this repository (project-level `.claude/skills/`).

## Related Projects

- [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) - Inspiration for the web viewer
- [Arbuzov/copilot-chat-history](https://github.com/Arbuzov/copilot-chat-history) - VS Code extension for viewing chat history
- [microsoft/vscode-copilot-chat](https://github.com/microsoft/vscode-copilot-chat) - Official VS Code Copilot Chat extension

## License

MIT License - see [LICENSE](LICENSE) for details.
