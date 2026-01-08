"""Pytest configuration and shared fixtures."""

import json
from pathlib import Path

import pytest

# Path to sample files directory
SAMPLE_FILES_DIR = Path(__file__).parent.parent / "sample_files"

# List of sample session JSON files (UUIDs)
SAMPLE_SESSION_IDS = [
    "7add4c61-3ac2-42db-b672-cf461938cdfb",
    "7e50fbc3-c355-44b3-8cbf-2eb26a98ca62",
    "ba85770a-6cde-4333-a65b-e36d86eabb14",
    "d200f8e2-39a9-450b-b25c-fbb658f97d29",
]


def sample_files_exist() -> bool:
    """Check if sample files directory exists and contains JSON files."""
    if not SAMPLE_FILES_DIR.exists():
        return False
    return any((SAMPLE_FILES_DIR / f"{sid}.json").exists() for sid in SAMPLE_SESSION_IDS)


# Skip marker for tests requiring sample files
requires_sample_files = pytest.mark.skipif(
    not sample_files_exist(),
    reason="Sample files not available (sample_files/ directory missing or empty)"
)


@pytest.fixture
def sample_session_path():
    """Return path to first available sample session JSON file."""
    for sid in SAMPLE_SESSION_IDS:
        path = SAMPLE_FILES_DIR / f"{sid}.json"
        if path.exists():
            return path
    pytest.skip("No sample session files found")


@pytest.fixture
def all_sample_session_paths():
    """Return list of all available sample session JSON file paths."""
    paths = []
    for sid in SAMPLE_SESSION_IDS:
        path = SAMPLE_FILES_DIR / f"{sid}.json"
        if path.exists():
            paths.append(path)
    if not paths:
        pytest.skip("No sample session files found")
    return paths


@pytest.fixture
def sample_session_data(sample_session_path):
    """Load and return parsed JSON from first available sample session."""
    import orjson
    with open(sample_session_path, "rb") as f:
        return orjson.loads(f.read())
