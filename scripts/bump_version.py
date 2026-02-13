#!/usr/bin/env python3
"""Bump the package version in pyproject.toml and __init__.py atomically."""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
INIT_FILE = REPO_ROOT / "src" / "copilot_session_tools" / "__init__.py"

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def bump_version(new_version: str) -> None:
    if not VERSION_PATTERN.match(new_version):
        print(f"Error: '{new_version}' is not a valid version (expected X.Y.Z)", file=sys.stderr)
        sys.exit(1)

    # Update pyproject.toml
    pyproject_text = PYPROJECT.read_text(encoding="utf-8")
    old_match = re.search(r'^version = "([^"]+)"', pyproject_text, re.MULTILINE)
    if not old_match:
        print("Error: Could not find version in pyproject.toml", file=sys.stderr)
        sys.exit(1)
    old_version = old_match.group(1)

    if old_version == new_version:
        print(f"Version is already {new_version}, nothing to do.")
        sys.exit(0)

    pyproject_text = pyproject_text.replace(
        f'version = "{old_version}"',
        f'version = "{new_version}"',
        1,
    )
    PYPROJECT.write_text(pyproject_text, encoding="utf-8")
    print(f"  pyproject.toml: {old_version} → {new_version}")

    # Update __init__.py
    init_text = INIT_FILE.read_text(encoding="utf-8")
    init_text = re.sub(
        r'^__version__ = "[^"]+"',
        f'__version__ = "{new_version}"',
        init_text,
        count=1,
        flags=re.MULTILINE,
    )
    INIT_FILE.write_text(init_text, encoding="utf-8")
    print(f"  __init__.py:    {old_version} → {new_version}")

    print(f"\n✅ Version bumped to {new_version}")
    print("\nNext steps:")
    print("  git add pyproject.toml src/copilot_session_tools/__init__.py")
    print(f"  git commit -m 'Bump version to {new_version}'")
    print(f"  git tag v{new_version}")
    print("  git push origin main --tags")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <new-version>", file=sys.stderr)
        print(f"Example: python {sys.argv[0]} 0.2.0", file=sys.stderr)
        sys.exit(1)

    bump_version(sys.argv[1])
