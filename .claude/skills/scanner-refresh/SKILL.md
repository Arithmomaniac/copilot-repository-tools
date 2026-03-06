---
name: scanner-refresh
description: Research recent changes in Copilot CLI/SDK/VS Code repositories (and optional Shadow CLI) and update the chat session scanner to handle new event types and response kinds. Use when user says "refresh scanner", "update parser", "check for new event types", or mentions keeping the scanner up-to-date with Copilot changes.
---

# Scanner Refresh

Analyze recent changes in GitHub Copilot repositories and update the scanner.py parsing logic for new CLI event types, VS Code response kinds, session metadata, and data formats.

## Working directory

Use `temp_export/` (gitignored) as the working directory for any intermediate markdown files such as gap analysis reports, research notes, or comparison summaries. This keeps the repo clean while preserving artifacts for review.

```powershell
# Ensure working directory exists
New-Item -ItemType Directory -Path "temp_export" -Force | Out-Null
```

## Configuration

Optional machine-specific settings are stored in `.claude/skills/scanner-refresh/config.local.json` (gitignored). This file is **not required** — the skill works without it. Create it when you have a local Shadow CLI mirror available.

The Shadow CLI is a beautified/decompiled mirror of each `@github/copilot` release, maintained via `Sync-CopilotShadow.ps1` (in the `avilevin-pastebin` repo). Each commit in the shadow repo represents a CLI version.

**Schema:**
```json
{
  "shadowCli": {
    "localPath": "C:\\path\\to\\copilot-cli-shadow",
    "description": "Beautified/decompiled mirror of @github/copilot CLI releases"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `shadowCli.localPath` | `string` | No | Absolute path to the local Shadow CLI repo. If present and the directory exists, the skill includes it in research. |

**Keeping the shadow repo up to date:**
```powershell
# Run the sync script to pull the latest CLI version into the shadow repo
# (located in the avilevin-pastebin repo)
& "C:\_SRC\avilevin-pastebin\Sync-CopilotShadow.ps1"
```

## Quick start

When triggered, follow this workflow:
1. Research recent changes in source repositories
2. Analyze local session files for actual event types in use
3. Compare against current scanner.py handlers
4. Check for structural/metadata changes (session types, locations, storage formats)
5. Write gap analysis and findings to `temp_export/` as markdown
6. Implement missing handlers and add tests

## Instructions

### Step 0: Determine search window and detect optional sources

Restrict research to changes **since the last scanner update** to avoid redundant work.

```powershell
# Get the date of the last commit that touched scanner.py or the SKILL.md
$lastScannerChange = git --no-pager log --format="%ai" -1 -- `
  src/copilot_session_tools/scanner/ `
  .claude/skills/scanner-refresh/SKILL.md
Write-Output "Search window: since $lastScannerChange"
```

Use this date as the `--since` filter for all `github-mcp-server-list_commits` calls and as the `since:` qualifier for `github-mcp-server-search_pull_requests`. If no date is available, fall back to "past 2 weeks".

**Detect Shadow CLI (optional):**

```powershell
$configPath = ".claude/skills/scanner-refresh/config.local.json"
$shadowCliPath = $null
if (Test-Path $configPath) {
    $config = Get-Content $configPath | ConvertFrom-Json
    $candidate = $config.shadowCli.localPath
    if ($candidate -and (Test-Path $candidate)) {
        $shadowCliPath = $candidate
        Write-Output "Shadow CLI detected at: $shadowCliPath"
    } else {
        Write-Output "Shadow CLI config found but path does not exist: $candidate"
    }
} else {
    Write-Output "No config.local.json — skipping Shadow CLI research"
}
```

If `$shadowCliPath` is set, include it in Step 1 research. Otherwise, skip Shadow CLI sections silently.

### Step 1: Research source repositories

Check recent commits and PRs (**since last scanner update**) in these repositories:

| Repository | What to look for |
|------------|------------------|
| `github/copilot-cli` | New event types in JSONL format, changes to session structure |
| `github/copilot-sdk` | Schema changes, new data structures |
| `microsoft/vscode-copilot-chat` | New response item kinds, chat session format changes, background session storage (`chatSessionWorktreeServiceImpl.ts`) |
| `microsoft/vscode` | Session serialization (`chatModel.ts`), storage (`chatSessionStore.ts`), location enum (`constants.ts`), copy-all format (`chatCopyActions.ts`, `chatActions.ts`) |
| **Shadow CLI** *(if detected)* | New event types, JSONL format changes, session structure, changelog entries, SDK divergences |

Use GitHub MCP tools:
```
github-mcp-server-list_commits (past 2 weeks)
github-mcp-server-search_pull_requests (merged PRs)
```

#### Shadow CLI research (local repo — only if detected in Step 0)

The Shadow CLI is a **beautified/decompiled mirror** of the `@github/copilot` CLI package. Each commit represents a released version (e.g., `v0.0.421-0 (commit 49c1cdc)`), created by `Sync-CopilotShadow.ps1`. It is **not** a traditional source repository — research means diffing between version commits, grepping the beautified JS, and reading structured schema/changelog files.

**Key files in the shadow repo:**

| File | What it contains |
|------|-----------------|
| `schemas/session-events.schema.json` | **Canonical JSON Schema for CLI session events** — the authoritative source for all event types, their `data` payloads, and field types. Compare against scanner handlers directly. |
| `changelog.json` | Structured changelog with version entries, change types (`added`/`fixed`/`improved`/`removed`), descriptions, and PR links to `github/copilot-agent-runtime`. |
| `index.js` | Beautified CLI source (~250K lines) — grep for event type registrations, callback handlers, serialization logic. |
| `sdk/index.js` | Beautified SDK source — event schemas, data structures. |
| `sdk/index.d.ts` | TypeScript type definitions (already readable) — interfaces, enums, type unions. |
| `definitions/*.agent.yaml` | Built-in agent definitions (explore, task, code-review) — `promptParts`, tool filters, model configs. |

**Research commands:**

```powershell
# 1. What versions were released since last scanner update?
git -C $shadowCliPath --no-pager log --oneline --since="$lastScannerChange"

# 2. Diff the session-events schema between versions (most important!)
#    This reveals new/changed event types and payload fields
git -C $shadowCliPath --no-pager diff HEAD~1 -- schemas/session-events.schema.json

# 3. Diff the changelog for new entries
git -C $shadowCliPath --no-pager diff HEAD~1 -- changelog.json

# 4. Full diff stats to see what changed overall
git -C $shadowCliPath --no-pager diff --stat HEAD~2..HEAD

# 5. Grep for event type string literals in the beautified source
git -C $shadowCliPath --no-pager grep -n '"type":\s*"' -- schemas/session-events.schema.json | Select-Object -First 50

# 6. Search for specific event handling patterns in index.js
git -C $shadowCliPath --no-pager grep -n "event_type\|eventType\|\.type ===" -- index.js | Select-Object -First 50

# 7. Check agent definitions for changes (new tools, prompts)
git -C $shadowCliPath --no-pager diff HEAD~1 -- definitions/

# 8. Check SDK type definitions for new interfaces/enums
git -C $shadowCliPath --no-pager diff HEAD~1 -- sdk/index.d.ts
```

**What to look for in the Shadow CLI:**

| Area | Best source | How to find it |
|------|------------|---------------|
| New event types | `session-events.schema.json` | Diff the schema — new `"type": "const"` entries = new events |
| Event payload changes | `session-events.schema.json` | New or changed `"data"` properties in existing event types |
| Changelog entries | `changelog.json` | New version entries with `"type": "added"` for features |
| Implementation details | `index.js` | `git grep` for handler registrations, serialization calls |
| SDK type changes | `sdk/index.d.ts` | Diff for new interfaces, enum values, type unions |
| Agent definition changes | `definitions/*.agent.yaml` | New tools in `promptParts.tools`, model or prompt changes |
| Version/release info | `git log` | Commit messages are `vX.Y.Z (commit hash)` |

**Comparing Shadow CLI against public repos:**

After researching the shadow repo, compare findings against `github/copilot-cli` and `github/copilot-sdk`:
1. **Schema-defined but unhandled events** — types in `session-events.schema.json` that the scanner doesn't handle
2. **Changelog features** — new capabilities mentioned in `changelog.json` that might produce new event shapes
3. **SDK type divergences** — interfaces in `sdk/index.d.ts` that differ from what `github/copilot-sdk` exposes publicly

Save findings to `temp_export/shadow-cli-research.md`.

**Key files to monitor for structural changes in `microsoft/vscode`:**

| File | What it controls |
|------|-----------------|
| `src/vs/workbench/contrib/chat/common/model/chatModel.ts` | `ISerializableChatData` (v1/v2/v3), `IExportableChatData`, `SerializedChatResponsePart`, `normalizeSerializableChatData()` |
| `src/vs/workbench/contrib/chat/common/model/chatSessionStore.ts` | `IChatSessionEntryMetadata` (includes `initialLocation`, `isExternal`, `timing`, `stats`), storage format (flat JSON → JSONL append log) |
| `src/vs/workbench/contrib/chat/common/constants.ts` | `ChatAgentLocation` enum (`panel`, `terminal`, `notebook`, `editor`), `ChatModeKind` enum (`ask`, `edit`, `agent`) |
| `src/vs/workbench/contrib/chat/browser/actions/chatCopyActions.ts` | "Copy All" action — calls `stringifyItem()` |
| `src/vs/workbench/contrib/chat/browser/actions/chatActions.ts` | `stringifyItem()` — flattens response to `item.response.toString()` (lossy markdown) |
| `src/vs/workbench/contrib/chat/common/model/chatSessionOperationLog.ts` | New JSONL append-log session storage format (>=1.109) |

Also consult DeepWiki/Context7 for documentation on event schemas.

**Mining past Copilot sessions for format examples:**

Use `copilot-session-tools search` to find past sessions that discussed formats, parsing, or scanner changes:
```powershell
uv run copilot-session-tools search "vscode session type background" --full --limit 20
uv run copilot-session-tools search '"copy all"' --full --limit 20
```

**Do NOT use `copilot --share`** for format comparison. CLI `--share` only exports the
current turn (post-compaction), not the full session history. Our tool parses the raw
`events.jsonl` which contains the complete history — strictly more comprehensive. Use our
own `uv run copilot-session-tools export <sessionId>` for markdown export instead.

### Step 2: Analyze local session files

**CLI sessions** are stored in:
- `~/.copilot/session-state/*/events.jsonl`

Extract unique event types:
```powershell
Get-ChildItem -Path "$env:USERPROFILE\.copilot\session-state" -Recurse -Filter "events.jsonl" |
  ForEach-Object { Get-Content $_.FullName } |
  ForEach-Object { ($_ | ConvertFrom-Json).type } |
  Sort-Object -Unique
```

**VS Code sessions** come in three formats (see `sample_files/README.md`):

1. **JSON** (structured export / `state.vscdb`) — full metadata + response item kinds
   - `~/.config/Code/User/workspaceStorage/*/state.vscdb` (SQLite, key-value in `ItemTable`)
   - Or exported `.json` files
2. **HTML** (DOM snapshot from VS Code Inspector) — rendered panel output
3. **Markdown** ("Copy All" right-click) — lossy `stringifyItem()` output, just `username: response.toString()`

Extract unique response kinds from VS Code JSON:
```powershell
# From exported JSON
Get-Content session.json | ConvertFrom-Json |
  Select-Object -ExpandProperty requests |
  Select-Object -ExpandProperty response |
  Select-Object -ExpandProperty value |
  ForEach-Object { $_.kind } |
  Sort-Object -Unique
```

**Check `initialLocation` values** across sessions to see which session types are in use:
```powershell
# From exported JSON files
Get-ChildItem -Path "sample_files" -Filter "*.json" |
  ForEach-Object { (Get-Content $_.FullName | ConvertFrom-Json).initialLocation } |
  Sort-Object -Unique
```

**VS Code session types** (from `ChatAgentLocation` in `constants.ts`):

| `initialLocation` value | Meaning |
|------------------------|---------|
| `panel` | Sidebar chat, chat editor, or quick chat |
| `terminal` | Terminal inline chat |
| `notebook` | Notebook chat |
| `editor` | Inline chat in text editor |

Background/cloud sessions are marked with `isExternal: true` in `IChatSessionEntryMetadata` and may be stored via `chatSessionWorktreeServiceImpl.ts` in `vscode-copilot-chat`.

**Cross-referencing formats using sample files:**

The `sample_files/` directory contains the same sessions in all three formats (JSON, HTML, MD) with matching UUID filenames. Use these to compare what information each format preserves:

```powershell
# Compare a session across all three formats
$id = "7add4c61-3ac2-42db-b672-cf461938cdfb"
# JSON — structured response items with kind, tool invocations, metadata
Get-Content "sample_files/$id.json" | ConvertFrom-Json | Select-Object initialLocation, version
# HTML — rendered DOM, shows what the VS Code panel actually displays (classes, data attributes)
# MD — "Copy All" output, shows what users get when they right-click > Copy All
```

When identifying rendering gaps, compare how the same content appears in:
1. The **JSON** response items (ground truth)
2. The **HTML** rendered panel (what users see in VS Code)
3. The **MD** copy-all output (what users paste elsewhere)
4. The **web viewer** output from this tool (what we render)

Any content visible in HTML/MD but missing from our web viewer parsing = a gap to fix.

### Step 3: Compare with current scanner.py

The scanner is at: `src/copilot_session_tools/scanner/`

**CLI event handlers** are in `_parse_cli_jsonl_events()` method (~line 1950+):
- Look for `elif event_type == "..."` patterns
- Current handlers include: `user.message`, `assistant.message`, `tool.use`, `tool.result`, `session.model_change`, `assistant.reasoning`, `skill.invoked`, `session.compaction_complete`
- CLI events for tool lifecycle: `tool.execution_start` / `tool.execution_complete` (paired by `toolCallId`)
- MCP tools appear as `tool.execution_start` with `mcpServerName` and `mcpToolName` fields

**Tool pretty-formatting** is in `_build_tool_invocation()` (~line 1735):
- Known tools get user-friendly `invocation_message` values (e.g., `edit` → "Edited `filename`")
- Unknown tools fall back to `description` argument, then bare `tool_name`
- **When adding new MCP tools**, add a pretty handler here — bare tool names like "web_search" are unhelpful in the viewer
- Current pretty-formatted tools: `view`, `edit`, `create`, `show_file`, `str_replace_editor`, `grep`, `glob`, `web_search`, `web_fetch`, `task`, `read_agent`, `list_agents`, `store_memory`, `task_complete`, `sql`, `ask_user`, `skill`, `lsp`, `propose_work`, `exit_plan_mode`, `fetch_copilot_cli_documentation`, `gh-advisory-database`, `parallel_validation`, `list_bash`, `list_powershell`, `update_todo`
- Pattern for adding new tool formatters:
```python
elif tool_name == "new_tool":
    key_arg = arguments.get("key_param", "")
    invocation_message = f"🔧 Description: {key_arg}" if key_arg else "🔧 Description"
```

**VS Code kind handlers** are in `_process_vscode_response_item()` method (~line 1100+):
- Look for `if kind == "..."` patterns
- Current handlers include: `markdownContent`, `codeBlockContent`, `inlineReference`, `progressMessage`, `treeData`, `thinkingContent`, `toolInvocation`, `toolMessage`, `confirmationWidget`, `buttonPresentation`, `progressTaskSerialized`

**VS Code session parsing** uses two entry points:
- `_parse_chat_session_file()` — parses exported `.json` files
- `_parse_vscdb_file()` → `_extract_session_from_dict()` — parses `state.vscdb` SQLite databases

Both use the same response-item processing logic, looking for `requests` (or `messages`/`exchanges`) arrays.

**Session metadata extraction status:**
- ✅ `session.start` context (`cwd`, `gitRoot`, `repository`) — extracted for workspace/repo URL detection
- ❌ `initialLocation` (panel/terminal/notebook/editor) — not read from VS Code JSON
- ❌ `isExternal` (background/cloud sessions) — not read
- ❌ `version` (serialization format version 1/2/3) — not read
- ❌ `ChatModeKind` (ask/edit/agent mode) — not read
- ❌ Session timing data (`IChatSessionTiming`) — not read

**"Copy All" markdown format** — the scanner does not handle `.md` files from "Copy All".
This format is lossy: `stringifyItem()` calls `item.response.toString()` which flattens
all response items (tool invocations, thinking blocks, inline references) into plain
markdown text. The format is just:
```
username: message text

username: response markdown
```

### Step 4: Identify gaps

Create a gap analysis and save it to `temp_export/scanner-gap-analysis.md`:
- List event types found in local files but not handled in scanner
- List structural/metadata fields present in source repos but not extracted
- **Include Shadow CLI findings** (if researched) — flag Shadow-only event types and payload differences
- Categorize by priority:
  - **HIGH**: Contains user-visible content that would be lost
  - **MEDIUM**: Contains metadata that aids understanding (e.g., session type, mode)
  - **LOW**: Internal/transient events with no content

**Structural gaps to always check:**
- New `ChatAgentLocation` values (new session locations)
- New `ChatModeKind` values (new interaction modes)
- New `SerializedChatResponsePart` types (new response content types)
- Changes to `ISerializableChatData` version (currently v3)
- New `IChatSessionEntryMetadata` fields
- Changes to `stringifyItem()` format (affects "Copy All" markdown parsing)
- New VS Code JSONL append-log format (`chatSessionOperationLog.ts`, used >=1.109)
- **New MCP tools without pretty-format handlers** — check `_build_tool_invocation()` for any tool names that fall through to bare name display
- **Shadow CLI event types** not present in public CLI (if Shadow CLI was researched) — compare `session-events.schema.json` event types against scanner handlers; decide whether to add handlers preemptively or wait for public release

### Step 5: Implement handlers

For each HIGH/MEDIUM priority gap:

**Using the Shadow CLI to understand how new events should render:**

When implementing handlers for new event types, the shadow repo provides critical context for *how* to render them — not just *what* fields they contain. Use these sources in order:

1. **`schemas/session-events.schema.json`** — defines the payload shape (which fields exist, types, required vs optional)
2. **`sdk/index.d.ts`** — TypeScript interfaces show the semantic meaning of fields (e.g., `PermissionRequestKind`, `ElicitationMode`) and which are user-facing
3. **`index.js`** — search for how the CLI itself renders these events in the terminal. Grep for the event type string to find the handler, then trace how it formats the output:
   ```powershell
   # Find how the CLI renders a specific event type
   git -C $shadowCliPath --no-pager grep -n '"permission.requested"\|permission_requested\|permissionRequest' -- index.js | Select-Object -First 20
   
   # Find rendering/formatting functions near event handlers
   git -C $shadowCliPath --no-pager grep -n -B 5 -A 15 '"permission.requested"' -- index.js
   ```

The CLI's own rendering logic (what it shows in the terminal timeline) is the best guide for what information is important to display in our web viewer and markdown export.

**CLI event handler pattern:**
```python
elif event_type == "new.event.type":
    content = data.get("content") or data.get("text", "")
    if content:
        content_block = ContentBlock(
            kind="appropriate_kind",  # text, thinking, status, skill, etc.
            content=content
        )
        message.content_blocks.append(content_block)
```

**VS Code kind handler pattern:**
```python
elif kind == "newKindType":
    content_value = item.get("content", {}).get("value", "")
    if content_value:
        content_block = ContentBlock(
            kind="appropriate_kind",
            content=content_value
        )
        blocks.append(content_block)
```

### Step 6: Add tests

Add tests in `tests/test_scanner.py`:

```python
class TestNewEventTypes:
    def test_new_cli_event(self):
        """Test handling of new.event.type CLI events."""
        event = {
            "type": "new.event.type",
            "data": {"content": "test content"}
        }
        # ... test implementation
```

### Step 7: Validate

Run the full test suite:
```bash
uv run pytest tests/ --ignore=tests/test_webapp_e2e.py -v
uv run ruff check .
uv run ruff format .
uv run ty check
```

**Snapshot baselines:** Scanner and exporter changes will likely break snapshot tests (`tests/test_snapshot.py`). After confirming the new output is correct, regenerate baselines:
```bash
# Regenerate only failing baselines
uv run pytest tests/test_snapshot.py --force-regen -v
# Then force-add gitignored baselines for CI
git add -f tests/snapshots/baselines/
```

### Step 8: Visual validation

After tests pass, visually verify that new event types render correctly in both the markdown export and the web viewer.

**Find real sessions containing the new event types:**
```powershell
# Search local sessions for the new events
Get-ChildItem -Path "$env:USERPROFILE\.copilot\session-state" -Recurse -Filter "events.jsonl" |
  ForEach-Object {
    $types = Get-Content $_.FullName | ForEach-Object { ($_ | ConvertFrom-Json -ErrorAction SilentlyContinue).type } | Sort-Object -Unique
    # Match against the new event types you just implemented
    $new = $types | Where-Object { $_ -match 'the_new_event_type' }
    if ($new) { Write-Output "$($_.Directory.Name): $($new -join ', ')" }
  } | Select-Object -First 5
```

**Enrich the sessions and start the web viewer:**
```powershell
uv run copilot-session-tools enrich <session-id>
uv run copilot-session-tools web --port 5000
```

**Use Playwright to capture screenshots** of the web viewer and markdown export:
```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    # CLI session with new events
    page.goto("http://127.0.0.1:5000/session/<id>")
    page.wait_for_load_state("networkidle")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.screenshot(path="temp_export/web_new_events.png", full_page=False)
    # Also spot-check a VS Code session to confirm no regressions
    page.goto("http://127.0.0.1:5000/session/<vscode-session-id>")
    page.wait_for_load_state("networkidle")
    page.screenshot(path="temp_export/web_vscode_check.png", full_page=False)
    browser.close()
```

**Upload screenshots to yourself** (use the `view` tool on captured PNGs) and verify:
- New content blocks render with readable formatting
- Content isn't truncated or garbled
- Layout is consistent with existing status blocks (warnings, errors, mode changes)
- Long content (e.g., multi-line task summaries) wraps properly

### Step 9: Showboat demo

Create an executable demo document with [showboat](https://github.com/simonw/showboat) that demonstrates what was found and changed. The demo should show:
- Raw sample events from real sessions
- How each new event type renders in the markdown export and web viewer
- Screenshots captured via Playwright as proof-of-work

```powershell
showboat init temp_export/scanner-refresh-demo.md "Scanner Refresh: New Event Types"

# For each new event type:
showboat note temp_export/scanner-refresh-demo.md "## <event_type> — <what it represents>"
showboat exec temp_export/scanner-refresh-demo.md powershell '<command to extract a raw sample event from a real session>'
showboat note temp_export/scanner-refresh-demo.md "Rendered in the web viewer:"
showboat image temp_export/scanner-refresh-demo.md '<playwright screenshot command>'
```

## Content block kinds reference

| Kind | Use for |
|------|---------|
| `text` | Regular message content |
| `thinking` | AI reasoning/thinking blocks |
| `status` | Progress updates, compaction summaries |
| `skill` | Skill invocations with descriptions |
| `intent` | Intent declarations |
| `ask_user` | User questions with choices |
| `toolInvocation` | Tool calls and results |

## VS Code session data model reference

**Serialization format** (`ISerializableChatData` v3 in `chatModel.ts`):
```
{
  version: 3,
  sessionId: string,
  creationDate: number,
  initialLocation: "panel" | "terminal" | "notebook" | "editor" | undefined,
  responderUsername: string,
  customTitle: string | undefined,
  requests: ISerializableChatRequestData[],
  // v3 additions: hasPendingEdits, pendingRequestQueue, inputState
}
```

**Response parts** (`SerializedChatResponsePart` union type):
- `IMarkdownString` — markdown text content
- `IChatResponseProgressFileTreeData` — file tree data
- `IChatContentInlineReference` — inline file/symbol references
- `IChatAgentMarkdownContentWithVulnerability` — markdown with vulnerability info
- `IChatThinkingPart` — thinking/reasoning content
- `IChatProgressResponseContentSerialized` — progress content
- `IChatQuestionCarousel` — question carousel UI

**Session metadata** (`IChatSessionEntryMetadata` in `chatSessionStore.ts`):
- `sessionId`, `title`, `lastMessageDate`
- `initialLocation?: ChatAgentLocation` — where chat was started
- `isExternal?: boolean` — background/cloud sessions
- `hasPendingEdits?: boolean`
- `isEmpty?: boolean`
- `timing: IChatSessionTiming` — created, lastRequestStarted, lastRequestEnded
- `stats?: IChatSessionStats`
- `lastResponseState: ResponseModelState`

**"Copy All" format** (from `chatActions.ts` `stringifyItem()`):
```
username: user message text

username: flattened response markdown (response.toString())
```
This is lossy — tool invocations, thinking blocks, inline references, and structured
content are all flattened into plain markdown. No metadata is preserved.

## Best practices

- Only implement handlers for events with meaningful content
- Skip internal/transient events (add to skip list with comment)
- Preserve original formatting where possible
- Add tests for each new handler
- Update `_merge_content_blocks()` if new kinds should stay standalone
- When adding metadata extraction (e.g., `initialLocation`), store it on `ChatSession` and propagate through to the database schema
- Consider whether new session types (terminal, notebook, editor) need different parsing logic than panel sessions
- Monitor the VS Code JSONL append-log format for structural changes separate from the flat JSON format
- **Always add pretty-format handlers** for new MCP/tool names in `_build_tool_invocation()` — bare tool names are unhelpful in the web viewer and markdown export
- When adding new tools, extract the most informative argument (e.g., `query` for search tools, `path` for file tools) into the `invocation_message`

## Export parity notes

Our markdown export (`markdown_exporter.py`) is **strictly more comprehensive** than CLI `--share`:

| Feature | Our export | CLI `--share` |
|---------|-----------|---------------|
| Full session history | ✅ All messages from `events.jsonl` | ❌ Current turn only (post-compaction) |
| Tool invocations | ✅ Inline with arguments | ✅ With emoji status icons |
| Thinking blocks | ✅ Noticed or included | ❌ Not shown |
| Session metadata | ✅ ID, workspace, path, edition, user | ✅ ID, duration, timestamps |
| Code blocks | ✅ Preserved | ✅ Preserved |

CLI `--share` cosmetic features (emoji role markers 👤💬, `<details>` for tool args,
`<sub>⏱️</sub>` timing) are NOT worth adopting — they add noise without adding content.

**Do not use `copilot --share` for comparison or research** — it only exports the active
turn after compaction. Use our own `copilot-session-tools export` for full session markdown.
