# Flask Webapp Specification

## Overview

This document specifies the behavior of the Flask webapp that replaces static HTML generation for the Copilot Chat Archive. The webapp renders chat sessions dynamically from SQLite with server-side FTS5 search.

## Definitions

- **Session**: A single Copilot chat conversation, identified by `session_id`
- **Custom Title**: User-defined title for a session (e.g., "VS Code debug configuration for command line app")
- **Workspace Name**: The VS Code workspace/project name where the chat occurred
- **FTS5**: SQLite Full-Text Search extension version 5
- **Snippet**: A highlighted excerpt from a matching message in search results

## Routes

### GET /

Lists all chat sessions with optional search filtering.

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `q` | string | Optional search query for FTS5 full-text search |

**Response:** HTML page with session list

### GET /session/<session_id>

Displays a single chat session with all messages.

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | string | The unique session identifier |

**Response:** HTML page with session details, or 404 if not found

## CLI Command: serve

```bash
copilot-chat-archive serve [OPTIONS]
```

### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--db` | `-d` | `copilot_chats.db` | Path to SQLite database file |
| `--host` | `-h` | `127.0.0.1` | Host to bind to |
| `--port` | `-p` | `5000` | Port to bind to |
| `--title` | `-t` | `Copilot Chat Archive` | Title for the archive |
| `--debug` | | `False` | Enable Flask debug mode |

### Preconditions

- Database file must exist
- Database should contain imported sessions (warning shown if empty)

### Behavior

1. Validates database file exists
2. Displays startup message with database statistics
3. Starts Flask development server
4. Serves requests until interrupted (Ctrl+C)

## Index Page Behavior

### Session Display

Each session item displays:

1. **Clickable Title** (primary link)
   - Shows `custom_title` if available
   - Falls back to `workspace_name` if no custom title
   - Falls back to truncated `session_id` if neither available
   
2. **Workspace Name** (as property, not link)
   - Displayed in session metadata when different from title

3. **Metadata**
   - Message count
   - Creation timestamp (formatted)
   - VS Code edition badge (stable/insider)

### Search Behavior

When `?q=<term>` is provided:

1. Performs FTS5 search across message content
2. Filters sessions to only show those with matching messages
3. Displays search info bar with query and "clear search" link
4. Shows up to 5 search snippets per session:
   - Each snippet is a quote box with highlighted match
   - Snippets link directly to the matching message (`#msg-N`)
   - Newlines are normalized to spaces in snippet display
   - Each snippet comes from a different message

### Edge Cases

- Empty query (`?q=` or `?q=  `): Shows all sessions
- No matching results: Shows empty list with sessions container
- Invalid FTS5 syntax: Returns error gracefully

## Session View Behavior

### Header

- Back link to index (`/`)
- Session title (h2)
- Metadata: message count, workspace path, creation date, edition badge
- Session ID (in small text)

### Message Display

Each message shows:

1. **Header row**
   - Role badge (USER / ASSISTANT)
   - Timestamp (if available)
   - Anchor link (#N)

2. **Content**
   - Markdown rendered to HTML
   - Proper paragraph breaks preserved
   - Code blocks with syntax highlighting
   - Tables rendered correctly

3. **Collapsible sections** (if applicable)
   - Tool Invocations
   - File Changes
   - Command Runs

### Markdown Rendering

The markdown filter must:

- Convert fenced code blocks to `<pre><code>`
- Convert inline code to `<code>`
- Render tables with proper styling
- Preserve paragraph breaks (double newline → new paragraph)
- Convert single newlines to `<br>` within paragraphs
- Handle smart quotes and dashes

### Content Blocks

When messages have structured `content_blocks`:

- **thinking** blocks: Collapsed by default, purple left border, 💭 icon
- **text** blocks: Normal display with green left border (result block)
- Other blocks: Normal markdown rendering

### Anchor Navigation

- URL hash `#msg-N` scrolls to message N
- Scrolling uses smooth behavior
- Target message receives highlight animation (2s pulse)

## Error Handling

### 404 - Session Not Found

Displayed when `session_id` doesn't exist in database:

- Title: "Session not found"
- Message: "No session found with ID: {session_id}"
- Link back to index

### Database Errors

- Missing database: CLI exits with error before starting server
- Corrupted database: Flask returns 500 error page

## Non-Goals

- User authentication/authorization
- Session editing or deletion via UI
- Real-time updates (WebSocket)
- Production WSGI server configuration
- Session import via webapp (CLI only)

## Programmatic API

```python
from copilot_chat_archive import create_app, run_server

# Create Flask app for embedding or testing
app = create_app(db_path="copilot_chats.db", title="My Archive")

# Run development server
run_server(host="127.0.0.1", port=5000, db_path="copilot_chats.db")
```

### Jinja2 Filters

The app registers these custom filters:

| Filter | Description |
|--------|-------------|
| `markdown` | Converts markdown text to HTML |
| `urldecode` | Decodes URL-encoded text |
| `format_timestamp` | Formats epoch milliseconds to "YYYY-MM-DD HH:MM:SS" |
