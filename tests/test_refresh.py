"""Tests for the shared run_refresh() function in refresh.py."""

from pathlib import Path
from unittest.mock import patch

import pytest

from copilot_session_tools import ChatMessage, ChatSession, Database
from copilot_session_tools.refresh import RefreshMode, run_refresh
from copilot_session_tools.scanner.models import SessionFileInfo

# Force sequential parsing in tests so that mocked parse_session_file
# (a MagicMock) doesn't need to be pickled across process boundaries.
pytestmark = pytest.mark.usefixtures("_sequential_parse_workers")


@pytest.fixture(autouse=True)
def _sequential_parse_workers():
    with patch("copilot_session_tools.refresh.DEFAULT_PARSE_WORKERS", 1):
        yield


@pytest.fixture
def temp_db(tmp_path):
    """Return a fresh empty Database in a temp directory."""
    db_path = tmp_path / "refresh_test.db"
    return Database(db_path)


def _platform_path(posix_path: str) -> str:
    """Convert a POSIX path string to the OS-native representation.

    Ensures source_file values in test fixtures match str(Path(...)) on every
    platform (forward slashes on Linux/macOS, backslashes on Windows).
    """
    return str(Path(posix_path))


@pytest.fixture
def session_a():
    return ChatSession(
        session_id="refresh-session-a",
        workspace_name="ws-a",
        workspace_path="/home/user/ws-a",
        messages=[ChatMessage(role="user", content="Hello")],
        created_at="2025-01-01T00:00:00Z",
        vscode_edition="stable",
        source_file=_platform_path("/tmp/a.json"),
        source_file_mtime=1000.0,
        source_file_size=512,
    )


@pytest.fixture
def session_b():
    return ChatSession(
        session_id="refresh-session-b",
        workspace_name="ws-b",
        workspace_path="/home/user/ws-b",
        messages=[ChatMessage(role="user", content="World")],
        created_at="2025-01-02T00:00:00Z",
        vscode_edition="stable",
        source_file=_platform_path("/tmp/b.json"),
        source_file_mtime=2000.0,
        source_file_size=1024,
    )


class TestRunRefreshReturnType:
    """run_refresh() must always return a valid RefreshResult."""

    def test_returns_dataclass_with_correct_attrs(self, temp_db):
        with patch("copilot_session_tools.refresh.scan_session_files", return_value=iter([])):
            result = run_refresh(temp_db, storage_paths=[], full=False)
        assert hasattr(result, "added")
        assert hasattr(result, "updated")
        assert hasattr(result, "skipped")
        assert hasattr(result, "mode")

    def test_incremental_mode_label(self, temp_db):
        with patch("copilot_session_tools.refresh.scan_session_files", return_value=iter([])):
            result = run_refresh(temp_db, storage_paths=[], full=False)
        assert result.mode is RefreshMode.INCREMENTAL

    def test_full_mode_label(self, temp_db):
        with (
            patch("copilot_session_tools.refresh.scan_session_files", return_value=iter([])),
        ):
            result = run_refresh(temp_db, storage_paths=[], full=True)
        assert result.mode is RefreshMode.FULL


class TestRunRefreshIncrementalMode:
    """run_refresh() incremental path."""

    def _make_file_info(self, session: ChatSession) -> SessionFileInfo:
        return SessionFileInfo(
            file_path=Path(session.source_file or "/tmp/unknown.json"),
            file_type="json",
            session_type="vscode",
            vscode_edition="stable",
            mtime=session.source_file_mtime or 0.0,
            size=session.source_file_size or 0,
            workspace_name=session.workspace_name,
            workspace_path=session.workspace_path,
        )

    def test_new_session_is_added(self, temp_db, session_a):
        file_info = self._make_file_info(session_a)

        with (
            patch("copilot_session_tools.refresh.scan_session_files", return_value=iter([file_info])),
            patch("copilot_session_tools.refresh.parse_session_file", return_value=[session_a]),
        ):
            result = run_refresh(temp_db, storage_paths=[], full=False)

        assert result.added == 1
        assert result.updated == 0
        assert result.skipped == 0

    def test_unchanged_session_is_skipped(self, temp_db, session_a):
        """A session whose mtime/size matches stored metadata must be skipped."""
        temp_db.add_session(session_a)
        file_info = self._make_file_info(session_a)

        with (
            patch("copilot_session_tools.refresh.scan_session_files", return_value=iter([file_info])),
        ):
            result = run_refresh(temp_db, storage_paths=[], full=False)

        assert result.skipped == 1
        assert result.added == 0
        assert result.updated == 0

    def test_changed_session_is_updated(self, temp_db, session_a):
        """A session whose mtime differs from stored metadata must be re-imported."""
        temp_db.add_session(session_a)

        # Create a file_info with a different mtime to signal a change
        file_info = SessionFileInfo(
            file_path=Path(session_a.source_file or "/tmp/a.json"),
            file_type="json",
            session_type="vscode",
            vscode_edition="stable",
            mtime=(session_a.source_file_mtime or 0.0) + 999,  # changed mtime
            size=session_a.source_file_size or 0,
            workspace_name=session_a.workspace_name,
            workspace_path=session_a.workspace_path,
        )

        with (
            patch("copilot_session_tools.refresh.scan_session_files", return_value=iter([file_info])),
            patch("copilot_session_tools.refresh.parse_session_file", return_value=[session_a]),
        ):
            result = run_refresh(temp_db, storage_paths=[], full=False)

        assert result.updated == 1
        assert result.added == 0

    def test_multiple_sessions_mixed(self, temp_db, session_a, session_b):
        """New + unchanged sessions in a single incremental run."""
        # session_a is already stored and unchanged
        temp_db.add_session(session_a)

        file_info_a = self._make_file_info(session_a)
        file_info_b = self._make_file_info(session_b)

        with (
            patch(
                "copilot_session_tools.refresh.scan_session_files",
                return_value=iter([file_info_a, file_info_b]),
            ),
            patch("copilot_session_tools.refresh.parse_session_file", return_value=[session_b]),
        ):
            result = run_refresh(temp_db, storage_paths=[], full=False)

        assert result.skipped == 1
        assert result.added == 1
        assert result.updated == 0


class TestRunRefreshFullMode:
    """run_refresh() full path."""

    @staticmethod
    def _mock_full_scan(*sessions):
        """Create mocks for scan_session_files + parse_session_file that return the given sessions."""
        dummy_file = SessionFileInfo(
            file_path=Path("/tmp/dummy.json"),
            file_type="json",
            session_type="vscode",
            vscode_edition="stable",
            mtime=0.0,
            size=0,
        )
        # One dummy file per session batch
        files = [dummy_file]
        # parse_session_file returns all sessions for each file
        parse_fn = lambda _fi: list(sessions)  # noqa: E731
        return (
            patch("copilot_session_tools.refresh.scan_session_files", return_value=iter(files)),
            patch("copilot_session_tools.refresh.parse_session_file", side_effect=parse_fn),
        )

    def test_new_session_is_added(self, temp_db, session_a):
        mock_scan, mock_parse = self._mock_full_scan(session_a)
        with mock_scan, mock_parse:
            result = run_refresh(temp_db, storage_paths=[], full=True)

        assert result.added == 1
        assert result.updated == 0

    def test_existing_session_is_updated(self, temp_db, session_a):
        temp_db.add_session(session_a)

        mock_scan, mock_parse = self._mock_full_scan(session_a)
        with mock_scan, mock_parse:
            result = run_refresh(temp_db, storage_paths=[], full=True)

        assert result.updated == 1
        assert result.added == 0

    def test_multiple_sessions(self, temp_db, session_a, session_b):
        """Full mode with one new and one existing session."""
        temp_db.add_session(session_a)

        mock_scan, mock_parse = self._mock_full_scan(session_a, session_b)
        with mock_scan, mock_parse:
            result = run_refresh(temp_db, storage_paths=[], full=True)

        assert result.added == 1
        assert result.updated == 1
        assert result.skipped == 0
