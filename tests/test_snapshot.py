"""Snapshot/regression tests for scanner + exporter output stability.

These tests parse fixed session files and compare their exported output
(markdown, HTML) against saved baselines using pytest-regressions.

Fixtures and baselines live in tests/snapshots/ (gitignored).
Tests are skipped when fixtures are absent.

Usage:
    # Normal run (compares against baselines)
    uv run pytest tests/test_snapshot.py -v

    # Regenerate all baselines after intentional changes
    uv run pytest tests/test_snapshot.py --regen-all -v

    # Regenerate only failing baselines
    uv run pytest tests/test_snapshot.py --force-regen -v
"""

from pathlib import Path

import pytest

from copilot_session_tools.html_exporter import session_to_html
from copilot_session_tools.markdown_exporter import session_to_markdown
from copilot_session_tools.scanner import SessionFileInfo, parse_session_file
from copilot_session_tools.scanner.cli import _parse_cli_jsonl_file

SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"
FIXTURES_DIR = SNAPSHOTS_DIR / "fixtures"
BASELINES_DIR = SNAPSHOTS_DIR / "baselines"


# --- CLI Session Snapshots ---

_cli_events = FIXTURES_DIR / "cli" / "events.jsonl"
_cli_fixture_exists = _cli_events.exists() if FIXTURES_DIR.exists() else False


@pytest.mark.skipif(not _cli_fixture_exists, reason="CLI fixture not populated")
class TestCLISessionSnapshot:
    """Snapshot tests for CLI session parsing and export."""

    @pytest.fixture(autouse=True)
    def parse_session(self):
        self.session = _parse_cli_jsonl_file(_cli_events)
        assert self.session is not None, f"Failed to parse CLI fixture: {_cli_events}"
        self.session.source_file = "cli"

    def test_markdown_export(self, file_regression):
        md = session_to_markdown(
            self.session,
            include_diffs=True,
            include_tool_inputs=True,
            include_thinking=True,
        )
        file_regression.check(md, fullpath=BASELINES_DIR / "cli.md", encoding="utf-8")

    def test_html_export(self, file_regression):
        html = session_to_html(self.session)
        file_regression.check(html, fullpath=BASELINES_DIR / "cli.html", encoding="utf-8")


# --- VS Code Session Snapshots ---

# Discover all vscode-* fixture dirs and their session files
# Each entry is (unique_key, path, file_type) — key includes format suffix for uniqueness
_vscode_fixtures: list[tuple[str, Path, str]] = []
if FIXTURES_DIR.exists():
    for d in sorted(FIXTURES_DIR.iterdir()):
        if not d.is_dir() or not d.name.startswith("vscode-"):
            continue
        for f in sorted(d.iterdir()):
            if f.suffix in (".json", ".jsonl", ".vscdb"):
                # Use dir name as key (dirs are named per-format: vscode-insider-json, etc.)
                # Append file stem if dir has multiple files to avoid collisions
                key = d.name
                _vscode_fixtures.append((key, f, f.suffix.lstrip(".")))


@pytest.mark.skipif(not _vscode_fixtures, reason="VS Code fixtures not populated")
class TestVSCodeSessionSnapshot:
    """Snapshot tests for VS Code session parsing and export."""

    @pytest.fixture(
        autouse=True,
        params=list(range(len(_vscode_fixtures))) if _vscode_fixtures else [0],
        ids=[fx[0] for fx in _vscode_fixtures] if _vscode_fixtures else ["skip"],
    )
    def parse_session(self, request):
        if not _vscode_fixtures:
            pytest.skip("No VS Code fixtures")
        idx = request.param
        name, path, file_type = _vscode_fixtures[idx]
        edition = "insider" if "insider" in name else "stable"
        file_info = SessionFileInfo(
            file_path=path.resolve(),
            file_type=file_type,
            session_type="vscode",
            vscode_edition=edition,
            mtime=path.stat().st_mtime,
            size=path.stat().st_size,
            workspace_name=None,
            workspace_path=None,
        )
        sessions = parse_session_file(file_info)
        if not sessions:
            pytest.skip(f"No sessions parsed from {path}")
        self.session = sessions[0]
        # Neutralize source path to avoid OS/cwd-dependent snapshots
        self.session.source_file = name
        self.fixture_name = name

    def test_markdown_export(self, file_regression):
        md = session_to_markdown(
            self.session,
            include_diffs=True,
            include_tool_inputs=True,
            include_thinking=True,
        )
        file_regression.check(md, fullpath=BASELINES_DIR / f"{self.fixture_name}.md", encoding="utf-8")

    def test_html_export(self, file_regression):
        html = session_to_html(self.session)
        file_regression.check(html, fullpath=BASELINES_DIR / f"{self.fixture_name}.html", encoding="utf-8")
