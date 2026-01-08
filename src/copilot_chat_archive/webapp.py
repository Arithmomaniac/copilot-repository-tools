"""Flask web application for viewing Copilot chat archive."""

from datetime import datetime
from urllib.parse import unquote

from flask import Flask, render_template, request, redirect, url_for, session
import markdown

from .database import Database
from .scanner import get_vscode_storage_paths, scan_chat_sessions


# Create a reusable markdown converter with extensions
_md_converter = markdown.Markdown(
    extensions=[
        'tables',           # Support markdown tables
        'fenced_code',      # Support ```code blocks```
        'sane_lists',       # Better list handling
        'smarty',           # Smart quotes and dashes
        'nl2br',            # Convert newlines to <br> tags
    ],
    extension_configs={
        'smarty': {
            'smart_dashes': True,
            'smart_quotes': True,
        },
    },
)


def _markdown_to_html(text: str) -> str:
    """Convert markdown text to HTML using the markdown library."""
    if not text:
        return ""
    
    # Replace Windows line endings with Unix ones for consistent processing
    text = text.replace('\r\n', '\n')
    
    # Replace common VS Code Copilot UI patterns with proper markdown
    import re
    
    # "Using "Tool Name"" -> _Using "Tool Name"_
    text = re.sub(r'^(Using ["""][^"""]+["""])$', r'_\1_', text, flags=re.MULTILINE)
    
    # "_Creating [](file://...)_" -> "_Creating filename_" (extract leaf name within italics)
    text = re.sub(r'_Creating \[\]\(file://[^)]+/([^/)]+)\)_', r'_Creating \1_', text)
    
    # "_Reading [](file://...)_" -> "_Reading filename_" (extract leaf name within italics)
    text = re.sub(r'_Reading \[\]\(file://[^)]+/([^/)]+)\)_', r'_Reading \1_', text)
    
    # "_Edited `filename`_" -> "_Edited filename_" (remove backticks within italics)
    text = re.sub(r'_Edited `([^`]+)`_', r'_Edited \1_', text)
    
    # "Created [](file://...)" -> Better link text
    text = re.sub(r'Created \[\]\((file://[^)]+)\)', r'Created file: [\1](\1)', text)
    
    # "Read [](file://...)" -> Better link text  
    text = re.sub(r'Read \[\]\((file://[^)]+)\)', r'Read file: [\1](\1)', text)
    
    # "Ran terminal command:" -> _Ran terminal command:_
    text = re.sub(r'^(Ran terminal command:.*)$', r'_\1_', text, flags=re.MULTILINE)
    
    # "Let me [action]:" or "Now let me [action]:" -> _Let me [action]:_
    text = re.sub(r'^((?:Now )?[Ll]et me [^:]+:)$', r'_\1_', text, flags=re.MULTILINE)
    
    # "Made changes." at end -> _Made changes._
    text = re.sub(r'^(Made changes\.)$', r'_\1_', text, flags=re.MULTILINE)
    
    # Reset the markdown converter state for each conversion
    _md_converter.reset()
    
    # Convert markdown to HTML
    result = _md_converter.convert(text)
    
    return result


def _urldecode(text: str) -> str:
    """Decode URL-encoded text (e.g., 'c%3A' -> 'c:')."""
    if not text:
        return ""
    return unquote(text)


def _format_timestamp(value: str) -> str:
    """Format an epoch timestamp (milliseconds) to a human-readable date string."""
    if not value:
        return ""
    try:
        # Handle both string and numeric values
        epoch_ms = float(value)
        # Convert milliseconds to seconds
        epoch_s = epoch_ms / 1000
        dt = datetime.fromtimestamp(epoch_s)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        # If parsing fails, return original value
        return str(value)


def _parse_diff_stats(diff: str) -> dict:
    """Parse a diff string and return addition/deletion line counts.
    
    Args:
        diff: The diff string in unified diff format.
        
    Returns:
        Dictionary with 'additions' and 'deletions' counts.
    """
    if not diff:
        return {"additions": 0, "deletions": 0}
    
    additions = 0
    deletions = 0
    
    for line in diff.split('\n'):
        # Skip diff headers and hunk headers
        if line.startswith('+++') or line.startswith('---') or line.startswith('@@'):
            continue
        if line.startswith('+'):
            additions += 1
        elif line.startswith('-'):
            deletions += 1
    
    return {"additions": additions, "deletions": deletions}


def _extract_filename(path: str) -> str:
    """Extract the filename from a file path.
    
    Args:
        path: Full file path (Unix or Windows style).
        
    Returns:
        The filename portion of the path.
    """
    if not path:
        return ""
    # Handle both Unix and Windows path separators
    if '/' in path:
        return path.split('/')[-1]
    if '\\' in path:
        return path.split('\\')[-1]
    return path


def create_app(db_path: str, title: str = "Copilot Chat Archive", storage_paths: list | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        db_path: Path to the SQLite database file.
        title: Title for the archive.
        storage_paths: Optional list of (path, edition) tuples for scanning.
                       If None, uses default VS Code storage paths.

    Returns:
        Configured Flask application.
    """
    app = Flask(
        __name__,
        template_folder="templates",
    )
    
    # Set a secret key for session support (used for transient flash messages)
    # A random key is fine here since sessions only contain ephemeral refresh notifications.
    # Set FLASK_SECRET_KEY environment variable for persistent sessions across restarts.
    import os
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))
    
    # Register Jinja2 filters
    app.jinja_env.filters["markdown"] = _markdown_to_html
    app.jinja_env.filters["urldecode"] = _urldecode
    app.jinja_env.filters["format_timestamp"] = _format_timestamp
    app.jinja_env.filters["parse_diff_stats"] = _parse_diff_stats
    app.jinja_env.filters["extract_filename"] = _extract_filename
    
    # Store database path, title, and storage paths in app config
    app.config["DB_PATH"] = db_path
    app.config["ARCHIVE_TITLE"] = title
    app.config["STORAGE_PATHS"] = storage_paths  # None means use default VS Code paths
    
    def _create_snippet(content: str, max_length: int = 150) -> str:
        """Create a snippet from content, normalizing whitespace."""
        if not content:
            return ""
        # Normalize whitespace (replace newlines and multiple spaces with single space)
        import re
        normalized = re.sub(r'\s+', ' ', content).strip()
        if len(normalized) > max_length:
            return normalized[:max_length] + "..."
        return normalized
    
    @app.route("/")
    def index():
        """List sessions, with optional search and workspace filtering."""
        db = Database(app.config["DB_PATH"])
        query = request.args.get("q", "").strip()
        selected_workspaces = request.args.getlist("workspace")
        
        # Get refresh results from session (set after a refresh operation)
        # Pop to ensure it's only shown once
        refresh_result = session.pop("refresh_result", None)
        
        search_snippets = {}  # session_id -> list of snippets with message links
        
        if query:
            # Use FTS search
            search_results = db.search(query, limit=100)
            
            # Group results by session and collect snippets
            session_ids = []
            for r in search_results:
                sid = r["session_id"]
                if sid not in search_snippets:
                    search_snippets[sid] = []
                    session_ids.append(sid)
                
                # Add snippet (up to 5 per session, each from different message)
                if len(search_snippets[sid]) < 5:
                    # message_index is 0-based, but anchor is 1-based
                    msg_index = r.get("message_index", 0)
                    snippet = {
                        "text": _create_snippet(r.get("highlighted", r.get("content", ""))),
                        "message_anchor": msg_index + 1,  # 1-based for #msg-N
                    }
                    search_snippets[sid].append(snippet)
            
            # Get full session info for matching sessions
            all_sessions = db.list_sessions()
            sessions = [s for s in all_sessions if s["session_id"] in session_ids]
        else:
            sessions = db.list_sessions()
        
        # Apply workspace filter if selected
        if selected_workspaces:
            sessions = [s for s in sessions if s.get("workspace_name") in selected_workspaces]
        
        workspaces = db.get_workspaces()
        stats = db.get_stats()
        
        return render_template(
            "index.html",
            title=app.config["ARCHIVE_TITLE"],
            sessions=sessions,
            workspaces=workspaces,
            stats=stats,
            query=query,
            search_snippets=search_snippets,
            selected_workspaces=selected_workspaces,
            refresh_result=refresh_result,
        )
    
    @app.route("/session/<session_id>")
    def session_view(session_id: str):
        """Render a single session."""
        db = Database(app.config["DB_PATH"])
        session = db.get_session(session_id)
        
        if session is None:
            return render_template(
                "error.html",
                title=app.config["ARCHIVE_TITLE"],
                error="Session not found",
                message=f"No session found with ID: {session_id}",
            ), 404
        
        return render_template(
            "session.html",
            title=app.config["ARCHIVE_TITLE"],
            session=session,
            message_count=len(session.messages),
        )
    
    @app.route("/refresh", methods=["POST"])
    def refresh_database():
        """Refresh the database by scanning for new or updated sessions.
        
        Supports two modes:
        - full=false (default): Incremental refresh, only updates changed sessions
        - full=true: Full rebuild, re-imports all sessions
        """
        db = Database(app.config["DB_PATH"])
        full_refresh = request.form.get("full", "false").lower() == "true"
        
        # Get storage paths - use configured paths or default VS Code paths
        storage_paths = app.config.get("STORAGE_PATHS") or get_vscode_storage_paths()
        
        added = 0
        updated = 0
        skipped = 0
        
        for chat_session in scan_chat_sessions(storage_paths):
            if full_refresh:
                # In full mode, update all sessions
                # Try to add first - if it fails (returns False), session exists and we update
                if db.add_session(chat_session):
                    added += 1
                else:
                    db.update_session(chat_session)
                    updated += 1
            else:
                # Incremental mode: use needs_update() to determine if session should be updated
                if db.needs_update(chat_session.session_id, chat_session.source_file_mtime, chat_session.source_file_size):
                    # Try to add first - if it fails (returns False), session exists and we update
                    if db.add_session(chat_session):
                        added += 1
                    else:
                        db.update_session(chat_session)
                        updated += 1
                else:
                    skipped += 1
        
        # Store refresh result in Flask session for display after redirect
        session["refresh_result"] = {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "mode": "full" if full_refresh else "incremental",
        }
        
        return redirect(url_for("index"))
    
    return app


def run_server(
    host: str = "127.0.0.1",
    port: int = 5000,
    db_path: str = "copilot_chats.db",
    title: str = "Copilot Chat Archive",
    debug: bool = False,
) -> None:
    """Run the Flask development server.

    Args:
        host: Host to bind to.
        port: Port to bind to.
        db_path: Path to the SQLite database file.
        title: Title for the archive.
        debug: Enable debug mode.
    """
    app = create_app(db_path, title)
    app.run(host=host, port=port, debug=debug)
