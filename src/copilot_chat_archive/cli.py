"""Command-line interface for Copilot Chat Archive."""

import sys
from datetime import datetime
from pathlib import Path

import click

from . import __version__
from .database import Database
from .scanner import get_vscode_storage_paths, scan_chat_sessions
from .viewer import generate_html


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
@click.option("--force", "-f", is_flag=True, help="Force re-import of existing sessions (updates changed sessions).")
def scan(db: str, storage_path: tuple, edition: str, verbose: bool, force: bool):
    """Scan for and import Copilot chat sessions into the database."""
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
    if force:
        click.echo("  (Force mode: will update existing sessions)")
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
        if force:
            # In force mode, update existing sessions
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
            # Normal mode: skip existing sessions
            if database.add_session(session):
                added += 1
                log_session_action("Added", session)
            else:
                skipped += 1

    click.echo(f"\nImport complete:")
    click.echo(f"  Added: {added} sessions")
    if force:
        click.echo(f"  Updated: {updated} sessions")
    else:
        click.echo(f"  Skipped (already exists): {skipped} sessions")

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
    "--output",
    "-o",
    default="./archive",
    help="Output directory for HTML files.",
    type=click.Path(file_okay=False),
)
@click.option(
    "--title",
    "-t",
    default="Copilot Chat Archive",
    help="Title for the archive.",
)
def generate(db: str, output: str, title: str):
    """Generate static HTML files from the database."""
    if not Path(db).exists():
        click.echo(f"Error: Database file '{db}' not found.", err=True)
        click.echo("Run 'copilot-chat-archive scan' first to import chat sessions.", err=True)
        sys.exit(1)

    database = Database(db)
    stats = database.get_stats()

    if stats["session_count"] == 0:
        click.echo("Warning: Database is empty. Run 'copilot-chat-archive scan' first.", err=True)

    click.echo(f"Generating HTML archive...")
    index_path = generate_html(database, output, title)

    click.echo(f"\nArchive generated successfully!")
    click.echo(f"  Output directory: {output}")
    click.echo(f"  Index file: {index_path}")
    click.echo(f"\nOpen {index_path} in a browser to view your archive.")


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


if __name__ == "__main__":
    main()
