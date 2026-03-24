# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.4] - 2026-03-24

### Changed

- **Architecture**: Split `database.py` monolith (2433L) into four focused modules aligned with pipeline stages:
  - `db_search.py` (767L) — FTS5 search, query parsing, result merging
  - `db_retrieval.py` (700L) — session reconstruction, listing, analytics, builtin accessors
  - `db_storage.py` (760L) — schema, ingestion, enrichment, discovery, cleanup
  - `database.py` (515L) — thin facade preserving full backward compatibility
- **Architecture**: Added `db_schema.py` — centralized column-name constants and dataclass↔SQL mapping helpers (`session_to_row`, `row_to_tool`, etc.) closing the schema gap between `models.py` and SQL
- **Architecture**: Decomposed `refresh.py` — extracted `_parse_files_parallel()`, `_classify_and_batch_write()`, `_enrich_session_batch()` from duplicated logic in `run_refresh()` and `run_enrichment()`

## [0.6.3] - 2026-03-23

### Changed

- **Skill**: Rewritten `search-copilot-chats` skill as hybrid read-help pattern
- **Docs**: Refreshed all README screenshots to current UI (PII-free, workspace-filtered)
- **Docs**: Added cleanup screenshot showing Cleanup/Revert All toolbar
- **Docs**: Backfilled CHANGELOG entries for v0.1.1 through v0.6.1

## [0.6.2] - 2026-03-15

### Added

- **Scanner**: Handle `session.title_changed` events — renders status block and sets session display title (highest priority over workspace.yaml and intent blocks)
- **Scanner**: Handle `assistant.usage` events — renders per-API-call token stats (model, input/output tokens, cost, duration, reasoning effort)
- **Scanner**: Extract `intentionSummary` and `toolTitle` from `assistant.message` tool requests for richer tool invocation display
- **Scanner**: Extract `hostType`, `headCommit`, `baseCommit` from `session.start` context
- **Scanner**: ADO sessions (`hostType=ado`) no longer receive incorrect GitHub repository URLs

### Changed

- **Scanner**: Expanded skip list with 22 new event types from CLI v1.0.5 schema (hooks, streaming deltas, plan mode lifecycle, agent selection, internal state tracking)

### Fixed

- **Scanner**: Guard against null token fields in `assistant.usage` events (prevents `TypeError` on nullable `Double` values from SDK)
- **Scanner**: `toolTitle` now correctly used for shell command descriptions (was previously ignored in `cmd_run` branch)

## [0.6.1] - 2026-03-12

### Fixed

- **Scanner**: Extract `reasoningText` from `assistant.message` events as thinking blocks (newer CLI versions embed thinking directly instead of separate `assistant.reasoning` events)

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

## [0.5.0] - 2026-03-08

### Added

- **Web**: Content-type filter pills on index page ('Search in:' pills for messages, tools, files, etc.)
- **Web**: Sticky unified toolbar with title, gear icon, and export buttons
- **Web**: Per-message sticky headers with role labels
- **Web**: Workspace/repository sorting by count, searchable repository dropdown
- **CLI**: Unified `--include`/`--exclude` flags on search command with 8 content types (replaces `--tools-only`, `--files-only`)

### Changed

- **Web**: Denser UI — 14px base font, compact headers, unified card padding
- **Web**: Scroll position preservation (CSS `overflow-anchor` + JS fallback)
- **Web**: Full rebuild parallelized with `ThreadPoolExecutor`

### Fixed

- **Database**: `enrichment_version` now correctly stamped for VS Code sessions
- **Scanner**: CLI sessions with missing `events.jsonl` no longer stuck in refresh loop
- **Web**: Zero-message sessions hidden from index page and banner
- **Web**: Tool/file search results now link to correct message anchors

## [0.4.4] - 2026-03-07

### Changed

- **Core**: Extract shared utility module (`utils.py`) — eliminates ~500 lines of duplicated code across exporters and webapp
- **Scanner**: Shared `scanner/shared.py` with `normalize_tool_status`, `extract_command_run`, `normalize_invocation_message`
- **Scanner**: Bump `PARSER_VERSION` to 2 (triggers re-parse of existing sessions)

### Fixed

- **Scanner**: Normalize VS Code tool status (`completed` → `success`, detect errors)
- **Scanner**: Extract `CommandRun` for terminal/shell tools in VS Code subagents
- **Scanner**: Populate block description with `toolId` fallback
- **Scanner**: Normalize invocation messages for built-in tools
- **Scanner**: Guard against null `modelMetrics` in `session.shutdown` handler
- **Scanner**: `ask_user` falls back to `message` field when `question` absent

## [0.4.3] - 2026-03-06

### Added

- **Scanner**: Handle `session.task_complete` events (renders task summary as collapsible block with markdown)
- **Scanner**: Handle `session.shutdown` events (renders session stats: shutdown type, code changes, model metrics)
- **Scanner**: Pretty-format handlers for 13 new CLI tools (`show_file`, `ask_user`, `skill`, `lsp`, `propose_work`, `list_agents`, `read_agent`, `exit_plan_mode`, `fetch_copilot_cli_documentation`, etc.)
- **CLI**: `migrate` subcommand for explicit database schema migration

### Changed

- **Scanner**: Added 10 new ephemeral event types to skip list (permission, elicitation, user_input, external_tool from CLI v0.0.422)

## [0.4.2] - 2026-03-06

### Added

- **CLI**: `--include-agent-details`/`--no-agent-details` flag for markdown export

### Fixed

- **Web**: Structured agent rendering with recursive nested content — internal tools as compact text, MCP tools as collapsible details, agent results in styled quote box
- **Scanner**: Skills render as expandable dropdowns with description

## [0.4.1] - 2026-02-28

### Added

- **Database**: `enrichment_version` column on `cst_sessions` (schema v3) for tracking which parser version enriched each session
- **Web**: Version-refresh banner on session page when enriched with older version
- **Web**: Global version-refresh banner on index page with count of stale sessions
- **Web**: PyPI upgrade check — dismissible banner when newer version available
- **CLI**: PyPI upgrade notice after `scan` and `web` commands (24-hour cached)
- **Web**: App version displayed in header

### Changed

- **Web**: Default title renamed from "Copilot Chat Archive" to "Copilot Session Tools"
- **Web**: Browser tab title uses session display name instead of workspace/session ID

## [0.4.0] - 2026-02-27

### Added

- **Scanner**: Sub-agent/background agent content rendered as collapsible `<details>` blocks with status pills (completed/failed)
- **Scanner**: CLI scanner tracks `subagent.started`/`completed` brackets via stack, tags messages with `agent_id` and `agent_display_name`
- **Scanner**: VS Code scanner detects `toolSpecificData.kind=='subagent'` and groups child tools under parent
- **Web**: Collapsible agent blocks with indigo left border and dark mode support
- **Markdown**: Agent messages rendered as blockquotes with group headers
- **Database**: Schema v2 — `agent_id`, `agent_display_name`, `agent_nesting_level` on `cst_messages`; `subagent_invocation_id` on `cst_tool_invocations`

## [0.3.1] - 2026-02-25

### Changed

- **Core**: Unified CLI and web refresh into shared `refresh.py` module — both now use identical code paths for scan, enrichment, and VS Code import

### Fixed

- **Scanner**: `parse_session_file()` now stamps scan-time mtime/size (not parse-time), preventing unnecessary re-imports
- **CLI**: Verbose output restored via `ProgressCallback`

## [0.3.0] - 2026-02-24

### Added

- **Database**: Two-tier rendering — sessions visible immediately from Copilot CLI's built-in `session-store.db`, enriched with `cst_*` tables on scan
- **CLI**: `enrich` command for single-session enrichment
- **Web**: Enrichment badge (green ✓ Enriched / gray Basic) on session list
- **Web**: "Scan Now" button on unenriched sessions for inline enrichment
- **Web**: Flash message rendering for enrich errors

### Changed

- **Database**: Default DB path changed to `~/.copilot/session-store.db` (Copilot CLI's own database)
- **Database**: All tables renamed to `cst_*` prefix — built-in tables are never modified
- **Database**: WAL mode + `busy_timeout=5000` for concurrent access
- **Scanner**: Added `PARSER_VERSION` tracking and `source_format` field

### Removed

- **CLI**: `rebuild`, `raw-json` commands (superseded by session store integration)
- **Database**: Raw compressed JSON blob storage (`raw_sessions` table)

## [0.2.0] - 2026-02-22

### Added

- **Scanner**: Handle 8 new CLI event types — `subagent.started`/`completed`/`failed`, `session.handoff`/`warning`/`mode_changed`/`context_changed`/`plan_changed`
- **Testing**: Snapshot test infrastructure with `pytest-regressions` golden-file baselines
- **Testing**: Real session fixtures (CLI, VS Code Insiders, VS Code Stable) replacing synthetic test data

### Changed

- **Scanner**: Added 5 internal event types to skip list

## [0.1.3] - 2026-02-14

### Fixed

- **CI/CD**: Auto-tag release trigger — `GITHUB_TOKEN` tags don't trigger workflows; now uses `gh workflow run`

## [0.1.2] - 2026-02-14

### Added

- **CI/CD**: Auto-tag on version bump, pre-push lint hook

## [0.1.1] - 2026-02-13

### Added

- **CI/CD**: PyPI publishing infrastructure

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
