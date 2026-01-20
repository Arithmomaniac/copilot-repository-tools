"""Flask web application for viewing Copilot chat archive."""

from datetime import datetime
from urllib.parse import unquote

import markdown
from copilot_repository_tools_common import Database, get_vscode_storage_paths, scan_chat_sessions
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

# Create a reusable markdown converter with extensions
_md_converter = markdown.Markdown(
    extensions=[
        "tables",  # Support markdown tables
        "fenced_code",  # Support ```code blocks```
        "sane_lists",  # Better list handling
        "smarty",  # Smart quotes and dashes
        "nl2br",  # Convert newlines to <br> tags
    ],
    extension_configs={
        "smarty": {
            "smart_dashes": True,
            "smart_quotes": True,
        },
    },
)


def _markdown_to_html(text: str) -> str:
    """Convert markdown text to HTML using the markdown library."""
    if not text:
        return ""

    # Replace Windows line endings with Unix ones for consistent processing
    text = text.replace("\r\n", "\n")

    # Replace common VS Code Copilot UI patterns with proper markdown
    import re
    from urllib.parse import unquote

    def extract_filename_from_file_uri(uri: str) -> str:
        """Extract the filename from a file:// URI."""
        # Decode URL encoding and get the leaf name
        decoded = unquote(uri)
        # Remove file:// prefix and any anchor
        path = decoded.replace("file:///", "").split("#")[0]
        # Get leaf name
        if "/" in path:
            return path.split("/")[-1]
        if "\\" in path:
            return path.split("\\")[-1]
        return path

    # Handle empty-text links with file:// URIs: [](file://...) -> `filename`
    # This covers patterns like "Reading [](file://...)" or "Created [](file://...)"
    def replace_empty_file_link(match):
        uri = match.group(1)
        filename = extract_filename_from_file_uri(uri)
        return f"`{filename}`"

    text = re.sub(r"\[\]\(file://([^)]+)\)", replace_empty_file_link, text)

    # "Using "Tool Name"" -> _Using "Tool Name"_
    text = re.sub(r'^(Using ["""][^"""]+["""])$', r"_\1_", text, flags=re.MULTILINE)

    # "_Edited `filename`_" -> "_Edited filename_" (remove backticks within italics)
    text = re.sub(r"_Edited `([^`]+)`_", r"_Edited \1_", text)

    # "Ran terminal command:" -> _Ran terminal command:_
    text = re.sub(r"^(Ran terminal command:.*)$", r"_\1_", text, flags=re.MULTILINE)

    # "Let me [action]:" or "Now let me [action]:" -> _Let me [action]:_
    text = re.sub(r"^((?:Now )?[Ll]et me [^:]+:)$", r"_\1_", text, flags=re.MULTILINE)

    # "Made changes." at end -> _Made changes._
    text = re.sub(r"^(Made changes\.)$", r"_\1_", text, flags=re.MULTILINE)

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


def _parse_diff_stats(diff: str) -> dict:
    """Parse a diff string and return addition/deletion line counts.

    Args:
        diff: The diff string in unified diff format.

    Returns:
        Dictionary with 'additions' and 'deletions' counts.
    """
    if not diff:
        return {"additions": 0, "deletions": 0}

    additions = 0
    deletions = 0

    for line in diff.split("\n"):
        # Skip diff headers and hunk headers
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1

    return {"additions": additions, "deletions": deletions}


def _extract_filename(path: str) -> str:
    """Extract the filename from a file path.

    Args:
        path: Full file path (Unix or Windows style).

    Returns:
        The filename portion of the path.
    """
    if not path:
        return ""
    # Handle both Unix and Windows path separators
    if "/" in path:
        return path.split("/")[-1]
    if "\\" in path:
        return path.split("\\")[-1]
    return path


def _match_tool_for_block(block_content: str, tools: list, used_indices: set) -> tuple:
    """Match a tool invocation block content to a tool from the list.

    The block content contains text like "Running `pipelines_get_build_status`"
    but the tool name might be "mcp_ado-mcp_pipelines_get_build_status".
    We need to match by finding if the short name appears in the full tool name.

    Args:
        block_content: The content of the toolInvocation block.
        tools: List of ToolInvocation objects.
        used_indices: Set of already used tool indices (to avoid duplicates).

    Returns:
        Tuple of (matched_tool or None, updated used_indices set).
    """
    if not tools:
        return None, used_indices

    # Extract the tool name from backticks in the content
    # e.g., "Running `pipelines_get_build_status`" -> "pipelines_get_build_status"
    import re

    match = re.search(r"`([^`]+)`", block_content)
    short_name = match.group(1) if match else None

    # Also try to extract from "Running X" pattern without backticks
    if not short_name:
        match = re.search(r"Running\s+(\S+)", block_content)
        short_name = match.group(1) if match else None

    if short_name:
        # Try to find a tool whose name ends with or contains the short name
        for i, tool in enumerate(tools):
            if i in used_indices:
                continue
            # Check if short_name appears in the tool name (case-insensitive)
            if short_name.lower() in tool.name.lower() or tool.name.lower().endswith(short_name.lower()):
                used_indices = used_indices | {i}
                return tool, used_indices

    # Fallback: try sequential matching for tools not yet used
    for i, tool in enumerate(tools):
        if i not in used_indices:
            used_indices = used_indices | {i}
            return tool, used_indices

    return None, used_indices


def create_app(
    db_path: str,
    title: str = "Copilot Chat Archive",
    storage_paths: list | None = None,
    include_cli: bool = True,
) -> Flask:
    """Create and configure the Flask application.

    Args:
        db_path: Path to the SQLite database file.
        title: Title for the archive.
        storage_paths: Optional list of (path, edition) tuples for scanning.
                       If None, uses default VS Code storage paths.
        include_cli: Whether to include CLI sessions when scanning (default: True).

    Returns:
        Configured Flask application.
    """
    app = Flask(
        __name__,
        template_folder="templates",
    )

    # Set a secret key for session support (used for transient flash messages)
    # A random key is fine here since sessions only contain ephemeral refresh notifications.
    # Set FLASK_SECRET_KEY environment variable for persistent sessions across restarts.
    import os

    app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

    # Register Jinja2 filters
    app.jinja_env.filters["markdown"] = _markdown_to_html
    app.jinja_env.filters["urldecode"] = _urldecode
    app.jinja_env.filters["format_timestamp"] = _format_timestamp
    app.jinja_env.filters["parse_diff_stats"] = _parse_diff_stats
    app.jinja_env.filters["extract_filename"] = _extract_filename

    # Register global function for tool matching
    app.jinja_env.globals["match_tool_for_block"] = _match_tool_for_block

    # Store database path, title, storage paths, and CLI inclusion in app config
    app.config["DB_PATH"] = db_path
    app.config["ARCHIVE_TITLE"] = title
    app.config["STORAGE_PATHS"] = storage_paths  # None means use default VS Code paths
    app.config["INCLUDE_CLI"] = include_cli

    def _create_snippet(content: str, max_length: int = 150) -> str:
        """Create a snippet from content, normalizing whitespace."""
        if not content:
            return ""
        # Normalize whitespace (replace newlines and multiple spaces with single space)
        import re

        normalized = re.sub(r"\s+", " ", content).strip()
        if len(normalized) > max_length:
            return normalized[:max_length] + "..."
        return normalized

    @app.route("/")
    def index():
        """List sessions, with optional search, workspace, and repository filtering."""
        db = Database(app.config["DB_PATH"])
        query = request.args.get("q", "").strip()
        selected_workspaces = request.args.getlist("workspace")
        selected_repositories = request.args.getlist("repository")
        selected_editions = request.args.getlist("edition")
        sort_by = request.args.get("sort", "relevance")  # 'relevance' or 'date'

        # Get refresh results from session (set after a refresh operation)
        # Pop to ensure it's only shown once
        refresh_result = session.pop("refresh_result", None)

        search_snippets = {}  # session_id -> list of snippets with message links

        if query:
            # Use FTS search with sort option
            # The db.search() returns results in the correct order based on sort_by
            search_results = db.search(query, limit=100, sort_by=sort_by)

            # Group results by session and collect snippets
            # session_ids preserves the order from search results (for relevance sorting)
            session_ids = []
            for r in search_results:
                sid = r["session_id"]
                if sid not in search_snippets:
                    search_snippets[sid] = []
                    session_ids.append(sid)

                # Add snippet (up to 5 per session, each from different message)
                if len(search_snippets[sid]) < 5:
                    # message_index is 0-based, but anchor is 1-based
                    msg_index = r.get("message_index", 0)
                    snippet = {
                        "text": _create_snippet(r.get("highlighted", r.get("content", ""))),
                        "message_anchor": msg_index + 1,  # 1-based for #msg-N
                    }
                    search_snippets[sid].append(snippet)

            # Get full session info for matching sessions, preserving search result order
            all_sessions = db.list_sessions()
            session_map = {s["session_id"]: s for s in all_sessions}
            sessions = [session_map[sid] for sid in session_ids if sid in session_map]
        else:
            # No query: list_sessions() returns sessions sorted by date (newest first)
            # Relevance sorting doesn't apply without a search query
            sessions = db.list_sessions()

        # Apply workspace filter if selected
        if selected_workspaces:
            sessions = [s for s in sessions if s.get("workspace_name") in selected_workspaces]

        # Apply repository filter if selected
        if selected_repositories:
            sessions = [s for s in sessions if s.get("repository_url") in selected_repositories]

        # Apply edition filter if selected
        if selected_editions:
            sessions = [s for s in sessions if s.get("vscode_edition") in selected_editions]

        workspaces = db.get_workspaces()
        repositories = db.get_repositories()
        stats = db.get_stats()

        return render_template(
            "index.html",
            title=app.config["ARCHIVE_TITLE"],
            sessions=sessions,
            workspaces=workspaces,
            repositories=repositories,
            stats=stats,
            query=query,
            search_snippets=search_snippets,
            selected_workspaces=selected_workspaces,
            selected_repositories=selected_repositories,
            selected_editions=selected_editions,
            refresh_result=refresh_result,
            sort_by=sort_by,
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

        # Pre-process messages to match tool invocations and command runs with content blocks
        # This creates a mapping that the template can use directly
        first_user_prompt = None
        for message in session.messages:
            # Capture first user prompt for title fallback
            if message.role == "user" and first_user_prompt is None:
                first_user_prompt = message.content

            block_tool_map = {}
            block_cmd_map = {}
            used_tool_indices = set()
            used_cmd_indices = set()

            for i, block in enumerate(message.content_blocks):
                if block.kind == "toolInvocation":
                    # First try to match against command runs (for CLI shell commands)
                    # CLI commands have content like "$ git fetch --prune"
                    if message.command_runs and block.content.startswith("$"):
                        cmd_text = block.content[1:].strip()  # Remove leading $
                        for j, cmd in enumerate(message.command_runs):
                            if j in used_cmd_indices:
                                continue
                            # Match if the command starts with or contains the block text
                            if cmd.command and (cmd.command.startswith(cmd_text[:30]) or cmd_text[:30] in cmd.command):
                                block_cmd_map[i] = cmd
                                used_cmd_indices.add(j)
                                break

                    # If no command match, try matching against tool invocations
                    if i not in block_cmd_map and message.tool_invocations:
                        matched_tool, used_tool_indices = _match_tool_for_block(block.content, message.tool_invocations, used_tool_indices)
                        if matched_tool:
                            block_tool_map[i] = matched_tool

            # Store the mappings on the message for template access
            message._block_tool_map = block_tool_map
            message._block_cmd_map = block_cmd_map
            message._matched_tool_names = {t.name for t in block_tool_map.values()}
            message._matched_cmd_indices = used_cmd_indices

        return render_template(
            "session.html",
            title=app.config["ARCHIVE_TITLE"],
            session=session,
            message_count=len(session.messages),
            first_user_prompt=first_user_prompt,
        )

    @app.route("/refresh", methods=["POST"])
    def refresh_database():
        """Refresh the database by scanning for new or updated sessions.

        Supports two modes:
        - full=false (default): Incremental refresh, only updates changed sessions
        - full=true: Full rebuild, re-imports all sessions
        """
        db = Database(app.config["DB_PATH"])
        full_refresh = request.form.get("full", "false").lower() == "true"

        # Get storage paths - use configured paths or default VS Code paths
        # Check for None explicitly since empty list [] is a valid value (for testing)
        storage_paths = app.config.get("STORAGE_PATHS")
        if storage_paths is None:
            storage_paths = get_vscode_storage_paths()

        include_cli = app.config.get("INCLUDE_CLI", True)

        added = 0
        updated = 0
        skipped = 0

        for chat_session in scan_chat_sessions(storage_paths, include_cli=include_cli):
            if full_refresh:
                # In full mode, update all sessions
                # Try to add first - if it fails (returns False), session exists and we update
                if db.add_session(chat_session):
                    added += 1
                else:
                    db.update_session(chat_session)
                    updated += 1
            else:
                # Incremental mode: use needs_update() to determine if session should be updated
                needs_update = db.needs_update(
                    chat_session.session_id,
                    chat_session.source_file_mtime,
                    chat_session.source_file_size,
                )
                if needs_update:
                    # Try to add first - if it fails (returns False), session exists and we update
                    if db.add_session(chat_session):
                        added += 1
                    else:
                        db.update_session(chat_session)
                        updated += 1
                else:
                    skipped += 1

        # Store refresh result in Flask session for display after redirect
        session["refresh_result"] = {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "mode": "full" if full_refresh else "incremental",
        }

        return redirect(url_for("index"))

    @app.route("/api/markdown/<session_id>", methods=["GET"])
    def get_markdown(session_id: str):
        """Get cached markdown for a session's messages.

        Query parameters:
        - start: Start message number (1-based, optional, defaults to 1)
        - end: End message number (1-based, optional, defaults to last message)
        - include_diffs: Whether to include file diffs (default: true)
        - include_tool_inputs: Whether to include tool inputs (default: true)

        Returns:
            JSON with 'markdown' field containing the combined markdown.
        """
        db = Database(app.config["DB_PATH"])

        # Parse range parameters
        start_param = request.args.get("start", "").strip()
        end_param = request.args.get("end", "").strip()

        # Parse boolean options
        include_diffs = request.args.get("include_diffs", "true").lower() == "true"
        include_tool_inputs = request.args.get("include_tool_inputs", "true").lower() == "true"

        start = None
        end = None

        if start_param:
            try:
                start = int(start_param)
            except ValueError:
                return jsonify({"error": "Invalid start value"}), 400

        if end_param:
            try:
                end = int(end_param)
            except ValueError:
                return jsonify({"error": "Invalid end value"}), 400

        markdown_content = db.get_messages_markdown(
            session_id,
            start=start,
            end=end,
            include_diffs=include_diffs,
            include_tool_inputs=include_tool_inputs,
        )

        if not markdown_content:
            return jsonify({"error": "No messages found"}), 404

        return jsonify({"markdown": markdown_content})

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
