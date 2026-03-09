# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] - 2026-03-09

### Added

- **Transcript Cleanup**: LLM-powered cleanup of voice-dictated user messages via LiteLLM (GitHub Copilot API)
  - Pure-Python heuristic pre-filter auto-detects garbled messages (repeated words, filler, missing punctuation)
  - Batch processing: all messages sent in a single LLM call per chunk (10 msgs/chunk) with assistant context
  - Original content preserved for revert — cleanup is fully reversible
  - Structured output via `response_format` JSON schema (OpenAI models) with prompt-based fallback
- **CLI**: `cleanup` command with `--dry-run`, `--all`, `--message`, `--force`, `--threshold` options
- **CLI**: `cleanup-revert` command to restore original voice-dictated content
- **Web**: Per-message Font Awesome mic icon with tristate color (gray/green/amber)
- **Web**: Popover with Clean/Toggle/Revert actions, no page reload (AJAX + scroll preservation)
- **Web**: Session-level Cleanup and Revert All toolbar buttons
- **Web**: POST API routes `/api/cleanup/<session_id>` and `/api/cleanup-revert/<session_id>`
- **Database**: Schema v5 migration — `original_content` and `cleanup_model` columns on `cst_messages`
- **Dependencies**: Optional `[llm]` extras group with `litellm>=1.50.0`
- **Benchmark**: `scripts/benchmark_cleanup.py` for comparing pre-filter strategies and LLM models

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
