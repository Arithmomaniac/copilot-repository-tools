"""Flask routes for Mem0 memory features.

This module provides API endpoints for semantic memory search and management.
The routes check for Mem0 availability and return appropriate status messages
when the optional dependency is not installed.
"""

from typing import Any

from flask import Blueprint, current_app, jsonify, request

memory_bp = Blueprint("memory", __name__, url_prefix="/api/memory")

# Module-level cache for memory manager (avoids Flask app attribute issues)
_memory_manager_cache: dict[str, Any] = {}


def _get_memory_manager():
    """Get or create MemoryManager from app config.

    Returns:
        MemoryManager instance if Mem0 is available, None otherwise.
    """
    cache_key = "manager"
    if cache_key not in _memory_manager_cache:
        try:
            from copilot_repository_tools_common import MEM0_AVAILABLE, MemoryManager

            if MEM0_AVAILABLE:
                # Get config path from app config if provided
                config_path = current_app.config.get("MEM0_CONFIG_PATH")
                config = None
                if config_path:
                    import json
                    from pathlib import Path

                    config_file = Path(config_path)
                    if config_file.exists():
                        config = json.loads(config_file.read_text(encoding="utf-8"))

                _memory_manager_cache[cache_key] = MemoryManager(config=config)
            else:
                _memory_manager_cache[cache_key] = None
        except Exception:
            _memory_manager_cache[cache_key] = None
    return _memory_manager_cache.get(cache_key)


@memory_bp.route("/status")
def memory_status():
    """Check if Mem0 is available and configured.

    Returns:
        JSON with availability status and message.
    """
    try:
        from copilot_repository_tools_common import MEM0_AVAILABLE
    except ImportError:
        MEM0_AVAILABLE = False

    manager = _get_memory_manager() if MEM0_AVAILABLE else None

    return jsonify(
        {
            "available": manager is not None,
            "installed": MEM0_AVAILABLE,
            "message": "Mem0 is ready for semantic search"
            if manager
            else ("Mem0 is installed but not configured" if MEM0_AVAILABLE else "Mem0 not installed. Install with: uv add mem0ai litellm"),
        }
    )


@memory_bp.route("/search")
def semantic_search():
    """Semantic search through memories.

    Query parameters:
        q: Search query (required)
        workspace: Optional workspace filter
        limit: Maximum results (default: 10)

    Returns:
        JSON with search results or error message.
    """
    manager = _get_memory_manager()
    if not manager:
        try:
            from copilot_repository_tools_common import MEM0_AVAILABLE
        except ImportError:
            MEM0_AVAILABLE = False

        return (
            jsonify(
                {
                    "error": "Mem0 not available",
                    "installed": MEM0_AVAILABLE,
                    "message": "Install with: uv add mem0ai litellm" if not MEM0_AVAILABLE else "Mem0 configuration error",
                }
            ),
            503,
        )

    query = request.args.get("q", "").strip()
    workspace = request.args.get("workspace")
    limit = int(request.args.get("limit", 10))

    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    try:
        results = manager.search(query, limit=limit, workspace_name=workspace)

        return jsonify(
            {
                "query": query,
                "count": len(results),
                "results": [
                    {
                        "id": r.id,
                        "content": r.content,
                        "score": r.score,
                        "workspace": r.metadata.get("workspace_name"),
                        "session_id": r.metadata.get("session_id"),
                    }
                    for r in results
                ],
            }
        )
    except Exception as e:
        return jsonify({"error": f"Search failed: {e!s}"}), 500


@memory_bp.route("/list")
def list_memories():
    """List all stored memories.

    Query parameters:
        workspace: Optional workspace filter
        limit: Maximum results (default: 50)

    Returns:
        JSON with list of memories.
    """
    manager = _get_memory_manager()
    if not manager:
        return jsonify({"error": "Mem0 not available"}), 503

    workspace = request.args.get("workspace")
    limit = int(request.args.get("limit", 50))

    try:
        memories = manager.get_all(workspace_name=workspace)

        return jsonify(
            {
                "count": len(memories),
                "memories": [
                    {
                        "id": m.id,
                        "content": m.content,
                        "workspace": m.metadata.get("workspace_name"),
                        "session_id": m.metadata.get("session_id"),
                    }
                    for m in memories[:limit]
                ],
            }
        )
    except Exception as e:
        return jsonify({"error": f"Failed to list memories: {e!s}"}), 500


@memory_bp.route("/stats")
def memory_stats():
    """Get memory statistics.

    Returns:
        JSON with memory count and workspace breakdown.
    """
    manager = _get_memory_manager()
    if not manager:
        return jsonify({"error": "Mem0 not available"}), 503

    try:
        all_memories = manager.get_all()

        # Group by workspace
        workspace_counts: dict[str, int] = {}
        for m in all_memories:
            ws = m.metadata.get("workspace_name", "unknown")
            workspace_counts[ws] = workspace_counts.get(ws, 0) + 1

        return jsonify(
            {
                "total_memories": len(all_memories),
                "workspaces": workspace_counts,
            }
        )
    except Exception as e:
        return jsonify({"error": f"Failed to get stats: {e!s}"}), 500
