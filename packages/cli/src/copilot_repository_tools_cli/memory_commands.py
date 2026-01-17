"""Memory-related CLI commands using Mem0 integration.

This module provides CLI commands for managing semantic memories extracted
from Copilot chat history. Requires mem0ai and litellm to be installed.

Commands:
    memory index   - Index chat sessions into Mem0 for semantic search
    memory search  - Semantic search through memories
    memory list    - List all stored memories
    memory clear   - Clear stored memories
"""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

console = Console()

# Create a sub-app for memory commands
memory_app = typer.Typer(
    name="memory",
    help="Manage semantic memories from chat history (requires mem0ai + litellm).",
    no_args_is_help=True,
)


def _get_memory_manager(config_path: Path | None = None):
    """Get a MemoryManager instance, with helpful error if dependencies not installed.

    Args:
        config_path: Optional path to JSON config file for Mem0.

    Returns:
        Initialized MemoryManager instance.

    Raises:
        typer.Exit: If Mem0 or LiteLLM is not installed.
    """
    try:
        from copilot_repository_tools_common import MEM0_AVAILABLE, MemoryManager
    except ImportError:
        MEM0_AVAILABLE = False
        MemoryManager = None  # type: ignore[assignment]

    if not MEM0_AVAILABLE or MemoryManager is None:
        console.print("[red]Mem0 is not installed.[/red]\nInstall with:\n  [cyan]uv add mem0ai litellm[/cyan]\nor:\n  [cyan]pip install mem0ai litellm[/cyan]")
        raise typer.Exit(1)

    config = None
    if config_path and config_path.exists():
        import json

        config = json.loads(config_path.read_text(encoding="utf-8"))

    return MemoryManager(config=config)


@memory_app.command("index")
def index_memories(
    db: Annotated[
        Path,
        typer.Option("--db", "-d", help="Path to SQLite database file."),
    ] = Path("copilot_chats.db"),
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Only index sessions from this workspace."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to Mem0 config JSON file."),
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
        typer.Option("--force", "-f", help="Force re-index all sessions (ignore incremental tracking)."),
    ] = False,
):
    """Index chat sessions into Mem0 for semantic search.

    This command processes chat sessions from the database and extracts
    facts, preferences, and patterns using Mem0 with LLM-powered extraction.

    By default, uses incremental indexing: sessions that have already been
    indexed with the same message count are skipped. Use --force to re-index all.

    By default, uses GitHub Copilot models via LiteLLM for fact extraction.
    On first use, you may need to authenticate via OAuth device flow.

    Example:

    \b
        copilot-chat-archive memory index --db copilot_chats.db
        copilot-chat-archive memory index -w my-project --verbose
        copilot-chat-archive memory index --force  # Re-index everything
    """
    from copilot_repository_tools_common import Database

    if not db.exists():
        console.print(f"[red]Database not found: {db}[/red]")
        console.print("Run 'copilot-chat-archive scan' first to import sessions.")
        raise typer.Exit(1)

    manager = _get_memory_manager(config)
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
    console.print("[dim]Using GitHub Copilot via LiteLLM for fact extraction[/dim]")

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
                            for mem in memories[:3]:  # Show first 3
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


@memory_app.command("search")
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
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to Mem0 config JSON file."),
    ] = None,
):
    """Semantic search through memories.

    Unlike keyword search, semantic search understands meaning and can find
    relevant memories even when using different terminology.

    Example:

    \b
        copilot-chat-archive memory search "how did I handle authentication errors?"
        copilot-chat-archive memory search "async patterns" --workspace my-project
    """
    manager = _get_memory_manager(config)

    results = manager.search(query, limit=limit, workspace_name=workspace)

    if not results:
        console.print("[yellow]No memories found matching your query.[/yellow]")
        console.print("Try indexing your sessions first: [cyan]copilot-chat-archive memory index[/cyan]")
        return

    console.print(f"\n[cyan]Found {len(results)} relevant memories:[/cyan]\n")

    for i, mem in enumerate(results, 1):
        score_str = f" (score: {mem.score:.3f})" if mem.score else ""
        workspace_str = mem.metadata.get("workspace_name", "unknown")

        console.print(f"[bold]{i}.[/bold] {mem.content}")
        console.print(f"   [dim]Workspace: {workspace_str}{score_str}[/dim]\n")


@memory_app.command("list")
def list_memories(
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Filter by workspace."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to Mem0 config JSON file."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Maximum memories to display."),
    ] = 50,
):
    """List all stored memories.

    Shows memories that have been extracted from your chat sessions.
    Use --workspace to filter by a specific project.

    Example:

    \b
        copilot-chat-archive memory list
        copilot-chat-archive memory list --workspace my-project
    """
    manager = _get_memory_manager(config)

    memories = manager.get_all(workspace_name=workspace)

    if not memories:
        console.print("[yellow]No memories stored yet.[/yellow]")
        console.print("Run [cyan]copilot-chat-archive memory index[/cyan] to extract memories from your chat history.")
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

    console.print(table)

    if len(memories) > limit:
        console.print(f"\n[dim]Showing {limit} of {len(memories)} memories. Use --limit to see more.[/dim]")


@memory_app.command("clear")
def clear_memories(
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", "-w", help="Only clear memories for this workspace."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to Mem0 config JSON file."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation prompt."),
    ] = False,
):
    """Clear stored memories.

    This will permanently delete extracted memories. Use --workspace to limit
    deletion to a specific project.

    Example:

    \b
        copilot-chat-archive memory clear --workspace old-project
        copilot-chat-archive memory clear --force  # Clear all without confirmation
    """
    if not force:
        scope = f"workspace '{workspace}'" if workspace else "all workspaces"
        confirm = typer.confirm(f"Are you sure you want to clear memories for {scope}?")
        if not confirm:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)

    manager = _get_memory_manager(config)
    count = manager.clear(workspace_name=workspace)

    if count == -1:
        console.print("[green]✓ Cleared all memories.[/green]")
    else:
        console.print(f"[green]✓ Cleared {count} memories.[/green]")
