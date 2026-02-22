"""Pytest configuration and shared fixtures."""

from pathlib import Path

import pytest

# Path to snapshot fixtures directory
FIXTURES_DIR = Path(__file__).parent / "snapshots" / "fixtures"

# VS Code JSON session fixtures (glob across vscode-*/session.json)
VSCODE_JSON_FIXTURES = sorted(FIXTURES_DIR.glob("vscode-*/session.json")) if FIXTURES_DIR.exists() else []


def sample_files_exist() -> bool:
    """Check if fixture directory exists and contains VS Code JSON session files."""
    return len(VSCODE_JSON_FIXTURES) > 0


# Skip marker for tests requiring sample files
requires_sample_files = pytest.mark.skipif(not sample_files_exist(), reason="Snapshot fixtures not available (tests/snapshots/fixtures/ missing or empty)")


@pytest.fixture
def sample_session_path():
    """Return path to first available VS Code JSON session fixture."""
    if VSCODE_JSON_FIXTURES:
        return VSCODE_JSON_FIXTURES[0]
    pytest.skip("No VS Code JSON session fixtures found")


@pytest.fixture
def all_sample_session_paths():
    """Return list of all available VS Code JSON session fixture paths."""
    if not VSCODE_JSON_FIXTURES:
        pytest.skip("No VS Code JSON session fixtures found")
    return list(VSCODE_JSON_FIXTURES)


@pytest.fixture
def sample_session_data(sample_session_path):
    """Load and return parsed JSON from first available VS Code JSON session."""
    import orjson

    with open(sample_session_path, "rb") as f:
        return orjson.loads(f.read())
