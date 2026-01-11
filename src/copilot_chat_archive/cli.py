"""Command-line interface for Copilot Chat Archive."""

import sys
from datetime import datetime
from pathlib import Path

import click

from . import __version__
from .database import Database
from .scanner import get_vscode_storage_paths, scan_chat_sessions


def format_timestamp(ts: str | int | None) -> str:
    """Convert a timestamp to a human-readable date string."""
    if ts is None:
        return "Unknown"
    try:
        # Try parsing as milliseconds (JS timestamp)
        if isinstance(ts, str):
            ts = int(ts)
        if ts > 1e12:  # Milliseconds
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        # Return as-is if parsing fails
        return str(ts)


@click.group()
@click.version_option(version=__version__)
def main():
    """Copilot Chat Archive - Create a searchable archive of VS Code GitHub Copilot chats."""
    pass


@main.command()
@click.option(
    "--db",
    "-d",
    default="copilot_chats.db",
    help="Path to SQLite database file.",
    type=click.Path(dir_okay=False),
)
@click.option(
    "--storage-path",
    "-s",
    multiple=True,
    help="Custom VS Code storage path(s) to scan. Can be specified multiple times.",
    type=click.Path(exists=True, file_okay=False),
)
@click.option(
    "--edition",
    "-e",
    default="both",
    type=click.Choice(["stable", "insider", "both"]),
    help="VS Code edition to scan.",
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output.")
@click.option("--full", "-f", is_flag=True, help="Full scan: update all sessions regardless of file changes.")
def scan(db: str, storage_path: tuple, edition: str, verbose: bool, full: bool):
    """Scan for and import Copilot chat sessions into the database.
    
    By default, uses incremental refresh: only updates sessions whose source files
    have changed (based on file mtime and size). Use --full to force a complete
    re-import of all sessions.
    """
    database = Database(db)

    # Determine storage paths
    if storage_path:
        # Use custom paths
        paths = [(str(p), "custom") for p in storage_path]
    else:
        # Use default paths based on edition
        all_paths = get_vscode_storage_paths()
        if edition == "both":
            paths = all_paths
        else:
            paths = [(p, e) for p, e in all_paths if e == edition]

    click.echo(f"Scanning for Copilot chat sessions...")
    if full:
        click.echo("  (Full mode: will update all sessions)")
    else:
        click.echo("  (Incremental mode: skipping unchanged sessions)")
    if verbose:
        for path, ed in paths:
            click.echo(f"  Checking: {path} ({ed})")

    added = 0
    updated = 0
    skipped = 0

    def log_session_action(action: str, session):
        """Log a session action if verbose mode is enabled."""
        if verbose:
            workspace = session.workspace_name or "Unknown workspace"
            click.echo(f"  {action}: {workspace} ({len(session.messages)} messages)")

    for session in scan_chat_sessions(paths):
        if full:
            # In full mode, update all sessions
            existing = database.get_session(session.session_id)
            if existing:
                database.update_session(session)
                updated += 1
                log_session_action("Updated", session)
            else:
                database.add_session(session)
                added += 1
                log_session_action("Added", session)
        else:
            # Incremental mode: use needs_update() to determine if session should be updated
            if database.needs_update(session.session_id, session.source_file_mtime, session.source_file_size):
                existing = database.get_session(session.session_id)
                if existing:
                    database.update_session(session)
                    updated += 1
                    log_session_action("Updated", session)
                else:
                    database.add_session(session)
                    added += 1
                    log_session_action("Added", session)
            else:
                skipped += 1
                if verbose:
                    workspace = session.workspace_name or "Unknown workspace"
                    click.echo(f"  Skipped (unchanged): {workspace}")

    click.echo(f"\nImport complete:")
    click.echo(f"  Added: {added} sessions")
    click.echo(f"  Updated: {updated} sessions")
    click.echo(f"  Skipped (unchanged): {skipped} sessions")

    stats = database.get_stats()
    click.echo(f"\nDatabase now contains:")
    click.echo(f"  {stats['session_count']} sessions")
    click.echo(f"  {stats['message_count']} messages")
    click.echo(f"  {stats['workspace_count']} workspaces")


@main.command()
@click.option(
    "--db",
    "-d",
    default="copilot_chats.db",
    help="Path to SQLite database file.",
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--host",
    "-h",
    default="127.0.0.1",
    help="Host to bind to.",
)
@click.option(
    "--port",
    "-p",
    default=5000,
    help="Port to bind to.",
    type=int,
)
@click.option(
    "--title",
    "-t",
    default="Copilot Chat Archive",
    help="Title for the archive.",
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug mode.",
)
def serve(db: str, host: str, port: int, title: str, debug: bool):
    """Start the web server to browse chat sessions."""
    if not Path(db).exists():
        click.echo(f"Error: Database file '{db}' not found.", err=True)
        click.echo("Run 'copilot-chat-archive scan' first to import chat sessions.", err=True)
        sys.exit(1)

    from .webapp import run_server

    database = Database(db)
    stats = database.get_stats()

    if stats["session_count"] == 0:
        click.echo("Warning: Database is empty. Run 'copilot-chat-archive scan' first.", err=True)

    click.echo(f"Starting web server...")
    click.echo(f"  Database: {db}")
    click.echo(f"  Sessions: {stats['session_count']}")
    click.echo(f"  Messages: {stats['message_count']}")
    click.echo(f"\nOpen http://{host}:{port}/ in a browser to view your archive.")
    click.echo("Press Ctrl+C to stop the server.\n")

    run_server(host=host, port=port, db_path=db, title=title, debug=debug)


@main.command()
@click.option(
    "--db",
    "-d",
    default="copilot_chats.db",
    help="Path to SQLite database file.",
    type=click.Path(exists=True, dir_okay=False),
)
@click.argument("query")
@click.option(
    "--limit",
    "-l",
    default=20,
    help="Maximum number of results to show.",
)
@click.option(
    "--role",
    "-r",
    type=click.Choice(["user", "assistant"]),
    help="Filter by message role (user requests or assistant responses).",
)
@click.option(
    "--title",
    "-t",
    help="Filter by session title or workspace name.",
)
@click.option(
    "--no-tools",
    is_flag=True,
    help="Exclude tool invocations from search results.",
)
@click.option(
    "--no-files",
    is_flag=True,
    help="Exclude file changes from search results.",
)
@click.option(
    "--tools-only",
    is_flag=True,
    help="Only search in tool invocations.",
)
@click.option(
    "--files-only",
    is_flag=True,
    help="Only search in file changes.",
)
@click.option(
    "--full",
    "-F",
    is_flag=True,
    help="Show full content instead of truncated snippets.",
)
def search(
    db: str,
    query: str,
    limit: int,
    role: str | None,
    title: str | None,
    no_tools: bool,
    no_files: bool,
    tools_only: bool,
    files_only: bool,
    full: bool,
):
    """Search chat messages in the database.
    
    By default, searches message content, tool invocations, and file changes.
    Use --role to filter by user requests or assistant responses.
    Use --title to filter by session/workspace name.
    Use --no-tools or --no-files to exclude specific content types.
    Use --tools-only or --files-only to search only specific content types.
    Use --full to show complete content instead of truncated snippets.
    """
    if not Path(db).exists():
        click.echo(click.style(f"Error: Database file '{db}' not found.", fg="red"), err=True)
        sys.exit(1)

    # Handle search mode options
    include_messages = True
    include_tool_calls = not no_tools
    include_file_changes = not no_files

    if tools_only:
        # Search only in tool invocations
        include_messages = False
        include_file_changes = False
        include_tool_calls = True
    elif files_only:
        # Search only in file changes
        include_messages = False
        include_tool_calls = False
        include_file_changes = True

    database = Database(db)
    results = database.search(
        query,
        limit=limit,
        role=role,
        include_messages=include_messages,
        include_tool_calls=include_tool_calls,
        include_file_changes=include_file_changes,
        session_title=title,
    )

    if not results:
        click.echo(click.style(f"No results found for '{query}'", fg="yellow"))
        return

    click.echo(click.style(f"Found {len(results)} result(s) for '{query}':\n", fg="green", bold=True))

    for i, result in enumerate(results, 1):
        # Header with result number
        click.echo(click.style(f"━━━ Result {i} ━━━", fg="cyan", bold=True))
        
        # Session ID (full)
        click.echo(click.style("Session ID: ", fg="bright_blue", bold=True) + click.style(result['session_id'], fg="white"))
        
        # Workspace name
        if result.get("workspace_name"):
            click.echo(click.style("Workspace:  ", fg="bright_blue", bold=True) + click.style(result['workspace_name'], fg="yellow"))
        
        # Custom title
        if result.get("custom_title"):
            click.echo(click.style("Title:      ", fg="bright_blue", bold=True) + result['custom_title'])
        
        # Date
        if result.get("created_at"):
            formatted_date = format_timestamp(result['created_at'])
            click.echo(click.style("Date:       ", fg="bright_blue", bold=True) + click.style(formatted_date, fg="bright_black"))
        
        # Role with color coding
        role_color = "green" if result['role'] == "user" else "magenta"
        click.echo(click.style("Role:       ", fg="bright_blue", bold=True) + click.style(result['role'], fg=role_color))
        
        # Match type if not a regular message
        if result.get("match_type") and result["match_type"] != "message":
            click.echo(click.style("Match Type: ", fg="bright_blue", bold=True) + click.style(result['match_type'], fg="cyan"))

        # Content
        content = result["content"]
        if not full and len(content) > 200:
            content = content[:200] + click.style("... (use --full to see more)", fg="bright_black")
        click.echo(click.style("Content:    ", fg="bright_blue", bold=True) + content)
        click.echo()


@main.command()
@click.option(
    "--db",
    "-d",
    default="copilot_chats.db",
    help="Path to SQLite database file.",
    type=click.Path(exists=True, dir_okay=False),
)
def stats(db: str):
    """Show database statistics."""
    if not Path(db).exists():
        click.echo(f"Error: Database file '{db}' not found.", err=True)
        sys.exit(1)

    database = Database(db)
    stats_data = database.get_stats()

    click.echo("Database Statistics:")
    click.echo(f"  Sessions: {stats_data['session_count']}")
    click.echo(f"  Messages: {stats_data['message_count']}")
    click.echo(f"  Workspaces: {stats_data['workspace_count']}")

    if stats_data["editions"]:
        click.echo("\n  By VS Code Edition:")
        for edition, count in stats_data["editions"].items():
            click.echo(f"    {edition}: {count}")

    # Show workspaces
    workspaces = database.get_workspaces()
    if workspaces:
        click.echo("\n  Workspaces:")
        for ws in workspaces[:10]:
            click.echo(f"    {ws['workspace_name']}: {ws['session_count']} sessions")
        if len(workspaces) > 10:
            click.echo(f"    ... and {len(workspaces) - 10} more")


@main.command()
@click.option(
    "--db",
    "-d",
    default="copilot_chats.db",
    help="Path to SQLite database file.",
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--output",
    "-o",
    default="-",
    help="Output file (- for stdout).",
    type=click.Path(dir_okay=False),
)
def export(db: str, output: str):
    """Export the database as JSON."""
    if not Path(db).exists():
        click.echo(f"Error: Database file '{db}' not found.", err=True)
        sys.exit(1)

    database = Database(db)
    json_data = database.export_json()

    if output == "-":
        click.echo(json_data)
    else:
        Path(output).write_text(json_data, encoding="utf-8")
        click.echo(f"Exported to {output}")


@main.command()
@click.option(
    "--db",
    "-d",
    default="copilot_chats.db",
    help="Path to SQLite database file.",
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--output-dir",
    "-o",
    default=".",
    help="Output directory for markdown files.",
    type=click.Path(file_okay=False),
)
@click.option(
    "--session-id",
    "-s",
    help="Export only a specific session by ID.",
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output.")
@click.option(
    "--include-diffs/--no-diffs",
    default=False,
    help="Include file diffs as code blocks in the markdown output.",
)
@click.option(
    "--include-tool-inputs/--no-tool-inputs",
    default=False,
    help="Include tool inputs as code blocks in the markdown output.",
)
def export_markdown(
    db: str,
    output_dir: str,
    session_id: str | None,
    verbose: bool,
    include_diffs: bool,
    include_tool_inputs: bool,
):
    """Export sessions as markdown files.
    
    Each session is exported to a separate markdown file with:
    - Header block with metadata (session ID, workspace, dates)
    - Messages separated by horizontal rules
    - Message numbers and roles as bold headers
    - Tool call summaries in italics
    - Thinking block notices in italics (content omitted)
    
    Use --include-diffs to add file change diffs as code blocks.
    Use --include-tool-inputs to add tool inputs as code blocks.
    """
    from .markdown_exporter import (
        session_to_markdown,
        export_session_to_file,
        generate_session_filename,
    )

    database = Database(db)
    
    # Create output directory if it doesn't exist
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    if session_id:
        # Export a specific session
        session = database.get_session(session_id)
        if session is None:
            click.echo(f"Error: Session '{session_id}' not found.", err=True)
            sys.exit(1)
        
        filename = generate_session_filename(session)
        file_path = output_path / filename
        export_session_to_file(
            session,
            file_path,
            include_diffs=include_diffs,
            include_tool_inputs=include_tool_inputs,
        )
        click.echo(f"Exported: {file_path}")
    else:
        # Export all sessions
        sessions = database.list_sessions()
        exported = 0
        
        for session_info in sessions:
            session = database.get_session(session_info["session_id"])
            if session:
                filename = generate_session_filename(session)
                file_path = output_path / filename
                export_session_to_file(
                    session,
                    file_path,
                    include_diffs=include_diffs,
                    include_tool_inputs=include_tool_inputs,
                )
                exported += 1
                if verbose:
                    click.echo(f"  Exported: {file_path}")
        
        click.echo(f"\nExported {exported} sessions to {output_path}/")


@main.command()
@click.option(
    "--db",
    "-d",
    default="copilot_chats.db",
    help="Path to SQLite database file.",
    type=click.Path(dir_okay=False),
)
@click.argument("json_file", type=click.Path(exists=True, dir_okay=False))
def import_json(db: str, json_file: str):
    """Import sessions from a JSON file."""
    import json

    from .scanner import ChatMessage, ChatSession

    database = Database(db)

    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        click.echo("Error: JSON file must contain an array of sessions.", err=True)
        sys.exit(1)

    added = 0
    skipped = 0

    for item in data:
        if not isinstance(item, dict):
            continue

        messages = [
            ChatMessage(
                role=m.get("role", "unknown"),
                content=m.get("content", ""),
                timestamp=m.get("timestamp"),
            )
            for m in item.get("messages", [])
        ]

        session = ChatSession(
            session_id=item.get("session_id", str(hash(str(item)))),
            workspace_name=item.get("workspace_name"),
            workspace_path=item.get("workspace_path"),
            messages=messages,
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
            source_file=json_file,
            vscode_edition=item.get("vscode_edition", "imported"),
        )

        if database.add_session(session):
            added += 1
        else:
            skipped += 1

    click.echo(f"Import complete:")
    click.echo(f"  Added: {added} sessions")
    click.echo(f"  Skipped: {skipped} sessions")


@main.command()
@click.option(
    "--db",
    "-d",
    default="copilot_chats.db",
    help="Path to SQLite database file.",
    type=click.Path(exists=True, dir_okay=False),
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output.")
def rebuild(db: str, verbose: bool):
    """Rebuild derived tables from raw JSON data.
    
    This command drops all derived tables (sessions, messages, tool_invocations,
    file_changes, command_runs, content_blocks) and recreates them from the
    compressed raw JSON stored in raw_sessions.
    
    Use this after schema changes to regenerate all derived data without needing
    to re-scan the original VS Code storage.
    """
    if not Path(db).exists():
        click.echo(f"Error: Database file '{db}' not found.", err=True)
        sys.exit(1)

    database = Database(db)
    
    # Check if there are any raw sessions to rebuild from
    raw_count = database.get_raw_session_count()
    if raw_count == 0:
        click.echo("Warning: No raw sessions found in database.", err=True)
        click.echo("Run 'copilot-chat-archive scan' first to import sessions.", err=True)
        sys.exit(1)

    click.echo(f"Rebuilding {raw_count} sessions from raw JSON...")
    
    def progress_callback(processed, total):
        if verbose:
            click.echo(f"  Processed: {processed}/{total}")
    
    result = database.rebuild_derived_tables(
        progress_callback=progress_callback if verbose else None
    )
    
    click.echo(f"\nRebuild complete:")
    click.echo(f"  Processed: {result['processed']} sessions")
    if result['errors'] > 0:
        click.echo(f"  Errors: {result['errors']} sessions")
    
    stats = database.get_stats()
    click.echo(f"\nDatabase now contains:")
    click.echo(f"  {stats['session_count']} sessions")
    click.echo(f"  {stats['message_count']} messages")
    click.echo(f"  {stats['workspace_count']} workspaces")


if __name__ == "__main__":
    main()
