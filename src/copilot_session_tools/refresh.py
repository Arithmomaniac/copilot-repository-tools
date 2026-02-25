"""Shared refresh/scan logic for the CLI and web interface.

This module provides a single implementation of the session scan-and-import
workflow so that the ``scan`` CLI command and the web ``/refresh`` endpoint
both exercise the same code path.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from copilot_session_tools import Database, scan_chat_sessions
from copilot_session_tools.scanner import SessionFileInfo, parse_session_file, scan_session_files

# Number of threads for parallel file parsing
PARSE_WORKERS = 4

#: Callback signature: ``(event, item)`` where *event* is one of
#: ``"skipped"``, ``"added"``, ``"updated"`` and *item* is a
#: :class:`SessionFileInfo` (for skipped) or :class:`ChatSession`.
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


def run_refresh(
    database: Database,
    storage_paths: list[tuple[str, str]] | None,
    full: bool = False,
    include_cli: bool = True,
    on_progress: ProgressCallback | None = None,
) -> RefreshResult:
    """Scan for Copilot chat sessions and import them into *database*.

    Args:
        database: Open :class:`~copilot_session_tools.Database` to write into.
        storage_paths: List of ``(path, edition)`` tuples to search for VS Code
            sessions, or ``None`` to use the default VS Code storage paths.
        full: When ``True`` every discovered session is re-imported regardless
            of whether its source file has changed.  When ``False`` (the
            default) only files whose ``mtime`` or ``size`` differ from the
            stored metadata are re-imported.
        include_cli: Whether to also scan Copilot CLI session directories
            (default ``True``).
        on_progress: Optional callback invoked for every add/update/skip event.
            Receives ``(event, item)`` where *event* is ``"added"``,
            ``"updated"``, or ``"skipped"`` and *item* is the relevant
            :class:`~copilot_session_tools.scanner.SessionFileInfo` or
            :class:`~copilot_session_tools.scanner.models.ChatSession`.

    Returns:
        A :class:`RefreshResult` with ``added``, ``updated``,
        ``skipped``, and ``mode`` fields.
    """
    added = 0
    updated = 0
    skipped = 0

    if full:
        # Full mode: parse every discovered session and upsert it.
        for chat_session in scan_chat_sessions(storage_paths, include_cli=include_cli):
            if database.add_session(chat_session):
                added += 1
                if on_progress:
                    on_progress("added", chat_session)
            else:
                database.update_session(chat_session)
                updated += 1
                if on_progress:
                    on_progress("updated", chat_session)
    else:
        # Incremental mode: load all stored file metadata upfront so we can
        # skip unchanged files without hitting the DB once per file.
        stored_metadata = database.get_all_file_metadata()

        files_to_update: list[SessionFileInfo] = []
        for file_info in scan_session_files(storage_paths, include_cli=include_cli):
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

            for chat_session in sessions_to_update:
                database.update_session(chat_session)
                updated += 1
                if on_progress:
                    on_progress("updated", chat_session)

    return RefreshResult(added=added, updated=updated, skipped=skipped, mode=RefreshMode.FULL if full else RefreshMode.INCREMENTAL)
