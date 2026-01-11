"""Copilot Repository Tools - Web interface.

This module provides a Flask-based web interface for browsing and searching
VS Code GitHub Copilot chat history.

Features:
- Session list with workspace names and message counts
- Workspace filtering to focus on specific projects
- Client-side search to filter sessions
- Dark mode support via CSS prefers-color-scheme
- Responsive design for mobile and desktop
- Syntax highlighting for code blocks

This project borrows patterns from several open-source projects:
- simonw/claude-code-transcripts: HTML transcript generation approach
"""

__version__ = "0.1.0"

from .webapp import create_app, run_server

__all__ = [
    "__version__",
    "create_app",
    "run_server",
]


def main():
    """Entry point for the web application.
    
    Can be run via: uvx copilot-repository-tools-web
    """
    import sys
    from pathlib import Path
    
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(
        description="Start the Copilot Chat Archive web server",
    )
    parser.add_argument(
        "--db", "-d",
        default="copilot_chats.db",
        help="Path to SQLite database file (default: copilot_chats.db)",
    )
    parser.add_argument(
        "--host", "-H",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=5000,
        help="Port to bind to (default: 5000)",
    )
    parser.add_argument(
        "--title", "-t",
        default="Copilot Chat Archive",
        help="Title for the archive (default: Copilot Chat Archive)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode",
    )
    
    args = parser.parse_args()
    
    # Check if database exists
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: Database file '{args.db}' not found.", file=sys.stderr)
        print("Run 'copilot-chat-archive scan' first to import chat sessions.", file=sys.stderr)
        sys.exit(1)
    
    from copilot_repository_tools_common import Database
    database = Database(args.db)
    stats = database.get_stats()
    
    if stats["session_count"] == 0:
        print("Warning: Database is empty. Run 'copilot-chat-archive scan' first.", file=sys.stderr)
    
    print(f"Starting web server...")
    print(f"  Database: {args.db}")
    print(f"  Sessions: {stats['session_count']}")
    print(f"  Messages: {stats['message_count']}")
    print(f"\nOpen http://{args.host}:{args.port}/ in a browser to view your archive.")
    print("Press Ctrl+C to stop the server.\n")
    
    run_server(
        host=args.host,
        port=args.port,
        db_path=args.db,
        title=args.title,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
