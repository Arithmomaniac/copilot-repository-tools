"""Command-line interface for Copilot Repository Tools.

This module provides a modern CLI built with Typer for scanning, searching,
and exporting VS Code GitHub Copilot chat history.
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from copilot_repository_tools_common import (
    __version__,
    ChatMessage,
    ChatSession,
    Database,
    export_session_to_file,
    generate_session_filename,
    get_vscode_storage_paths,
    scan_chat_sessions,
)

app = typer.Typer(
    name="copilot-chat-archive",
    help="Create a searchable archive of VS Code GitHub Copilot chats.",
    no_args_is_help=True,
)
console = Console()


def version_callback(value: bool):
    """Print version and exit."""
    if value:
        console.print(f"copilot-chat-archive version {__version__}")
        raise typer.Exit()


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
        return str(ts)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", "-V",
            callback=version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
):
    """Copilot Chat Archive - Create a searchable archive of VS Code GitHub Copilot chats."""
    pass


@app.command()
def scan(
    db: Annotated[
        Path,
        typer.Option(
            "--db", "-d",
            help="Path to SQLite database file.",
        ),
    ] = Path("copilot_chats.db"),
    storage_path: Annotated[
        Optional[list[Path]],
        typer.Option(
            "--storage-path", "-s",
            help="Custom VS Code storage path(s) to scan. Can be specified multiple times.",
            exists=True,
            file_okay=False,
        ),
    ] = None,
    edition: Annotated[
        str,
        typer.Option(
            "--edition", "-e",
            help="VS Code edition to scan.",
        ),
    ] = "both",
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose", "-v",
            help="Show verbose output.",
        ),
    ] = False,
    full: Annotated[
        bool,
        typer.Option(
            "--full", "-f",
            help="Full scan: update all sessions regardless of file changes.",
        ),
    ] = False,
):
    """Scan for and import Copilot chat sessions into the database.
    
    By default, uses incremental refresh: only updates sessions whose source files
    have changed (based on file mtime and size). Use --full to force a complete
    re-import of all sessions.
    """
    if edition not in ("stable", "insider", "both"):
        console.print(f"[red]Error: edition must be 'stable', 'insider', or 'both'[/red]")
        raise typer.Exit(1)
    
    database = Database(db)

    # Determine storage paths
    if storage_path:
        paths = [(str(p), "custom") for p in storage_path]
    else:
        all_paths = get_vscode_storage_paths()
        if edition == "both":
            paths = all_paths
        else:
            paths = [(p, e) for p, e in all_paths if e == edition]

    console.print("Scanning for Copilot chat sessions...")
    if full:
        console.print("  (Full mode: will update all sessions)")
    else:
        console.print("  (Incremental mode: skipping unchanged sessions)")
    if verbose:
        for path, ed in paths:
            console.print(f"  Checking: {path} ({ed})")

    added = 0
    updated = 0
    skipped = 0

    for session in scan_chat_sessions(paths):
        if full:
            existing = database.get_session(session.session_id)
            if existing:
                database.update_session(session)
                updated += 1
                if verbose:
                    workspace = session.workspace_name or "Unknown workspace"
                    console.print(f"  Updated: {workspace} ({len(session.messages)} messages)")
            else:
                database.add_session(session)
                added += 1
                if verbose:
                    workspace = session.workspace_name or "Unknown workspace"
                    console.print(f"  Added: {workspace} ({len(session.messages)} messages)")
        else:
            if database.needs_update(session.session_id, session.source_file_mtime, session.source_file_size):
                existing = database.get_session(session.session_id)
                if existing:
                    database.update_session(session)
                    updated += 1
                    if verbose:
                        workspace = session.workspace_name or "Unknown workspace"
                        console.print(f"  Updated: {workspace} ({len(session.messages)} messages)")
                else:
                    database.add_session(session)
                    added += 1
                    if verbose:
                        workspace = session.workspace_name or "Unknown workspace"
                        console.print(f"  Added: {workspace} ({len(session.messages)} messages)")
            else:
                skipped += 1
                if verbose:
                    workspace = session.workspace_name or "Unknown workspace"
                    console.print(f"  Skipped (unchanged): {workspace}")

    console.print("\n[green]Import complete:[/green]")
    console.print(f"  Added: {added} sessions")
    console.print(f"  Updated: {updated} sessions")
    console.print(f"  Skipped (unchanged): {skipped} sessions")

    stats = database.get_stats()
    console.print("\n[cyan]Database now contains:[/cyan]")
    console.print(f"  {stats['session_count']} sessions")
    console.print(f"  {stats['message_count']} messages")
    console.print(f"  {stats['workspace_count']} workspaces")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query.")],
    db: Annotated[
        Path,
        typer.Option(
            "--db", "-d",
            help="Path to SQLite database file.",
            exists=True,
        ),
    ] = Path("copilot_chats.db"),
    limit: Annotated[
        int,
        typer.Option(
            "--limit", "-l",
            help="Maximum number of results to show.",
        ),
    ] = 20,
    role: Annotated[
        Optional[str],
        typer.Option(
            "--role", "-r",
            help="Filter by message role (user or assistant).",
        ),
    ] = None,
    title_filter: Annotated[
        Optional[str],
        typer.Option(
            "--title", "-t",
            help="Filter by session title or workspace name.",
        ),
    ] = None,
    no_tools: Annotated[
        bool,
        typer.Option(
            "--no-tools",
            help="Exclude tool invocations from search results.",
        ),
    ] = False,
    no_files: Annotated[
        bool,
        typer.Option(
            "--no-files",
            help="Exclude file changes from search results.",
        ),
    ] = False,
    tools_only: Annotated[
        bool,
        typer.Option(
            "--tools-only",
            help="Only search in tool invocations.",
        ),
    ] = False,
    files_only: Annotated[
        bool,
        typer.Option(
            "--files-only",
            help="Only search in file changes.",
        ),
    ] = False,
    full_content: Annotated[
        bool,
        typer.Option(
            "--full", "-F",
            help="Show full content instead of truncated snippets.",
        ),
    ] = False,
):
    """Search chat messages in the database.
    
    By default, searches message content, tool invocations, and file changes.
    """
    if role and role not in ("user", "assistant"):
        console.print("[red]Error: role must be 'user' or 'assistant'[/red]")
        raise typer.Exit(1)

    # Handle search mode options
    include_messages = True
    include_tool_calls = not no_tools
    include_file_changes = not no_files

    if tools_only:
        include_messages = False
        include_file_changes = False
        include_tool_calls = True
    elif files_only:
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
        session_title=title_filter,
    )

    if not results:
        console.print(f"[yellow]No results found for '{query}'[/yellow]")
        return

    console.print(f"[green bold]Found {len(results)} result(s) for '{query}':[/green bold]\n")

    for i, result in enumerate(results, 1):
        console.print(f"[cyan bold]━━━ Result {i} ━━━[/cyan bold]")
        console.print(f"[bright_blue bold]Session ID:[/bright_blue bold] {result['session_id']}")
        
        if result.get("workspace_name"):
            console.print(f"[bright_blue bold]Workspace:[/bright_blue bold]  [yellow]{result['workspace_name']}[/yellow]")
        
        if result.get("custom_title"):
            console.print(f"[bright_blue bold]Title:[/bright_blue bold]      {result['custom_title']}")
        
        if result.get("created_at"):
            formatted_date = format_timestamp(result['created_at'])
            console.print(f"[bright_blue bold]Date:[/bright_blue bold]       [dim]{formatted_date}[/dim]")
        
        role_color = "green" if result['role'] == "user" else "magenta"
        console.print(f"[bright_blue bold]Role:[/bright_blue bold]       [{role_color}]{result['role']}[/{role_color}]")
        
        if result.get("match_type") and result["match_type"] != "message":
            console.print(f"[bright_blue bold]Match Type:[/bright_blue bold] [cyan]{result['match_type']}[/cyan]")

        content = result["content"]
        if not full_content and len(content) > 200:
            content = content[:200] + "[dim]... (use --full to see more)[/dim]"
        console.print(f"[bright_blue bold]Content:[/bright_blue bold]    {content}")
        console.print()


@app.command()
def stats(
    db: Annotated[
        Path,
        typer.Option(
            "--db", "-d",
            help="Path to SQLite database file.",
            exists=True,
        ),
    ] = Path("copilot_chats.db"),
):
    """Show database statistics."""
    database = Database(db)
    stats_data = database.get_stats()

    console.print("[bold]Database Statistics:[/bold]")
    console.print(f"  Sessions: {stats_data['session_count']}")
    console.print(f"  Messages: {stats_data['message_count']}")
    console.print(f"  Workspaces: {stats_data['workspace_count']}")

    if stats_data["editions"]:
        console.print("\n  [cyan]By VS Code Edition:[/cyan]")
        for edition, count in stats_data["editions"].items():
            console.print(f"    {edition}: {count}")

    workspaces = database.get_workspaces()
    if workspaces:
        console.print("\n  [cyan]Workspaces:[/cyan]")
        for ws in workspaces[:10]:
            console.print(f"    {ws['workspace_name']}: {ws['session_count']} sessions")
        if len(workspaces) > 10:
            console.print(f"    ... and {len(workspaces) - 10} more")


@app.command()
def export(
    db: Annotated[
        Path,
        typer.Option(
            "--db", "-d",
            help="Path to SQLite database file.",
            exists=True,
        ),
    ] = Path("copilot_chats.db"),
    output: Annotated[
        str,
        typer.Option(
            "--output", "-o",
            help="Output file (- for stdout).",
        ),
    ] = "-",
):
    """Export the database as JSON."""
    database = Database(db)
    json_data = database.export_json()

    if output == "-":
        console.print(json_data)
    else:
        Path(output).write_text(json_data, encoding="utf-8")
        console.print(f"[green]Exported to {output}[/green]")


@app.command("export-markdown")
def export_markdown(
    db: Annotated[
        Path,
        typer.Option(
            "--db", "-d",
            help="Path to SQLite database file.",
            exists=True,
        ),
    ] = Path("copilot_chats.db"),
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir", "-o",
            help="Output directory for markdown files.",
        ),
    ] = Path("."),
    session_id: Annotated[
        Optional[str],
        typer.Option(
            "--session-id", "-s",
            help="Export only a specific session by ID.",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose", "-v",
            help="Show verbose output.",
        ),
    ] = False,
    include_diffs: Annotated[
        bool,
        typer.Option(
            "--include-diffs/--no-diffs",
            help="Include file diffs as code blocks in the markdown output.",
        ),
    ] = False,
    include_tool_inputs: Annotated[
        bool,
        typer.Option(
            "--include-tool-inputs/--no-tool-inputs",
            help="Include tool inputs as code blocks in the markdown output.",
        ),
    ] = False,
):
    """Export sessions as markdown files.
    
    Each session is exported to a separate markdown file with:
    - Header block with metadata (session ID, workspace, dates)
    - Messages separated by horizontal rules
    - Message numbers and roles as bold headers
    - Tool call summaries in italics
    - Thinking block notices in italics (content omitted)
    """
    database = Database(db)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if session_id:
        session = database.get_session(session_id)
        if session is None:
            console.print(f"[red]Error: Session '{session_id}' not found.[/red]")
            raise typer.Exit(1)
        
        filename = generate_session_filename(session)
        file_path = output_dir / filename
        export_session_to_file(
            session,
            file_path,
            include_diffs=include_diffs,
            include_tool_inputs=include_tool_inputs,
        )
        console.print(f"[green]Exported: {file_path}[/green]")
    else:
        sessions = database.list_sessions()
        exported = 0
        
        for session_info in sessions:
            session = database.get_session(session_info["session_id"])
            if session:
                filename = generate_session_filename(session)
                file_path = output_dir / filename
                export_session_to_file(
                    session,
                    file_path,
                    include_diffs=include_diffs,
                    include_tool_inputs=include_tool_inputs,
                )
                exported += 1
                if verbose:
                    console.print(f"  Exported: {file_path}")
        
        console.print(f"\n[green]Exported {exported} sessions to {output_dir}/[/green]")


@app.command("import-json")
def import_json(
    json_file: Annotated[
        Path,
        typer.Argument(
            help="JSON file to import.",
            exists=True,
        ),
    ],
    db: Annotated[
        Path,
        typer.Option(
            "--db", "-d",
            help="Path to SQLite database file.",
        ),
    ] = Path("copilot_chats.db"),
):
    """Import sessions from a JSON file."""
    import json

    database = Database(db)

    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        console.print("[red]Error: JSON file must contain an array of sessions.[/red]")
        raise typer.Exit(1)

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
            source_file=str(json_file),
            vscode_edition=item.get("vscode_edition", "imported"),
        )

        if database.add_session(session):
            added += 1
        else:
            skipped += 1

    console.print("[green]Import complete:[/green]")
    console.print(f"  Added: {added} sessions")
    console.print(f"  Skipped: {skipped} sessions")


def run():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    run()
