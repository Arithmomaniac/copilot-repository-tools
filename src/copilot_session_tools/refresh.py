"""Shared refresh/scan logic for the CLI and web interface.

This module provides a single implementation of the session scan-and-import
workflow so that the ``scan`` CLI command and the web ``/refresh`` endpoint
both exercise the same code path.
"""

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from copilot_session_tools import Database
from copilot_session_tools.scanner import PARSER_VERSION, SessionFileInfo, parse_session_file, scan_session_files
from copilot_session_tools.scanner.cli import _parse_cli_jsonl_file

# Regex for validating session IDs (hex + hyphens, i.e. UUIDs)
_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F-]+$")

# Default location of CLI session-state directories
SESSION_STATE_DIR = Path.home() / ".copilot" / "session-state"

# Number of threads for parallel file parsing
PARSE_WORKERS = 4

#: Callback signature: ``(event, item)`` where *event* is one of
#: ``"skipped"``, ``"added"``, ``"updated"``, ``"enriched"``, ``"reparsed"``,
#: ``"enrich_failed"`` and *item* is a :class:`SessionFileInfo`,
#: :class:`ChatSession`, or session-ID string.
ProgressCallback = Callable[[str, Any], None]


class RefreshMode(StrEnum):
    """Scan mode used for a refresh operation."""

    INCREMENTAL = "incremental"
    FULL = "full"


@dataclass
class RefreshResult:
    """Counts returned by a refresh operation."""

    added: int
    updated: int
    skipped: int
    mode: RefreshMode


@dataclass
class EnrichResult:
    """Counts returned by an enrichment operation."""

    enriched: int
    reparsed: int
    failed: int
    orphaned: int


def run_refresh(
    database: Database,
    storage_paths: list[tuple[str, str]] | None,
    full: bool = False,
    on_progress: ProgressCallback | None = None,
) -> RefreshResult:
    """Scan for VS Code Copilot chat sessions and import them into *database*.

    CLI sessions are **not** handled here — they flow through Chronicle's
    built-in session store and are enriched via :func:`run_enrichment`.

    Args:
        database: Open :class:`~copilot_session_tools.Database` to write into.
        storage_paths: List of ``(path, edition)`` tuples to search for VS Code
            sessions, or ``None`` to use the default VS Code storage paths.
        full: When ``True`` every discovered session is re-imported regardless
            of whether its source file has changed.  When ``False`` (the
            default) only files whose ``mtime`` or ``size`` differ from the
            stored metadata are re-imported.
        on_progress: Optional callback invoked for every add/update/skip event.

    Returns:
        A :class:`RefreshResult` with ``added``, ``updated``,
        ``skipped``, and ``mode`` fields.
    """
    added = 0
    updated = 0
    skipped = 0

    if full:
        # Full mode: parse every discovered session file and upsert.
        # Use the same parallel parsing and batch DB ops as incremental mode.
        all_files = list(scan_session_files(storage_paths, include_cli=False))

        if all_files:
            with ThreadPoolExecutor(max_workers=PARSE_WORKERS) as executor:
                parse_results = list(executor.map(parse_session_file, all_files))

            # Load existing session IDs upfront to avoid per-session DB lookups
            existing_ids = set(database.get_all_session_ids())

            sessions_to_add = []
            sessions_to_update = []
            for sessions in parse_results:
                for chat_session in sessions:
                    if chat_session.session_id in existing_ids:
                        sessions_to_update.append(chat_session)
                    else:
                        sessions_to_add.append(chat_session)

            if sessions_to_add:
                batch_added, _batch_skipped = database.add_sessions_batch(sessions_to_add)
                added += batch_added
                if on_progress:
                    for s in sessions_to_add:
                        on_progress("added", s)

            if sessions_to_update:
                updated += database.update_sessions_batch(sessions_to_update)
                if on_progress:
                    for s in sessions_to_update:
                        on_progress("updated", s)
    else:
        # Incremental mode: load all stored file metadata upfront so we can
        # skip unchanged files without hitting the DB once per file.
        stored_metadata = database.get_all_file_metadata()

        files_to_update: list[SessionFileInfo] = []
        for file_info in scan_session_files(storage_paths, include_cli=False):
            source_file = str(file_info.file_path)
            stored = stored_metadata.get(source_file)

            needs_update = stored is None or stored[0] is None or stored[1] is None or stored[0] != file_info.mtime or stored[1] != file_info.size

            if needs_update:
                files_to_update.append(file_info)
            else:
                # Count sessions in this file, not just the file itself
                session_count = stored[2] if stored is not None and len(stored) > 2 else 1
                skipped += session_count
                if on_progress:
                    on_progress("skipped", file_info)

        if files_to_update:
            with ThreadPoolExecutor(max_workers=PARSE_WORKERS) as executor:
                parse_results = list(executor.map(parse_session_file, files_to_update))

            sessions_to_add = []
            sessions_to_update = []

            for sessions in parse_results:
                for chat_session in sessions:
                    if database.get_session(chat_session.session_id):
                        sessions_to_update.append(chat_session)
                    else:
                        sessions_to_add.append(chat_session)

            if sessions_to_add:
                batch_added, _batch_skipped = database.add_sessions_batch(sessions_to_add)
                added += batch_added
                if on_progress:
                    for chat_session in sessions_to_add:
                        on_progress("added", chat_session)

            if sessions_to_update:
                updated += database.update_sessions_batch(sessions_to_update)
                if on_progress:
                    for chat_session in sessions_to_update:
                        on_progress("updated", chat_session)

    return RefreshResult(added=added, updated=updated, skipped=skipped, mode=RefreshMode.FULL if full else RefreshMode.INCREMENTAL)


def run_enrichment(
    database: Database,
    on_progress: ProgressCallback | None = None,
) -> EnrichResult:
    """Enrich CLI sessions from Chronicle's built-in session store.

    Discovers sessions that need enrichment (new or with more turns than
    previously enriched) and sessions that need reparsing (outdated parser
    version), parses their ``events.jsonl`` files, and writes enriched data
    to the ``cst_*`` tables.  Also cleans up orphaned ``cst_sessions`` rows
    whose built-in session has been deleted.

    Args:
        database: Open :class:`~copilot_session_tools.Database`.
        on_progress: Optional callback invoked per enrichment event.
            Event names: ``"enriched"``, ``"reparsed"``, ``"enrich_failed"``.

    Returns:
        An :class:`EnrichResult` with ``enriched``, ``reparsed``,
        ``failed``, and ``orphaned`` counts.
    """
    enriched = 0
    reparsed = 0
    failed = 0

    from copilot_session_tools import __version__

    # Discover sessions needing enrichment (new or stale)
    try:
        needing_enrichment = database.discover_sessions_needing_enrichment()
    except Exception:
        needing_enrichment = []  # Built-in sessions table may not exist

    for entry in needing_enrichment:
        sid = entry["session_id"]
        error = enrich_single_session(database, sid, validate=False)
        if error is None:
            enriched += 1
            if on_progress:
                on_progress("enriched", sid)
        else:
            # Source file gone or unparseable — stamp version so session
            # stops appearing in "needs enrichment" queries.
            database.update_enrichment_version(sid, __version__)
            failed += 1
            if on_progress:
                on_progress("enrich_failed", error)

    # Reparse sessions with outdated parser version
    try:
        needing_reparse = database.get_sessions_needing_reparse(PARSER_VERSION)
    except Exception:
        needing_reparse = []

    for entry in needing_reparse:
        sid = entry["session_id"]
        error = enrich_single_session(database, sid, validate=False)
        if error is None:
            reparsed += 1
            if on_progress:
                on_progress("reparsed", sid)
        else:
            # Source gone — stamp version so it stops reappearing
            database.update_enrichment_version(sid, __version__)
            failed += 1
            if on_progress:
                on_progress("enrich_failed", error)

    # Re-enrich sessions with outdated enrichment_version

    try:
        needing_version_refresh = database.get_sessions_needing_version_refresh(__version__)
    except Exception:
        needing_version_refresh = []

    already_processed = {e["session_id"] for e in needing_enrichment} | {e["session_id"] for e in needing_reparse}
    vscode_version_stale = 0
    for entry in needing_version_refresh:
        sid = entry["session_id"]
        if sid in already_processed:
            continue
        session_type = entry.get("type", "")
        if session_type == "cli":
            # CLI sessions: re-enrich from events.jsonl (may extract more data)
            error = enrich_single_session(database, sid, validate=False)
            if error is None:
                reparsed += 1
                if on_progress:
                    on_progress("reparsed", sid)
            else:
                # Source file gone or unparseable — stamp version anyway so
                # the "needs refresh" banner clears. The session data is
                # already in cst_* tables from the original enrichment.
                database.update_enrichment_version(sid, __version__)
                failed += 1
                if on_progress:
                    on_progress("enrich_failed", error)
        else:
            # VS Code sessions: re-parsed via run_refresh(), which now stamps
            # enrichment_version. Just stamp the version here so the banner
            # clears. A full rebuild (run_refresh(full=True)) will re-parse
            # source files if the parser actually changed.
            database.update_enrichment_version(sid, __version__)
            vscode_version_stale += 1

    # vscode_version_stale are not truly reparsed — just version-stamped.
    # Don't inflate reparsed count.

    # Cleanup orphaned cst_sessions
    try:
        orphaned_ids = database.cleanup_orphaned_cst_sessions()
    except Exception:
        orphaned_ids = []

    return EnrichResult(enriched=enriched, reparsed=reparsed, failed=failed, orphaned=len(orphaned_ids))


def enrich_single_session(
    database: Database,
    session_id: str,
    *,
    validate: bool = True,
) -> str | None:
    """Parse a CLI session's ``events.jsonl`` and enrich it in *database*.

    Args:
        database: Open :class:`~copilot_session_tools.Database`.
        session_id: The CLI session ID (UUID-like hex string).
        validate: When ``True`` (default), reject *session_id* values that
            don't match the expected hex-and-hyphens format.

    Returns:
        ``None`` on success, or an error message string on failure.
    """
    if validate and not _SESSION_ID_RE.match(session_id):
        return f"Invalid session ID format: {session_id}"

    events_file = SESSION_STATE_DIR / session_id / "events.jsonl"
    if not events_file.exists():
        return f"events.jsonl not found for session {session_id}"

    parsed = _parse_cli_jsonl_file(events_file)
    if parsed is None:
        return f"Failed to parse events.jsonl for session {session_id}"

    database.enrich_session(parsed)
    return None
