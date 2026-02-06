---
name: scanner-refresh
description: Research recent changes in Copilot CLI/SDK/VS Code repositories and update the chat session scanner to handle new event types and response kinds. Use when user says "refresh scanner", "update parser", "check for new event types", or mentions keeping the scanner up-to-date with Copilot changes.
---

# Scanner Refresh

Analyze recent changes in GitHub Copilot repositories and update the scanner.py parsing logic for new CLI event types and VS Code response kinds.

## Working directory

Use `temp_export/` (gitignored) as the working directory for any intermediate markdown files such as gap analysis reports, research notes, or comparison summaries. This keeps the repo clean while preserving artifacts for review.

```powershell
# Ensure working directory exists
New-Item -ItemType Directory -Path "temp_export" -Force | Out-Null
```

## Quick start

When triggered, follow this workflow:
1. Research recent changes in source repositories
2. Analyze local session files for actual event types in use
3. Compare against current scanner.py handlers
4. Write gap analysis and findings to `temp_export/` as markdown
5. Implement missing handlers and add tests

## Instructions

### Step 1: Research source repositories

Check recent commits and PRs (past 2 weeks) in these repositories:

| Repository | What to look for |
|------------|------------------|
| `github/copilot-cli` | New event types in JSONL format, changes to session structure |
| `github/copilot-sdk` | Schema changes, new data structures |
| `microsoft/vscode-copilot-chat` | New response item kinds, chat session format changes |

Use GitHub MCP tools:
```
github-mcp-server-list_commits (past 2 weeks)
github-mcp-server-search_pull_requests (merged PRs)
```

Also consult DeepWiki/Context7 for documentation on event schemas.

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

**VS Code sessions** are stored in:
- `~/.config/Code/User/workspaceStorage/*/state.vscdb` (SQLite)
- Or exported JSON files

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

### Step 3: Compare with current scanner.py

The scanner is at: `packages/common/src/copilot_repository_tools_common/scanner.py`

**CLI event handlers** are in `_parse_cli_jsonl_events()` method (~line 1950+):
- Look for `elif event_type == "..."` patterns
- Current handlers include: `user.message`, `assistant.message`, `tool.use`, `tool.result`, `session.model_change`, `assistant.reasoning`, `skill.invoked`, `session.compaction_complete`

**VS Code kind handlers** are in `_process_vscode_response_item()` method (~line 1100+):
- Look for `if kind == "..."` patterns
- Current handlers include: `markdownContent`, `codeBlockContent`, `inlineReference`, `progressMessage`, `treeData`, `thinkingContent`, `toolInvocation`, `toolMessage`, `confirmationWidget`, `buttonPresentation`, `progressTaskSerialized`

### Step 4: Identify gaps

Create a gap analysis and save it to `temp_export/scanner-gap-analysis.md`:
- List event types found in local files but not handled in scanner
- Categorize by priority:
  - **HIGH**: Contains user-visible content that would be lost
  - **MEDIUM**: Contains metadata that aids understanding
  - **LOW**: Internal/transient events with no content

### Step 5: Implement handlers

For each HIGH/MEDIUM priority gap:

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

## Best practices

- Only implement handlers for events with meaningful content
- Skip internal/transient events (add to skip list with comment)
- Preserve original formatting where possible
- Add tests for each new handler
- Update `_merge_content_blocks()` if new kinds should stay standalone
