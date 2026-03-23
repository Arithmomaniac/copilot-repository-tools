---
name: search-copilot-chats
description: Search across archived Copilot chat sessions (VS Code + CLI) using the copilot-session-tools CLI. Use when the user says "search my chats", "find in chat history", "what did we discuss about X", "look up past sessions", "scan chats", or references a session-state path or session GUID. Also covers exporting sessions as markdown or HTML and launching the web viewer.
---

# Search Copilot Chats

Search, browse, and export archived GitHub Copilot chat sessions from VS Code (Stable & Insiders) and the Copilot CLI. Uses the **copilot-session-tools** CLI backed by a local SQLite database with FTS5 full-text search.

## Command discovery

**Do not memorize flags.** Always run `--help` to discover available commands and options:

```powershell
copilot-session-tools --help                    # list all commands
copilot-session-tools <command> --help          # flags for a specific command
```

The CLI is self-documenting. If a command or flag doesn't appear in `--help`, it doesn't exist. This skill only documents **domain knowledge that `--help` cannot teach** — search strategy, gotchas, database schema, and workflows.

## When to use

- User asks to search past Copilot conversations ("what did we discuss about X?")
- User pastes a `session-state` path or session GUID and wants context from it
- User pastes a web viewer URL like `http://127.0.0.1:5000/session/{uuid}`
- User wants to find prior decisions, code patterns, or troubleshooting steps from past sessions
- User asks to export a session as markdown, HTML, or JSON
- User wants to refresh the chat archive (scan for new sessions)
- User wants to clean up voice-dictated messages in a session

## Prerequisites

The CLI must be installed and on PATH. It uses `~/.copilot/session-store.db` (requires Copilot CLI v0.0.412+).

If not found, install with: `uv tool install copilot-session-tools[all]` (or `pipx` / `pip`). The `[all]` extra includes web viewer dependencies.

## Favor CLI over Python

**Always prefer CLI execution** over writing Python scripts. The CLI handles all common workflows. Only drop into Python/SQL when you need a custom join, aggregation, or programmatic processing the CLI doesn't support.

## Search strategy (critical — not in `--help`)

The FTS5 search uses **AND semantics**: every keyword in the query MUST appear in the **same message**. More keywords = stricter matching = fewer results. This is the #1 cause of empty search results.

**Start broad, narrow later.** Always begin with 1–2 core keywords. Only add more if you get too many results.

**Iterative approach.** If no results are returned, **remove** keywords (don't add more). Try synonyms. Drop field filters. The goal is to widen the net, not narrow it further.

**Field filters don't reduce keyword strictness.** A filter like `workspace:ZTS` narrows the *session pool*, but all FTS keywords still must match within a single message.

**Keyword budget.** Aim for **2–3 keywords max**. Use exact phrases for multi-word concepts (e.g., `"resource graph"` counts as 1 match unit).

### Search dos and don'ts

**❌ DON'T — these all returned zero results in real usage:**

| Failed query | Problem |
|---|---|
| `customer tenant query build SPN` | 5 loose keywords — ALL must appear in one message |
| `workspace:ZTS "resource graph" integration test permission` | Workspace filter + exact phrase + 3 keywords = too strict |
| `"build service" "resource graph" reader` | Two exact phrases + a keyword — very unlikely all in one message |
| `spec driven proposal specs design tasks` | 6 keywords, too many AND conditions |

**✅ DO — these succeeded:**

| Successful query | Why it works |
|---|---|
| `ARG build pipeline` | 3 focused keywords |
| `"resource graph" auth` | 1 exact phrase + 1 keyword |
| `workspace:ZTS ARG RBAC` | Workspace filter + only 2 keywords |
| `docker build` | 2 keywords, broad |

**Iterative search workflow:**

```
Step 1: copilot-session-tools search "resource graph" --full --limit 20
  → Too many results? Add ONE keyword:
Step 2: copilot-session-tools search "resource graph" auth --full --limit 20
  → Still too many? Add a workspace filter:
Step 3: copilot-session-tools search 'workspace:ZTS "resource graph" auth' --full --limit 10
  → No results? Back up and try different keyword:
Step 4: copilot-session-tools search 'workspace:ZTS "resource graph" permission' --full --limit 10
```

## Extract session references from user input

Users reference past sessions in several ways. Recognize and extract the session GUID:

| User provides | Extract |
|---|---|
| `C:\Users\...\session-state\{uuid}` or `~\.copilot\session-state\{uuid}` | The `{uuid}` portion |
| `http://127.0.0.1:5000/session/{uuid}` | The `{uuid}` portion |
| A bare GUID like `67894303-8571-...` | Use directly as session ID |
| A short prefix like `67894303` | Search: `copilot-session-tools search "67894303"` |

Then use `export-markdown --session-id` to retrieve the full session content.

## Direct SQL for advanced queries

When the CLI search doesn't support your query shape, use SQLite directly on `~/.copilot/session-store.db`:

```powershell
# Find sessions by workspace name
sqlite3 "$HOME/.copilot/session-store.db" "SELECT session_id, workspace_name, created_at FROM cst_sessions WHERE workspace_name LIKE '%zts%' ORDER BY created_at DESC LIMIT 20"

# Count messages per session (find long conversations)
sqlite3 "$HOME/.copilot/session-store.db" "SELECT s.session_id, s.workspace_name, COUNT(m.id) as msg_count FROM cst_sessions s JOIN cst_messages m ON s.session_id = m.session_id GROUP BY s.session_id ORDER BY msg_count DESC LIMIT 20"

# Find sessions with tool invocations of a specific tool
sqlite3 "$HOME/.copilot/session-store.db" "SELECT DISTINCT s.session_id, s.workspace_name, s.created_at FROM cst_sessions s JOIN cst_messages m ON s.session_id = m.session_id JOIN cst_tool_invocations ti ON m.id = ti.message_id WHERE ti.name LIKE '%build%' ORDER BY s.created_at DESC LIMIT 20"

# Find file changes by path pattern
sqlite3 "$HOME/.copilot/session-store.db" "SELECT DISTINCT s.session_id, s.workspace_name, fc.path FROM cst_sessions s JOIN cst_messages m ON s.session_id = m.session_id JOIN cst_file_changes fc ON m.id = fc.message_id WHERE fc.path LIKE '%Dockerfile%' ORDER BY s.created_at DESC LIMIT 20"
```

## Database schema (quick reference)

The tool extends `~/.copilot/session-store.db` with `cst_*` enrichment tables. Built-in tables are never modified.

**Built-in tables** (managed by Copilot CLI, read-only):

| Table | Key columns |
|-------|-------------|
| `sessions` | id, cwd, repository, branch, summary, created_at |
| `turns` | session_id, turn_index, user_message, assistant_response |

**Enrichment tables** (managed by this tool, prefixed `cst_`):

| Table | Key columns |
|-------|-------------|
| `cst_sessions` | session_id, workspace_name, vscode_edition, custom_title, parser_version, source_format |
| `cst_messages` | session_id, message_index, role, content, timestamp |
| `cst_messages_fts` | content (FTS5 virtual table, synced via triggers) |
| `cst_tool_invocations` | message_id, name, input, result, status |
| `cst_file_changes` | message_id, path, diff, content |
| `cst_command_runs` | message_id, command, result, status, output |

## Workflow: "Continue where we left off"

1. Extract the session GUID from the user's input (see table above)
2. Export: `copilot-session-tools export-markdown --session-id <guid> -o .`
3. Read the exported file to understand context
4. Summarize what was done and what remains
5. Continue the work in the current session

## Workflow: "Find how we solved X before"

1. Search: `copilot-session-tools search "<topic>" --full --limit 30`
2. Identify the most relevant session from results
3. Export: `copilot-session-tools export-markdown --session-id <guid> -o .`
4. Extract the relevant technique/solution
5. Apply it to the current situation

## Known issues

- **Rich console crashes on pipe**: The CLI uses Rich for console output, which can crash on Unicode box-drawing characters when piped. Workaround: don't pipe output; read it directly.
- **Missing sessions**: If a session doesn't appear after scanning, check that the source file exists and wasn't cleaned up by VS Code. Use `--full` scan to force reimport.
