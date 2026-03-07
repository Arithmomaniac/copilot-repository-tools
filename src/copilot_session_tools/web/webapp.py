"""Flask web application for viewing Copilot chat archive."""

import re

from flask import Flask, flash, jsonify, make_response, redirect, render_template, request, session, url_for

from copilot_session_tools import Database, __version__, generate_session_filename, get_vscode_storage_paths
from copilot_session_tools.refresh import enrich_single_session, run_enrichment, run_refresh
from copilot_session_tools.utils import (
    build_block_metadata,
    extract_filename,
    format_timestamp,
    markdown_to_html,
    match_tool_for_block,
    parse_diff_stats,
    strip_ansi,
    urldecode,
)


def create_app(
    db_path: str,
    title: str = "Copilot Session Tools",
    storage_paths: list | None = None,
) -> Flask:
    """Create and configure the Flask application.

    Args:
        db_path: Path to the SQLite database file.
        title: Title for the archive.
        storage_paths: Optional list of (path, edition) tuples for scanning.
                       If None, uses default VS Code storage paths.

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
    app.jinja_env.filters["markdown"] = markdown_to_html
    app.jinja_env.filters["urldecode"] = urldecode
    app.jinja_env.filters["format_timestamp"] = format_timestamp
    app.jinja_env.filters["parse_diff_stats"] = parse_diff_stats
    app.jinja_env.filters["extract_filename"] = extract_filename
    app.jinja_env.filters["strip_ansi"] = strip_ansi

    # Register global function for tool matching
    app.jinja_env.globals["match_tool_for_block"] = match_tool_for_block
    app.jinja_env.globals["get_nested_meta"] = lambda block: getattr(block, "_nested_meta", {})

    # Store database path, title, storage paths, and CLI inclusion in app config
    app.config["DB_PATH"] = db_path
    app.config["ARCHIVE_TITLE"] = title
    app.config["STORAGE_PATHS"] = storage_paths  # None means use default VS Code paths

    # Check for PyPI upgrade (cached, non-blocking)
    try:
        from copilot_session_tools.version_check import check_for_upgrade

        app.config["UPGRADE_AVAILABLE"] = check_for_upgrade()
    except Exception:
        app.config["UPGRADE_AVAILABLE"] = None

    def _create_snippet(content: str, max_length: int = 150) -> str:
        """Create a snippet from content, normalizing whitespace."""
        if not content:
            return ""
        # Normalize whitespace (replace newlines and multiple spaces with single space)

        normalized = re.sub(r"\s+", " ", content).strip()
        if len(normalized) > max_length:
            return normalized[:max_length] + "..."
        return normalized

    @app.route("/")
    def index():
        """List sessions, with optional search, workspace, repository filtering, and pagination."""
        db = Database(app.config["DB_PATH"])
        query = request.args.get("q", "").strip()
        selected_workspaces = request.args.getlist("workspace")
        selected_repositories = request.args.getlist("repository")
        selected_editions = request.args.getlist("edition")
        sort_by = request.args.get("sort", "relevance")  # 'relevance' or 'date'

        # Pagination parameters
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (ValueError, TypeError):
            page = 1
        per_page = 20  # Sessions per page

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

        # Calculate pagination
        total_sessions = len(sessions)
        total_pages = max(1, (total_sessions + per_page - 1) // per_page)  # Ceiling division
        page = min(page, total_pages)  # Ensure page doesn't exceed total
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_sessions = sessions[start_idx:end_idx]

        workspaces = db.get_workspaces()
        repositories = db.get_repositories()
        stats = db.get_stats()
        cst_tables_exist = db.has_cst_tables()
        version_refresh_count = db.count_sessions_needing_version_refresh(__version__) if cst_tables_exist else 0

        return render_template(
            "index.html",
            title=app.config["ARCHIVE_TITLE"],
            app_version=__version__,
            sessions=paginated_sessions,
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
            has_cst_tables=cst_tables_exist,
            version_refresh_count=version_refresh_count,
            upgrade_available=app.config.get("UPGRADE_AVAILABLE"),
            # Pagination context
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            total_sessions=total_sessions,
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

        # Determine if this session has enriched (cst_*) data
        is_enriched = False
        if db.has_cst_tables():
            with db._get_connection() as conn:
                row = conn.execute("SELECT 1 FROM cst_sessions WHERE session_id = ?", (session_id,)).fetchone()
                is_enriched = row is not None

        # For unenriched sessions, build simple turns list for the template
        turns = None
        new_turns = None
        if not is_enriched:
            turns = db.get_builtin_turns(session_id)
        else:
            # Check for new turns added since last enrichment
            enriched_turn_count = sum(1 for m in session.messages if m.role == "user")
            all_builtin_turns = db.get_builtin_turns(session_id)
            if len(all_builtin_turns) > enriched_turn_count:
                new_turns = all_builtin_turns[enriched_turn_count:]

        # Pre-process messages to match tool invocations and command runs with content blocks
        # This creates a mapping that the template can use directly
        first_user_prompt = None
        message_metadata: dict[int, dict] = {}
        for msg_idx, message in enumerate(session.messages):
            # Capture first user prompt for title fallback
            if message.role == "user" and first_user_prompt is None:
                first_user_prompt = message.content

            message_metadata[msg_idx] = build_block_metadata(message.content_blocks, message.tool_invocations, message.command_runs)

        enrichment_version = db.get_session_enrichment_version(session_id) if is_enriched else None
        needs_version_refresh = False
        if is_enriched and (enrichment_version is None or enrichment_version != __version__):
            from packaging.version import Version

            needs_version_refresh = enrichment_version is None or Version(enrichment_version) < Version(__version__)

        return render_template(
            "session.html",
            title=app.config["ARCHIVE_TITLE"],
            app_version=__version__,
            session=session,
            message_count=len(session.messages),
            first_user_prompt=first_user_prompt,
            message_metadata=message_metadata,
            is_enriched=is_enriched,
            turns=turns,
            new_turns=new_turns,
            needs_version_refresh=needs_version_refresh,
            enrichment_version=enrichment_version,
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

        result = run_refresh(db, storage_paths, full=full_refresh)
        enrich_result = run_enrichment(db)

        # Store refresh result in Flask session for display after redirect
        session["refresh_result"] = {
            "added": result.added,
            "updated": result.updated,
            "skipped": result.skipped,
            "mode": result.mode,
            "enriched": enrich_result.enriched,
            "reparsed": enrich_result.reparsed,
        }

        return redirect(url_for("index"))

    @app.route("/enrich/<session_id>", methods=["POST"])
    def enrich_session(session_id: str):
        """Enrich a single CLI session by parsing its events.jsonl file."""
        enrich_db = Database(app.config["DB_PATH"])
        error = enrich_single_session(enrich_db, session_id)
        if error:
            flash(error, "error")
            return redirect(url_for("session_view", session_id=session_id))
        return redirect(url_for("session_view", session_id=session_id))

    @app.route("/api/markdown/<session_id>", methods=["GET"])
    def get_markdown(session_id: str):
        """Get markdown for a session's messages.

        Query parameters:
        - start: Start message number (1-based, optional, defaults to 1)
        - end: End message number (1-based, optional, defaults to last message)
        - include_diffs: Whether to include file diffs (default: true)
        - include_tool_inputs: Whether to include tool inputs (default: true)
        - include_thinking: Whether to include thinking blocks (default: false)
        - download: If true, return as a file attachment instead of JSON (default: false)

        Returns:
            JSON with 'markdown' field, or a .md file download if download=true.
        """
        db = Database(app.config["DB_PATH"])

        # Parse range parameters
        start_param = request.args.get("start", "").strip()
        end_param = request.args.get("end", "").strip()

        # Parse boolean options
        include_diffs = request.args.get("include_diffs", "true").lower() == "true"
        include_tool_inputs = request.args.get("include_tool_inputs", "true").lower() == "true"
        include_thinking = request.args.get("include_thinking", "false").lower() == "true"
        include_agent_details = request.args.get("include_agent_details", "true").lower() == "true"
        download = request.args.get("download", "false").lower() == "true"

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

        # For download mode, we need the session object for filename generation
        chat_session = None
        if download:
            chat_session = db.get_session(session_id)
            if chat_session is None:
                return jsonify({"error": "Session not found"}), 404

        markdown_content = db.get_messages_markdown(
            session_id,
            start=start,
            end=end,
            include_diffs=include_diffs,
            include_tool_inputs=include_tool_inputs,
            include_thinking=include_thinking,
            include_agent_details=include_agent_details,
        )

        if not markdown_content:
            return jsonify({"error": "No messages found"}), 404

        if download and chat_session is not None:
            filename = generate_session_filename(chat_session)
            response = make_response(markdown_content)
            response.headers["Content-Type"] = "text/markdown; charset=utf-8"
            response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response

        return jsonify({"markdown": markdown_content})

    return app


def run_server(
    host: str = "127.0.0.1",
    port: int = 5000,
    db_path: str = "copilot_chats.db",
    title: str = "Copilot Session Tools",
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
    # Suppress Flask/Werkzeug startup banners — we print our own
    import logging

    logging.getLogger("werkzeug").setLevel(logging.WARNING if debug else logging.ERROR)
    app.run(host=host, port=port, debug=debug)
