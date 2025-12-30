# ADR 001: Replace Static HTML Archive with Flask Webapp

## Status

Accepted

## Context

The original implementation generated static HTML files for viewing Copilot chat sessions. This required:
- Running a separate `generate` command to create HTML files
- Re-generating files when new sessions were added
- Client-side JavaScript for search filtering (limited functionality)
- Static file hosting or local file:// browsing

Users needed a more dynamic solution that:
- Provides real-time access to the latest sessions
- Supports powerful server-side full-text search (FTS5)
- Eliminates the need for manual regeneration
- Enables future extensibility (e.g., session management, annotations)

## Decision

Replace the static HTML generation (`viewer.py` and `generate` CLI command) with a dynamic Flask web application that renders sessions on-the-fly from the SQLite database.

### Key Changes

1. **New Flask webapp** (`webapp.py`)
2. **Removed static HTML generation** (`viewer.py`)
3. **New `serve` CLI command** replaces `generate` command
4. **Server-side FTS5 search** replaces client-side filtering

## Feature Specification

### Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | List all sessions; filter with `?q=<search_term>` query parameter |
| `/session/<session_id>` | GET | Display a single session with all messages |

### CLI Command: `serve`

```bash
copilot-chat-archive serve [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--db, -d` | `copilot_chats.db` | Path to SQLite database file |
| `--host, -h` | `127.0.0.1` | Host to bind to |
| `--port, -p` | `5000` | Port to bind to |
| `--title, -t` | `Copilot Chat Archive` | Title for the archive |
| `--debug` | `False` | Enable Flask debug mode |

**Example usage:**

```bash
# Start server with default settings
copilot-chat-archive serve

# Start with custom options
copilot-chat-archive serve --db my_chats.db --port 8080 --title "My Copilot Chats"

# Start in debug mode
copilot-chat-archive serve --debug
```

### Search Functionality

The index page includes a search form that performs server-side full-text search:

- Uses SQLite FTS5 for fast, indexed search
- Searches across message content
- Returns sessions containing matching messages
- Supports FTS5 query syntax (AND, OR, NOT, phrase matching)

**Example searches:**

- `Python` - find sessions mentioning Python
- `"code review"` - find exact phrase
- `Flask OR Django` - find either term

### Templates

Templates use embedded CSS (no external static files required):

- `index.html` - Session list with search form
- `session.html` - Single session view with messages
- `error.html` - Error pages (404, etc.)

### Programmatic API

```python
from copilot_chat_archive import create_app, run_server

# Create Flask app for embedding or testing
app = create_app(db_path="copilot_chats.db", title="My Archive")

# Run development server
run_server(host="127.0.0.1", port=5000, db_path="copilot_chats.db")
```

## Consequences

### Positive

- **Real-time data**: Sessions are rendered from the database, always current
- **Better search**: Server-side FTS5 search is faster and more powerful
- **Simpler workflow**: No need to regenerate static files
- **Extensibility**: Flask enables future features (editing, annotations, API)
- **Self-contained**: No external static file dependencies

### Negative

- **Requires running server**: Cannot view without running the Flask server
- **Development server only**: Flask's built-in server is not production-ready; for production use, a WSGI server (e.g., gunicorn) would be needed
- **Memory usage**: Server process uses memory while running

### Neutral

- **No breaking API changes**: The database schema remains unchanged
- **Templates updated**: Links changed from static paths to Flask routes

## Migration Guide

### Before (v0.1.0)

```bash
copilot-chat-archive scan
copilot-chat-archive generate --output ./archive
# Then open ./archive/index.html in browser
```

### After (v0.2.0)

```bash
copilot-chat-archive scan
copilot-chat-archive serve
# Then open http://127.0.0.1:5000/ in browser
```

## References

- [Flask Documentation](https://flask.palletsprojects.com/)
- [SQLite FTS5](https://www.sqlite.org/fts5.html)
