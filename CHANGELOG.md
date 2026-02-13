# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-02-13

### Added

- **Scanner**: Scan VS Code workspace storage (Stable and Insiders editions) to find Copilot chat sessions
- **Scanner**: GitHub Copilot CLI chat history support (JSONL format from `~/.copilot/session-state`)
- **Scanner**: Support for VS Code JSONL append-log format (VS Code >=1.109)
- **Database**: SQLite storage with FTS5 full-text search indexing
- **Database**: Two-layer design with raw compressed JSON as source of truth and derived tables
- **Database**: Incremental scan support (only imports new/changed sessions)
- **CLI**: `scan` command to import sessions from VS Code and CLI
- **CLI**: `search` command with advanced query syntax (field filters, exact phrases, boolean logic)
- **CLI**: `stats` command for database statistics
- **CLI**: `export` command for JSON export
- **CLI**: `export-markdown` command for Markdown export
- **CLI**: `export-html` command for self-contained HTML export
- **CLI**: `import-json` command for JSON import
- **CLI**: `rebuild` command to recreate derived tables from raw JSON
- **Web**: Flask-based web interface for browsing chat sessions
- **Web**: Full-text search with highlighting
- **Web**: Dark mode support via CSS `prefers-color-scheme`
- **Web**: Syntax highlighting for code blocks
- **Web**: Incremental refresh without restarting
- **Tracking**: Tool invocations, file changes, and command runs from chat sessions
