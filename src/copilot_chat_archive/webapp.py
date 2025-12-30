"""Flask web application for viewing Copilot chat archive."""

from datetime import datetime
from urllib.parse import unquote

from flask import Flask, render_template, request
import markdown

from .database import Database


# Create a reusable markdown converter with extensions
_md_converter = markdown.Markdown(
    extensions=[
        'tables',           # Support markdown tables
        'fenced_code',      # Support ```code blocks```
        'sane_lists',       # Better list handling
        'smarty',           # Smart quotes and dashes
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


def create_app(db_path: str, title: str = "Copilot Chat Archive") -> Flask:
    """Create and configure the Flask application.

    Args:
        db_path: Path to the SQLite database file.
        title: Title for the archive.

    Returns:
        Configured Flask application.
    """
    app = Flask(
        __name__,
        template_folder="templates",
    )
    
    # Register Jinja2 filters
    app.jinja_env.filters["markdown"] = _markdown_to_html
    app.jinja_env.filters["urldecode"] = _urldecode
    app.jinja_env.filters["format_timestamp"] = _format_timestamp
    
    # Store database path and title in app config
    app.config["DB_PATH"] = db_path
    app.config["ARCHIVE_TITLE"] = title
    
    @app.route("/")
    def index():
        """List sessions, with optional search filtering."""
        db = Database(app.config["DB_PATH"])
        query = request.args.get("q", "").strip()
        
        if query:
            # Use FTS search
            search_results = db.search(query, limit=100)
            # Group by session and get unique session IDs
            session_ids = list(dict.fromkeys(r["session_id"] for r in search_results))
            # Get full session info for matching sessions
            all_sessions = db.list_sessions()
            sessions = [s for s in all_sessions if s["session_id"] in session_ids]
        else:
            sessions = db.list_sessions()
        
        workspaces = db.get_workspaces()
        stats = db.get_stats()
        
        return render_template(
            "index.html",
            title=app.config["ARCHIVE_TITLE"],
            sessions=sessions,
            workspaces=workspaces,
            stats=stats,
            query=query,
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
