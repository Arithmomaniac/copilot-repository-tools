"""Command-line interface for Copilot Session Tools.

This module provides a modern CLI built with Typer for scanning, searching,
and exporting VS Code GitHub Copilot chat history.
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from copilot_session_tools import (
    ChatMessage,
    ChatSession,
    Database,
    __version__,
    export_session_to_file,
    export_session_to_html_file,
    generate_session_filename,
    generate_session_html_filename,
    get_vscode_storage_paths,
)
from copilot_session_tools.refresh import enrich_single_session, run_enrichment, run_refresh

# On Windows, reconfigure stdout/stderr to UTF-8 when piped to prevent
# Rich from falling back to cp1252 which can't handle Unicode output
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # ty: ignore[call-non-callable]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # ty: ignore[call-non-callable]


def _default_db_path() -> Path:
    """Return the default database path: ~/.copilot/session-store.db"""
    return Path.home() / ".copilot" / "session-store.db"


_DEFAULT_DB = Path.home() / ".copilot" / "session-store.db"


def _ensure_db_exists(db: Path) -> None:
    """Check that the database file exists, with a friendly error if not."""
    try:
        if not db.exists():
            typer.echo(f"Error: Session store database not found at {db}", err=True)
            typer.echo(
                "The Copilot CLI must be installed and used at least once to create this database.",
                err=True,
            )
            raise typer.Exit(code=2)
    except FileNotFoundError:
        typer.echo(f"Error: Session store database not found at {db}", err=True)
        typer.echo(
            "The Copilot CLI must be installed and used at least once to create this database.",
            err=True,
        )
        raise typer.Exit(code=2) from None


app = typer.Typer(
    name="copilot-session-tools",
    help="Create a searchable archive of VS Code GitHub Copilot chats.",
    no_args_is_help=True,
)
console = Console()

# Module-level state set by the app callback
_unenriched_only: bool = False


def version_callback(value: bool):
    """Print version and exit."""
    if value:
        console.print(f"copilot-session-tools version {__version__}")
        raise typer.Exit()


def format_timestamp(ts: str | int | None) -> str:
    """Convert a timestamp to a human-readable date string."""
    if ts is None:
        return "Unknown"
    try:
        # Try parsing as milliseconds (JS timestamp) - int() accepts both strings and ints
        numeric_ts = int(ts) if isinstance(ts, str) else ts
        if numeric_ts > 1e12:  # Milliseconds
            numeric_ts = numeric_ts / 1000
        return datetime.fromtimestamp(numeric_ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return str(ts)


def _print_upgrade_notice():
    """Print a notice if a newer version is available on PyPI."""
    try:
        from copilot_session_tools.version_check import check_for_upgrade

        latest = check_for_upgrade()
        if latest:
            console.print(f"\n[yellow]💡 A newer version (v{latest}) is available. Upgrade with: pip install --upgrade copilot-session-tools[/yellow]")
    except Exception:  # noqa: S110
        pass


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
    unenriched_only: Annotated[
        bool,
        typer.Option(
            "--unenriched-only",
            help="Disable cst_* table reads; use built-in tables only.",
        ),
    ] = False,
):
    """Copilot Session Tools - Create a searchable archive of VS Code GitHub Copilot chats."""
    global _unenriched_only  # noqa: PLW0603
    _unenriched_only = unenriched_only


@app.command()
def scan(
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            "-d",
            help="Path to SQLite database file.",
        ),
    ] = _DEFAULT_DB,
    storage_path: Annotated[
        list[Path] | None,
        typer.Option(
            "--storage-path",
            "-s",
            help="Custom VS Code storage path(s) to scan. Can be specified multiple times.",
            exists=True,
            file_okay=False,
        ),
    ] = None,
    edition: Annotated[
        str,
        typer.Option(
            "--edition",
            "-e",
            help="VS Code edition to scan.",
        ),
    ] = "both",
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show verbose output.",
        ),
    ] = False,
    full: Annotated[
        bool,
        typer.Option(
            "--full",
            "-f",
            help="Full scan: update all sessions regardless of file changes.",
        ),
    ] = False,
):
    """Scan for and import Copilot chat sessions, enriching them into the built-in store.

    By default, uses incremental refresh: only updates sessions whose source files
    have changed (based on file mtime and size). Use --full to force a complete
    re-import of all sessions.
    """
    if edition not in ("stable", "insider", "both"):
        console.print("[red]Error: edition must be 'stable', 'insider', or 'both'[/red]")
        raise typer.Exit(1)

    db.parent.mkdir(parents=True, exist_ok=True)
    database = Database(db, unenriched_only=_unenriched_only)

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

    def _verbose_progress(event: str, item: object) -> None:
        from copilot_session_tools.scanner.models import ChatSession as _CS
        from copilot_session_tools.scanner.models import SessionFileInfo as _SFI

        if isinstance(item, _CS):
            workspace = item.workspace_name or "Unknown workspace"
            console.print(f"  {event.capitalize()}: {workspace} ({len(item.messages)} messages)")
        elif isinstance(item, _SFI):
            workspace = item.workspace_name or "Unknown workspace"
            console.print(f"  Skipped (unchanged): {workspace}")
        elif event == "enriched":
            console.print(f"  Enriched: {item}")
        elif event == "reparsed":
            console.print(f"  Reparsed: {item}")
        elif event == "enrich_failed":
            console.print(f"  [yellow]{item}[/yellow]")

    progress_cb = _verbose_progress if verbose else None

    result = run_refresh(database, paths, full=full, on_progress=progress_cb)
    added = result.added
    updated = result.updated
    skipped = result.skipped

    console.print("\n[green]VS Code import complete:[/green]")
    console.print(f"  Added: {added} sessions")
    console.print(f"  Updated: {updated} sessions")
    console.print(f"  Skipped (unchanged): {skipped} sessions")

    # --- CLI session enrichment from Chronicle's built-in session store ---
    console.print("\n[cyan]Enriching CLI sessions...[/cyan]")
    enrich_result = run_enrichment(database, on_progress=progress_cb)

    console.print(f"  Enriched: {enrich_result.enriched} sessions")
    if enrich_result.reparsed:
        console.print(f"  Reparsed: {enrich_result.reparsed} sessions (parser version upgrade)")
    if enrich_result.failed:
        console.print(f"  Skipped (no events file or parse error): {enrich_result.failed} sessions")
    if enrich_result.orphaned:
        console.print(f"\n[yellow]Cleaned up {enrich_result.orphaned} orphaned session(s)[/yellow]")

    stats = database.get_stats()
    console.print("\n[cyan]Database now contains:[/cyan]")
    console.print(f"  {stats['session_count']} sessions")
    console.print(f"  {stats['message_count']} messages")
    console.print(f"  {stats['workspace_count']} workspaces")

    _print_upgrade_notice()


@app.command()
def enrich(
    session_id: str = typer.Argument(..., help="Session ID to enrich"),
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            "-d",
            help="Path to SQLite database file.",
        ),
    ] = _DEFAULT_DB,
):
    """Enrich a single CLI session from its events.jsonl file."""
    _ensure_db_exists(db)
    database = Database(db, unenriched_only=_unenriched_only)

    error = enrich_single_session(database, session_id)
    if error:
        console.print(f"[red]Error: {error}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Successfully enriched session {session_id}[/green]")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query.")],
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            "-d",
            help="Path to SQLite database file.",
        ),
    ] = _DEFAULT_DB,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            "-l",
            "--top",
            help="Maximum number of results to show (top).",
        ),
    ] = 20,
    skip: Annotated[
        int,
        typer.Option(
            "--skip",
            help="Number of results to skip (for pagination).",
        ),
    ] = 0,
    role: Annotated[
        str | None,
        typer.Option(
            "--role",
            "-r",
            help="Filter by message role (user or assistant).",
        ),
    ] = None,
    title_filter: Annotated[
        str | None,
        typer.Option(
            "--title",
            "-t",
            help="Filter by session title or workspace name.",
        ),
    ] = None,
    repository_filter: Annotated[
        str | None,
        typer.Option(
            "--repository",
            "--repo",
            help="Filter by repository URL (e.g., github.com/owner/repo).",
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
            "--full",
            "-F",
            help="Show full content instead of truncated snippets.",
        ),
    ] = False,
    sort_by: Annotated[
        str,
        typer.Option(
            "--sort",
            "-s",
            help="Sort results by relevance (default) or date.",
        ),
    ] = "relevance",
):
    """Search chat messages in the database.

    Supports advanced query syntax:

    \b
    - Multiple words: "python function" matches both words (AND logic)
    - Exact phrases: Use quotes like "python function" for exact match
    - Field filters in query: role:user, role:assistant, workspace:name, title:name, repository:url (or repo:url)
    - Date filters: start_date:2024-01-01 end_date:2024-12-31 (yyyy-mm-dd format, inclusive)

    Examples:

    \b
      copilot-session-tools search "python function"
      copilot-session-tools search "role:user python"
      copilot-session-tools search "workspace:my-project"
      copilot-session-tools search "repo:github.com/owner/repo"
      copilot-session-tools search "start_date:2024-01-01 end_date:2024-06-30"
      copilot-session-tools search '"exact phrase"'

    Use --role to filter by user requests or assistant responses.
    Use --title to filter by session/workspace name.
    Use --repository to filter by git repository URL.
    Use --skip and --limit/--top for pagination.
    Use --no-tools or --no-files to exclude specific content types.
    Use --tools-only or --files-only to search only specific content types.
    Use --full to show complete content instead of truncated snippets.
    Use --sort to sort by relevance (default) or date.
    """
    _ensure_db_exists(db)
    if role and role not in ("user", "assistant"):
        console.print("[red]Error: role must be 'user' or 'assistant'[/red]")
        raise typer.Exit(1)

    if sort_by not in ("relevance", "date"):
        console.print("[red]Error: sort must be 'relevance' or 'date'[/red]")
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

    database = Database(db, unenriched_only=_unenriched_only)
    results = database.search(
        query,
        limit=limit,
        skip=skip,
        role=role,
        include_messages=include_messages,
        include_tool_calls=include_tool_calls,
        include_file_changes=include_file_changes,
        session_title=title_filter,
        sort_by=sort_by,
        repository=repository_filter,
    )

    if not results:
        console.print(f"[yellow]No results found for '{query}'[/yellow]")
        return

    # Display result count with pagination info
    start_num = skip + 1
    end_num = skip + len(results)
    console.print(f"[green bold]Showing results {start_num}-{end_num} for '{query}':[/green bold]\n")

    for i, result in enumerate(results, start_num):
        console.print(f"[cyan bold]━━━ Result {i} ━━━[/cyan bold]")
        console.print(f"[bright_blue bold]Session ID:[/bright_blue bold] {result['session_id']}")

        if result.get("workspace_name"):
            console.print(f"[bright_blue bold]Workspace:[/bright_blue bold]  [yellow]{result['workspace_name']}[/yellow]")

        if result.get("custom_title"):
            console.print(f"[bright_blue bold]Title:[/bright_blue bold]      {result['custom_title']}")

        if result.get("created_at"):
            formatted_date = format_timestamp(result["created_at"])
            console.print(f"[bright_blue bold]Date:[/bright_blue bold]       [dim]{formatted_date}[/dim]")

        role_color = "green" if result["role"] == "user" else "magenta"
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
            "--db",
            "-d",
            help="Path to SQLite database file.",
        ),
    ] = _DEFAULT_DB,
):
    """Show database statistics."""
    _ensure_db_exists(db)
    database = Database(db, unenriched_only=_unenriched_only)
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

    repositories = database.get_repositories()
    if repositories:
        console.print("\n  [green]Repositories:[/green]")
        for repo in repositories[:10]:
            console.print(f"    {repo['repository_url']}: {repo['session_count']} sessions")
        if len(repositories) > 10:
            console.print(f"    ... and {len(repositories) - 10} more")


@app.command()
def export(
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            "-d",
            help="Path to SQLite database file.",
        ),
    ] = _DEFAULT_DB,
    output: Annotated[
        str,
        typer.Option(
            "--output",
            "-o",
            help="Output file (- for stdout).",
        ),
    ] = "-",
):
    """Export the database as JSON."""
    _ensure_db_exists(db)
    database = Database(db, unenriched_only=_unenriched_only)
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
            "--db",
            "-d",
            help="Path to SQLite database file.",
        ),
    ] = _DEFAULT_DB,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            "-o",
            help="Output directory for markdown files.",
        ),
    ] = Path(),
    session_id: Annotated[
        str | None,
        typer.Option(
            "--session-id",
            "-s",
            help="Export only a specific session by ID.",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
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
    include_thinking: Annotated[
        bool,
        typer.Option(
            "--include-thinking/--no-thinking",
            help="Include thinking/reasoning block content in the markdown output.",
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
    _ensure_db_exists(db)
    database = Database(db, unenriched_only=_unenriched_only)

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
            include_thinking=include_thinking,
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
                    include_thinking=include_thinking,
                )
                exported += 1
                if verbose:
                    console.print(f"  Exported: {file_path}")

        console.print(f"\n[green]Exported {exported} sessions to {output_dir}/[/green]")


@app.command("export-html")
def export_html(
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            "-d",
            help="Path to SQLite database file.",
        ),
    ] = _DEFAULT_DB,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            "-o",
            help="Output directory for HTML files.",
        ),
    ] = Path(),
    session_id: Annotated[
        str | None,
        typer.Option(
            "--session-id",
            "-s",
            help="Export only a specific session by ID.",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show verbose output.",
        ),
    ] = False,
):
    """Export sessions as self-contained static HTML files.

    Each session is exported to a separate HTML file with the same rich
    rendering as the web viewer, but without interactive elements (toolbar,
    copy buttons, AJAX). The HTML is self-contained with no external
    dependencies.
    """
    _ensure_db_exists(db)
    database = Database(db, unenriched_only=_unenriched_only)

    output_dir.mkdir(parents=True, exist_ok=True)

    if session_id:
        session = database.get_session(session_id)
        if session is None:
            console.print(f"[red]Error: Session '{session_id}' not found.[/red]")
            raise typer.Exit(1)

        filename = generate_session_html_filename(session)
        file_path = output_dir / filename
        export_session_to_html_file(session, file_path)
        console.print(f"[green]Exported: {file_path}[/green]")
    else:
        sessions = database.list_sessions()
        exported = 0

        for session_info in sessions:
            session = database.get_session(session_info["session_id"])
            if session:
                filename = generate_session_html_filename(session)
                file_path = output_dir / filename
                export_session_to_html_file(session, file_path)
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
            "--db",
            "-d",
            help="Path to SQLite database file.",
        ),
    ] = _DEFAULT_DB,
):
    """Import sessions from a JSON file."""
    import json

    db.parent.mkdir(parents=True, exist_ok=True)
    database = Database(db, unenriched_only=_unenriched_only)

    with json_file.open(encoding="utf-8") as f:
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


@app.command()
def optimize(
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            "-d",
            help="Path to SQLite database file.",
        ),
    ] = _DEFAULT_DB,
):
    """Optimize the full-text search index for better query performance.

    This command merges FTS5 index segments, reducing fragmentation and
    improving search speed. Recommended to run periodically, especially
    after bulk imports.

    The optimization process:
    1. Merges all FTS index segments into fewer, larger segments
    2. Runs an integrity check to verify index consistency
    """
    _ensure_db_exists(db)
    database = Database(db, unenriched_only=_unenriched_only)

    console.print("Optimizing FTS5 search index...")

    result = database.optimize_fts()

    console.print("\n[green]Optimization complete:[/green]")
    console.print(f"  Index segments before: {result['segments_before']}")
    console.print(f"  Index segments after:  {result['segments_after']}")

    if result["segments_before"] > result["segments_after"]:
        reduction = result["segments_before"] - result["segments_after"]
        console.print(f"  [cyan]Merged {reduction} segments for faster queries[/cyan]")
    else:
        console.print("  [dim]Index was already optimized[/dim]")


@app.command()
def web(
    db: Annotated[Path, typer.Option("--db", "-d", help="Path to SQLite database file.")] = _DEFAULT_DB,
    host: Annotated[str, typer.Option("--host", "-H", help="Host to bind to.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Port to bind to.")] = 5000,
    title: Annotated[str, typer.Option("--title", "-t", help="Title for the archive.")] = "Copilot Session Tools",
    debug: Annotated[bool, typer.Option("--debug", help="Enable debug mode.")] = False,
):
    """Start the web viewer for browsing chat sessions."""
    try:
        from copilot_session_tools.web import run_server
    except ImportError as err:
        console.print("[red]Web interface requires the [web] extra.[/red]")
        console.print("Install with: pip install copilot-session-tools[web]")
        raise typer.Exit(1) from err

    _ensure_db_exists(db)

    database = Database(str(db), unenriched_only=_unenriched_only)
    db_stats = database.get_stats()

    if db_stats["session_count"] == 0:
        console.print("[yellow]Warning: Database is empty. Run 'copilot-session-tools scan' first.[/yellow]")

    console.print("Starting web server...")
    console.print(f"  Database: {db}")
    console.print(f"  Sessions: {db_stats['session_count']}")
    console.print(f"  Messages: {db_stats['message_count']}")
    console.print(f"\nOpen http://{host}:{port}/ in a browser to view your archive.")
    console.print("Press Ctrl+C to stop the server.\n")

    _print_upgrade_notice()

    run_server(
        host=host,
        port=port,
        db_path=str(db),
        title=title,
        debug=debug,
    )


def run():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    run()
