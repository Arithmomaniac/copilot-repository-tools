"""CLI for Copilot Repository Tools Memory.

This module provides a standalone CLI for semantic memory operations:
- setup: Initialize Mem0 with local resources (auto-runs on first use)
- index: Extract facts from chat sessions
- search: Semantic search through memories
- list: List all stored memories
- clear: Clear memories
- serve: Start a simple API server (optional, for web integration)
"""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .manager import MemoryManager, get_default_config

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
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Filter by workspace."),
    ] = None,
    repository: Annotated[
        str | None,
        typer.Option("--repository", "-r", help="Filter by repository (e.g., 'github.com/owner/repo')."),
    ] = None,
    data_dir: Annotated[
        Path,
        typer.Option("--data-dir", help="Directory for storing memory data."),
    ] = DEFAULT_DATA_DIR,
):
    """Semantic search through memories.

    Unlike keyword search, this understands meaning - so "login issues"
    can find memories about "authentication errors".

    You can filter by workspace (specific directory) or by repository
    (all workspaces/worktrees for the same git repository).

    Example:
        copilot-memory search "how did I handle authentication?"
        copilot-memory search "async patterns" --workspace my-project
        copilot-memory search "error handling" --repository github.com/owner/repo
    """
    manager = _get_memory_manager(data_dir)

    results = manager.search(query, limit=limit, workspace_name=workspace, repository_url=repository)

    if not results:
        console.print("[yellow]No memories found matching your query.[/yellow]")
        return

    console.print(f"\n[cyan]Found {len(results)} relevant memories:[/cyan]\n")

    for i, mem in enumerate(results, 1):
        score_str = f" (score: {mem.score:.2f})" if mem.score else ""
        workspace_str = mem.metadata.get("workspace_name", "unknown")
        repo_str = mem.metadata.get("repository_url", "")

        console.print(f"[bold]{i}.[/bold] {mem.content}")
        if repo_str:
            console.print(f"   [dim]Workspace: {workspace_str} | Repository: {repo_str}{score_str}[/dim]\n")
        else:
            console.print(f"   [dim]Workspace: {workspace_str}{score_str}[/dim]\n")


@app.command("list")
def list_memories(
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Filter by workspace."),
    ] = None,
    repository: Annotated[
        str | None,
        typer.Option("--repository", "-r", help="Filter by repository (e.g., 'github.com/owner/repo')."),
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
    """List all stored memories."""
    manager = _get_memory_manager(data_dir)

    memories = manager.get_all(workspace_name=workspace, repository_url=repository)

    if not memories:
        console.print("[yellow]No memories stored yet.[/yellow]")
        console.print("Run [cyan]copilot-memory index[/cyan] to extract memories from your chat history.")
        return

    table = Table(title=f"Stored Memories ({len(memories)} total)")
    table.add_column("ID", style="dim", width=12)
    table.add_column("Memory", style="cyan", max_width=50)
    table.add_column("Workspace", style="green")
    table.add_column("Repository", style="blue", max_width=30)

    for mem in memories[:limit]:
        repo_url = mem.metadata.get("repository_url", "")
        table.add_row(
            mem.id[:12] + "..." if len(mem.id) > 12 else mem.id,
            mem.content[:50] + "..." if len(mem.content) > 50 else mem.content,
            mem.metadata.get("workspace_name", "unknown"),
            repo_url[:30] + "..." if len(repo_url) > 30 else repo_url,
        )

    console.print(table)

    if len(memories) > limit:
        console.print(f"\n[dim]Showing {limit} of {len(memories)} memories. Use --limit to see more.[/dim]")


@app.command("clear")
def clear_memories(
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Only clear memories for this workspace."),
    ] = None,
    repository: Annotated[
        str | None,
        typer.Option("--repository", "-r", help="Only clear memories for this repository."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation prompt."),
    ] = False,
    data_dir: Annotated[
        Path,
        typer.Option("--data-dir", help="Directory for storing memory data."),
    ] = DEFAULT_DATA_DIR,
):
    """Clear stored memories."""
    if not force:
        if repository:
            scope = f"repository '{repository}'"
        elif workspace:
            scope = f"workspace '{workspace}'"
        else:
            scope = "all workspaces"
        confirm = typer.confirm(f"Are you sure you want to clear memories for {scope}?")
        if not confirm:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)

    manager = _get_memory_manager(data_dir)
    count = manager.clear(workspace_name=workspace, repository_url=repository)

    if count == -1:
        console.print("[green]✓ Cleared all memories.[/green]")
    else:
        console.print(f"[green]✓ Cleared {count} memories.[/green]")


@app.command("stats")
def show_stats(
    data_dir: Annotated[
        Path,
        typer.Option("--data-dir", help="Directory for storing memory data."),
    ] = DEFAULT_DATA_DIR,
):
    """Show memory statistics."""
    manager = _get_memory_manager(data_dir)

    all_memories = manager.get_all()

    # Group by workspace
    workspace_counts: dict[str, int] = {}
    for m in all_memories:
        ws = m.metadata.get("workspace_name", "unknown")
        workspace_counts[ws] = workspace_counts.get(ws, 0) + 1

    console.print("\n[bold]Memory Statistics[/bold]")
    console.print(f"  Total memories: {len(all_memories)}")
    console.print(f"  Workspaces: {len(workspace_counts)}")

    if workspace_counts:
        console.print("\n[cyan]By Workspace:[/cyan]")
        for ws, count in sorted(workspace_counts.items(), key=lambda x: -x[1]):
            console.print(f"  {ws}: {count} memories")


def run():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    run()
