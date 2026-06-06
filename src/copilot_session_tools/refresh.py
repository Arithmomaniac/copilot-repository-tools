"""Shared refresh/scan logic for the CLI and web interface.

This module provides a single implementation of the session scan-and-import
workflow so that the ``scan`` CLI command and the web ``/refresh`` endpoint
both exercise the same code path.
"""

import os
import re
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from copilot_session_tools import Database
from copilot_session_tools.scanner import PARSER_VERSION, SessionFileInfo, parse_session_file, scan_session_files
from copilot_session_tools.scanner.cli import _parse_cli_jsonl_file
from copilot_session_tools.scanner.models import ChatSession

# Regex for validating session IDs (hex + hyphens, i.e. UUIDs)
_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F-]+$")

# Default location of CLI session-state directories
SESSION_STATE_DIR = Path.home() / ".copilot" / "session-state"

# Default number of parallel workers for file parsing.
# ProcessPoolExecutor is used so that C-extension JSON parsers (which hold
# the GIL) can run on separate cores.  4 workers is the sweet-spot on most
# machines — beyond that, process-spawn overhead on Windows and I/O
# contention eat into the gains.
DEFAULT_PARSE_WORKERS = min(4, max(1, (os.cpu_count() or 2) // 2))

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


def _parse_files_parallel(files: list[SessionFileInfo], workers: int | None = None) -> list[list]:
    """Parse session files in parallel using a process pool.

    Uses :class:`ProcessPoolExecutor` so that C-extension JSON parsers
    (which hold the GIL) can run across multiple cores.  Falls back to
    sequential execution when *workers* is 1 (avoids pickling overhead
    and keeps test-friendly when ``parse_session_file`` is mocked).

    Args:
        files: List of session file info objects to parse.
        workers: Number of worker processes.  Defaults to
            :data:`DEFAULT_PARSE_WORKERS`.

    Returns:
        List of parse results (each is a list of ChatSession objects).
    """
    if not files:
        return []
    n = workers if workers is not None else DEFAULT_PARSE_WORKERS
    if n <= 1:
        return [parse_session_file(f) for f in files]
    with ProcessPoolExecutor(max_workers=n) as executor:
        return list(executor.map(parse_session_file, files))


def _classify_and_batch_write(
    database: Database,
    parse_results: list[list],
    existing_ids: set[str] | None,
    on_progress: ProgressCallback | None = None,
) -> tuple[int, int]:
    """Classify parsed sessions as new/existing and batch-write to the database.

    Args:
        database: Open Database to write into.
        parse_results: Nested list of parsed ChatSession objects.
        existing_ids: Set of known session IDs (used for classification).
            If None, each session is checked individually via database.get_session().
        on_progress: Optional callback for add/update events.

    Returns:
        Tuple of (added_count, updated_count).
    """
    sessions_to_add = []
    sessions_to_update = []

    for sessions in parse_results:
        for chat_session in sessions:
            if existing_ids is not None:
                is_existing = chat_session.session_id in existing_ids
            else:
                is_existing = database.get_session(chat_session.session_id) is not None
            if is_existing:
                sessions_to_update.append(chat_session)
            else:
                sessions_to_add.append(chat_session)

    added = 0
    updated = 0

    with database.batch_connection():
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

    return added, updated


def _parse_cli_entry(entry: dict) -> tuple[str, ChatSession | str]:
    """Parse a single CLI session's ``events.jsonl``.  Runs in a worker process.

    Must be at module level for :class:`ProcessPoolExecutor` pickling on
    Windows (``spawn`` start method).

    Returns:
        ``(session_id, parsed_session_or_error_string)``
    """
    sid = entry["session_id"]
    if not _SESSION_ID_RE.match(sid):
        return sid, f"Invalid session ID format: {sid}"
    events_file = SESSION_STATE_DIR / sid / "events.jsonl"
    if not events_file.exists():
        return sid, f"events.jsonl not found for session {sid}"
    parsed = _parse_cli_jsonl_file(events_file)
    if parsed is None:
        return sid, f"Failed to parse events.jsonl for session {sid}"
    return sid, parsed


def _enrich_session_batch(
    database: Database,
    entries: list[dict],
    *,
    success_event: str,
    stamp_version_on_failure: bool = False,
    on_progress: ProgressCallback | None = None,
    skip_ids: set[str] | None = None,
    executor: ProcessPoolExecutor,
) -> tuple[int, int]:
    """Enrich a batch of sessions, handling errors and version stamping.

    Shared loop body for the three enrichment phases (discovery, reparse,
    version-refresh).  Uses :meth:`Database.batch_connection` to hold a
    single SQLite connection open for the entire batch, and parses
    ``events.jsonl`` files in parallel before writing results serially.

    Args:
        database: Open Database.
        entries: List of dicts with at least a 'session_id' key.
        success_event: Event name for on_progress on success (e.g. "enriched", "reparsed").
        stamp_version_on_failure: If True, stamp enrichment_version even on failure
            (defaults to False so failed sessions remain retryable).
        on_progress: Optional progress callback.
        skip_ids: Session IDs to skip (already processed in a prior phase).
        executor: Shared :class:`ProcessPoolExecutor` (caller manages lifetime).

    Returns:
        Tuple of (success_count, failed_count).
    """
    from copilot_session_tools import __version__

    # Filter to actionable entries
    actionable = [e for e in entries if not (skip_ids and e["session_id"] in skip_ids)]
    if not actionable:
        return 0, 0

    import sqlite3 as _sqlite3

    # Parse in parallel, write serially (single connection, FK=OFF).
    # Per-session try/except so one write failure doesn't roll back the batch.
    success = 0
    failed = 0

    with database.batch_connection():
        for sid, result in executor.map(_parse_cli_entry, actionable):
            if isinstance(result, str):
                # Error string from parse phase
                if stamp_version_on_failure:
                    database.update_enrichment_version(sid, __version__)
                failed += 1
                if on_progress:
                    on_progress("enrich_failed", result)
            else:
                try:
                    database.enrich_session(result)
                except _sqlite3.Error as exc:
                    failed += 1
                    if on_progress:
                        on_progress("enrich_failed", f"DB error for {sid}: {exc}")
                else:
                    success += 1
                    if on_progress:
                        on_progress(success_event, sid)

    return success, failed


def run_refresh(
    database: Database,
    storage_paths: list[tuple[str, str]] | None,
    full: bool = False,
    on_progress: ProgressCallback | None = None,
    workers: int | None = None,
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
        workers: Number of worker processes for parallel parsing.
            Defaults to :data:`DEFAULT_PARSE_WORKERS`.

    Returns:
        A :class:`RefreshResult` with ``added``, ``updated``,
        ``skipped``, and ``mode`` fields.
    """
    added = 0
    updated = 0
    skipped = 0

    if full:
        all_files = list(scan_session_files(storage_paths, include_cli=False))
        if all_files:
            parse_results = _parse_files_parallel(all_files, workers=workers)
            existing_ids = set(database.get_all_session_ids())
            batch_added, batch_updated = _classify_and_batch_write(
                database,
                parse_results,
                existing_ids,
                on_progress,
            )
            added += batch_added
            updated += batch_updated
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
            parse_results = _parse_files_parallel(files_to_update, workers=workers)
            # No pre-loaded existing_ids — check each session individually
            batch_added, batch_updated = _classify_and_batch_write(
                database,
                parse_results,
                None,
                on_progress,
            )
            added += batch_added
            updated += batch_updated

    return RefreshResult(added=added, updated=updated, skipped=skipped, mode=RefreshMode.FULL if full else RefreshMode.INCREMENTAL)


def run_enrichment(
    database: Database,
    on_progress: ProgressCallback | None = None,
    workers: int | None = None,
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
        workers: Number of worker processes for parallel parsing.
            Defaults to :data:`DEFAULT_PARSE_WORKERS`.

    Returns:
        An :class:`EnrichResult` with ``enriched``, ``reparsed``,
        ``failed``, and ``orphaned`` counts.
    """
    from copilot_session_tools import __version__

    enriched = 0
    reparsed = 0
    failed = 0

    n = workers if workers is not None else DEFAULT_PARSE_WORKERS

    # Share a single process pool across all enrichment phases to avoid
    # repeated spawn/teardown overhead (significant on Windows).
    with ProcessPoolExecutor(max_workers=n) as pool:
        # Phase 1: Discover sessions needing enrichment (new or stale)
        try:
            needing_enrichment = database.discover_sessions_needing_enrichment()
        except Exception:
            needing_enrichment = []  # Built-in sessions table may not exist

        phase1_ok, phase1_fail = _enrich_session_batch(
            database,
            needing_enrichment,
            success_event="enriched",
            on_progress=on_progress,
            executor=pool,
        )
        enriched += phase1_ok
        failed += phase1_fail

        # Phase 2: Reparse sessions with outdated parser version
        try:
            needing_reparse = database.get_sessions_needing_reparse(PARSER_VERSION)
        except Exception:
            needing_reparse = []

        already_processed = {e["session_id"] for e in needing_enrichment}
        phase2_ok, phase2_fail = _enrich_session_batch(
            database,
            needing_reparse,
            success_event="reparsed",
            on_progress=on_progress,
            skip_ids=already_processed,
            executor=pool,
        )
        reparsed += phase2_ok
        failed += phase2_fail

        # Phase 3: Re-enrich sessions with outdated enrichment_version
        try:
            needing_version_refresh = database.get_sessions_needing_version_refresh(__version__)
        except Exception:
            needing_version_refresh = []

        already_processed |= {e["session_id"] for e in needing_reparse}

        # CLI sessions get re-enriched; VS Code sessions just get version-stamped
        cli_entries = [e for e in needing_version_refresh if e.get("type", "") == "cli" and e["session_id"] not in already_processed]
        phase3_ok, phase3_fail = _enrich_session_batch(
            database,
            cli_entries,
            success_event="reparsed",
            on_progress=on_progress,
            executor=pool,
        )
        reparsed += phase3_ok
        failed += phase3_fail

    # VS Code sessions: just stamp the version so the banner clears
    vscode_entries = [e for e in needing_version_refresh if e["session_id"] not in already_processed and e.get("type", "") != "cli"]
    if vscode_entries:
        with database.batch_connection():
            for entry in vscode_entries:
                database.update_enrichment_version(entry["session_id"], __version__)

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
    parsed = parse_single_cli_session(session_id, validate=validate)
    if isinstance(parsed, str):
        return parsed

    database.enrich_session(parsed)
    return None


def parse_single_cli_session(
    session_id: str,
    *,
    validate: bool = True,
) -> ChatSession | str:
    """Parse a CLI session's ``events.jsonl`` without writing to the database.

    Returns a :class:`ChatSession` on success, or an error message string on failure.
    """
    if validate and not _SESSION_ID_RE.match(session_id):
        return f"Invalid session ID format: {session_id}"

    events_file = SESSION_STATE_DIR / session_id / "events.jsonl"
    if not events_file.exists():
        return f"events.jsonl not found for session {session_id}"

    parsed = _parse_cli_jsonl_file(events_file)
    if parsed is None:
        return f"Failed to parse events.jsonl for session {session_id}"

    return parsed
