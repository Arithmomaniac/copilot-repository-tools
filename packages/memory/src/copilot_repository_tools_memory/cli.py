"""CLI for Copilot Repository Tools Memory.

This module provides a standalone CLI for semantic memory operations:
- setup: Initialize Mem0 with local resources (auto-runs on first use)
- index: Extract facts from chat sessions
- search: Semantic search through memories
- list: List all stored memories
- clear: Clear memories
- serve: Start a simple API server (optional, for web integration)

Memory Scoping:
- Memories are automatically scoped to repositories (if in a git repo)
  or to folders (if not in a git repo).
- Each scope is isolated: memories from one scope cannot affect another.
"""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .manager import MemoryManager, _get_scope_id, get_default_config

app = typer.Typer(
    name="copilot-memory",
    help="Semantic memory layer for Copilot chat history using Mem0.",
    no_args_is_help=True,
)
console = Console()

# Default data directory
DEFAULT_DATA_DIR = Path.home() / ".copilot-memory"


def _get_memory_manager(data_dir: Path | None = None) -> MemoryManager:
    """Get a MemoryManager instance, setting up if needed."""
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR

    data_dir.mkdir(parents=True, exist_ok=True)

    return MemoryManager(data_dir=data_dir)


@app.command("setup")
def setup_memory(
    data_dir: Annotated[
        Path,
        typer.Option("--data-dir", "-d", help="Directory for storing memory data."),
    ] = DEFAULT_DATA_DIR,
):
    """Initialize Mem0 with local resources.

    This sets up:
    - Local ChromaDB for vector storage
    - LiteLLM configuration for GitHub Copilot models

    This command is optional - setup happens automatically on first use.
    """
    data_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Setting up Mem0 in {data_dir}...[/cyan]")

    # Generate and display config
    config = get_default_config(data_dir)

    console.print("\n[green]✓ Mem0 setup complete![/green]")
    console.print(f"\n[dim]Data directory: {data_dir}[/dim]")
    console.print(f"[dim]Vector store: {config['vector_store']['config']['path']}[/dim]")
    console.print(f"[dim]LLM: {config['llm']['config']['model']}[/dim]")

    console.print("\n[cyan]Next steps:[/cyan]")
    console.print("  1. Index your chat history:")
    console.print("     [bold]copilot-memory index --db copilot_chats.db[/bold]")
    console.print("  2. Search your memories:")
    console.print('     [bold]copilot-memory search "how did I handle errors?"[/bold]')


@app.command("index")
def index_memories(
    db: Annotated[
        Path,
        typer.Option("--db", "-d", help="Path to copilot_chats.db SQLite database."),
    ] = Path("copilot_chats.db"),
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Only index sessions from this workspace."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", help="Maximum sessions to process."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show verbose output."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force re-index all sessions."),
    ] = False,
    data_dir: Annotated[
        Path,
        typer.Option("--data-dir", help="Directory for storing memory data."),
    ] = DEFAULT_DATA_DIR,
):
    """Index chat sessions into Mem0 for semantic search.

    This command reads sessions from the existing copilot_chats.db database
    and extracts facts, preferences, and patterns using Mem0.

    By default, uses incremental indexing (skips already-indexed sessions).
    Use --force to re-index everything.

    Example:
        copilot-memory index --db copilot_chats.db
        copilot-memory index -w my-project --verbose
        copilot-memory index --force
    """
    from copilot_repository_tools_common import Database

    if not db.exists():
        console.print(f"[red]Database not found: {db}[/red]")
        console.print("Run 'copilot-chat-archive scan' first to import sessions.")
        raise typer.Exit(1)

    manager = _get_memory_manager(data_dir)
    database = Database(str(db))

    sessions = database.list_sessions(workspace_name=workspace, limit=limit)

    if not sessions:
        console.print("[yellow]No sessions found to index.[/yellow]")
        return

    console.print(f"[cyan]Indexing {len(sessions)} sessions into Mem0...[/cyan]")
    if force:
        console.print("[dim]  (Force mode: re-indexing all sessions)[/dim]")
    else:
        console.print("[dim]  (Incremental mode: skipping already-indexed sessions)[/dim]")

    total_memories = 0
    indexed = 0
    skipped = 0
    errors = 0

    with console.status("[bold green]Processing...") as status:
        for i, session_info in enumerate(sessions):
            session = database.get_session(session_info["session_id"])
            if session:
                workspace_name = session.workspace_name or "Unknown"
                status.update(f"[bold green]Processing {i + 1}/{len(sessions)}: {workspace_name}...")

                try:
                    memories = manager.add_session(session, force=force)
                    if memories:
                        total_memories += len(memories)
                        indexed += 1
                        if verbose:
                            console.print(f"  [green]✓[/green] {workspace_name}: {len(memories)} memories extracted")
                            for mem in memories[:3]:
                                console.print(f"    - {mem.content[:80]}...")
                    else:
                        skipped += 1
                        if verbose:
                            console.print(f"  [dim]⊘[/dim] {workspace_name}: skipped (already indexed)")
                except Exception as e:
                    errors += 1
                    if verbose:
                        console.print(f"  [red]✗[/red] {workspace_name}: {e}")

    console.print("\n[green]✓ Indexing complete[/green]")
    console.print(f"  Indexed: {indexed} sessions ({total_memories} memories extracted)")
    console.print(f"  Skipped: {skipped} sessions (already indexed)")
    if errors > 0:
        console.print(f"  [yellow]Errors: {errors} sessions[/yellow]")


@app.command("search")
def search_memories(
    query: Annotated[str, typer.Argument(help="Natural language search query.")],
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Maximum results to return."),
    ] = 10,
    repository: Annotated[
        str | None,
        typer.Option("--repository", "-r", help="Search within repository scope (e.g., 'github.com/owner/repo')."),
    ] = None,
    folder: Annotated[
        str | None,
        typer.Option("--folder", "-f", help="Search within folder scope (workspace path)."),
    ] = None,
    data_dir: Annotated[
        Path,
        typer.Option("--data-dir", help="Directory for storing memory data."),
    ] = DEFAULT_DATA_DIR,
):
    """Semantic search through memories within a scope.

    Unlike keyword search, this understands meaning - so "login issues"
    can find memories about "authentication errors".

    Memories are scoped to either:
    - Repository (if workspace is in a git repo) - use --repository
    - Folder (if not in a git repo) - use --folder

    Example:
        copilot-memory search "how did I handle authentication?" --repository github.com/owner/repo
        copilot-memory search "async patterns" --folder /path/to/project
    """
    manager = _get_memory_manager(data_dir)

    # Determine scope
    scope_id = _get_scope_id(repository, folder)
    if scope_id == "unknown":
        console.print("[yellow]Please specify --repository or --folder to search within a scope.[/yellow]")
        console.print("Example: copilot-memory search 'query' --repository github.com/owner/repo")
        raise typer.Exit(1)

    results = manager.search(query, limit=limit, scope_id=scope_id)

    if not results:
        console.print("[yellow]No memories found matching your query.[/yellow]")
        console.print(f"[dim]Searched in scope: {scope_id}[/dim]")
        return

    console.print(f"\n[cyan]Found {len(results)} relevant memories:[/cyan]")
    console.print(f"[dim]Scope: {scope_id}[/dim]\n")

    for i, mem in enumerate(results, 1):
        score_str = f" (score: {mem.score:.2f})" if mem.score else ""
        workspace_str = mem.metadata.get("workspace_name", "unknown")

        console.print(f"[bold]{i}.[/bold] {mem.content}")
        console.print(f"   [dim]Workspace: {workspace_str}{score_str}[/dim]\n")


@app.command("list")
def list_memories(
    repository: Annotated[
        str | None,
        typer.Option("--repository", "-r", help="List memories in repository scope."),
    ] = None,
    folder: Annotated[
        str | None,
        typer.Option("--folder", "-f", help="List memories in folder scope."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Maximum results to show."),
    ] = 50,
    data_dir: Annotated[
        Path,
        typer.Option("--data-dir", help="Directory for storing memory data."),
    ] = DEFAULT_DATA_DIR,
):
    """List all stored memories within a scope.

    Memories are scoped to either:
    - Repository (if workspace is in a git repo) - use --repository
    - Folder (if not in a git repo) - use --folder
    """
    manager = _get_memory_manager(data_dir)

    # Determine scope
    scope_id = _get_scope_id(repository, folder)
    if scope_id == "unknown":
        console.print("[yellow]Please specify --repository or --folder to list memories within a scope.[/yellow]")
        console.print("Example: copilot-memory list --repository github.com/owner/repo")
        raise typer.Exit(1)

    memories = manager.get_all(scope_id=scope_id)

    if not memories:
        console.print("[yellow]No memories stored yet.[/yellow]")
        console.print(f"[dim]Scope: {scope_id}[/dim]")
        console.print("Run [cyan]copilot-memory index[/cyan] to extract memories from your chat history.")
        return

    table = Table(title=f"Stored Memories ({len(memories)} total)")
    table.add_column("ID", style="dim", width=12)
    table.add_column("Memory", style="cyan", max_width=60)
    table.add_column("Workspace", style="green")

    for mem in memories[:limit]:
        table.add_row(
            mem.id[:12] + "..." if len(mem.id) > 12 else mem.id,
            mem.content[:60] + "..." if len(mem.content) > 60 else mem.content,
            mem.metadata.get("workspace_name", "unknown"),
        )

    console.print(f"[dim]Scope: {scope_id}[/dim]")
    console.print(table)

    if len(memories) > limit:
        console.print(f"\n[dim]Showing {limit} of {len(memories)} memories. Use --limit to see more.[/dim]")


@app.command("clear")
def clear_memories(
    repository: Annotated[
        str | None,
        typer.Option("--repository", "-r", help="Clear memories in repository scope."),
    ] = None,
    folder: Annotated[
        str | None,
        typer.Option("--folder", "-f", help="Clear memories in folder scope."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt."),
    ] = False,
    data_dir: Annotated[
        Path,
        typer.Option("--data-dir", help="Directory for storing memory data."),
    ] = DEFAULT_DATA_DIR,
):
    """Clear stored memories within a scope.

    Memories from one scope cannot affect memories from another scope.
    You must specify either --repository or --folder.
    """
    # Determine scope
    scope_id = _get_scope_id(repository, folder)
    if scope_id == "unknown":
        console.print("[yellow]Please specify --repository or --folder to clear memories within a scope.[/yellow]")
        console.print("Example: copilot-memory clear --repository github.com/owner/repo")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Are you sure you want to clear all memories in scope '{scope_id}'?")
        if not confirm:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)

    manager = _get_memory_manager(data_dir)
    count = manager.clear(scope_id=scope_id)

    if count == -1:
        console.print(f"[green]✓ Cleared all memories in scope '{scope_id}'.[/green]")
    else:
        console.print(f"[green]✓ Cleared {count} memories.[/green]")


@app.command("stats")
def show_stats(
    repository: Annotated[
        str | None,
        typer.Option("--repository", "-r", help="Show stats for repository scope."),
    ] = None,
    folder: Annotated[
        str | None,
        typer.Option("--folder", "-f", help="Show stats for folder scope."),
    ] = None,
    data_dir: Annotated[
        Path,
        typer.Option("--data-dir", help="Directory for storing memory data."),
    ] = DEFAULT_DATA_DIR,
):
    """Show memory statistics for a scope.

    Provide --repository or --folder to see stats for that scope.
    """
    # Determine scope
    scope_id = _get_scope_id(repository, folder)
    if scope_id == "unknown":
        console.print("[yellow]Please specify --repository or --folder to show stats for a scope.[/yellow]")
        console.print("Example: copilot-memory stats --repository github.com/owner/repo")
        raise typer.Exit(1)

    manager = _get_memory_manager(data_dir)

    stats = manager.get_scope_stats(scope_id)

    console.print("\n[bold]Memory Statistics[/bold]")
    console.print(f"  Scope: {scope_id}")
    console.print(f"  Total memories: {stats['memory_count']}")


def run():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    run()
