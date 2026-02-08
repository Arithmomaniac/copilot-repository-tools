#!/usr/bin/env python3
"""Script to bump version in pyproject.toml and __init__.py files."""

import re
import sys
from pathlib import Path


def get_current_version(pyproject_path: Path) -> str:
    """Extract current version from pyproject.toml."""
    content = pyproject_path.read_text()
    match = re.search(r'^version = "([^"]+)"', content, re.MULTILINE)
    if not match:
        raise ValueError("Could not find version in pyproject.toml")
    return match.group(1)


def bump_version(version: str, part: str) -> str:
    """Bump version number based on part (major, minor, patch)."""
    major, minor, patch = map(int, version.split("."))

    if part == "major":
        return f"{major + 1}.0.0"
    elif part == "minor":
        return f"{major}.{minor + 1}.0"
    elif part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        raise ValueError(f"Invalid part: {part}. Use 'major', 'minor', or 'patch'")


def update_version_in_file(file_path: Path, old_version: str, new_version: str) -> None:
    """Update version string in a file."""
    content = file_path.read_text()
    
    # Replace version in pyproject.toml
    if file_path.name == "pyproject.toml":
        new_content = re.sub(
            r'^version = "[^"]+"',
            f'version = "{new_version}"',
            content,
            count=1,
            flags=re.MULTILINE,
        )
    # Replace __version__ in __init__.py
    else:
        new_content = re.sub(
            r'^__version__ = "[^"]+"',
            f'__version__ = "{new_version}"',
            content,
            count=1,
            flags=re.MULTILINE,
        )
    
    if new_content == content:
        raise ValueError(f"Failed to update version in {file_path}")
    
    file_path.write_text(new_content)
    print(f"Updated {file_path}: {old_version} → {new_version}")


def main():
    """Main entry point."""
    if len(sys.argv) != 2:
        print("Usage: python bump_version.py <major|minor|patch|X.Y.Z>")
        print()
        print("Examples:")
        print("  python bump_version.py patch   # 0.1.0 → 0.1.1")
        print("  python bump_version.py minor   # 0.1.0 → 0.2.0")
        print("  python bump_version.py major   # 0.1.0 → 1.0.0")
        print("  python bump_version.py 1.2.3   # Set version to 1.2.3")
        sys.exit(1)

    arg = sys.argv[1]
    
    # Get repository root (script is in scripts/ directory)
    repo_root = Path(__file__).parent.parent
    pyproject_path = repo_root / "pyproject.toml"
    init_path = repo_root / "src" / "copilot_repository_tools" / "__init__.py"
    
    # Get current version
    current_version = get_current_version(pyproject_path)
    print(f"Current version: {current_version}")
    
    # Determine new version
    if arg in ("major", "minor", "patch"):
        new_version = bump_version(current_version, arg)
    else:
        # Assume it's a version string
        if not re.match(r"^\d+\.\d+\.\d+$", arg):
            print(f"Error: Invalid version format: {arg}")
            print("Version must be in format X.Y.Z (e.g., 1.0.0)")
            sys.exit(1)
        new_version = arg
    
    print(f"New version: {new_version}")
    
    # Confirm
    response = input("Continue? (y/n): ")
    if response.lower() != "y":
        print("Aborted")
        sys.exit(1)
    
    # Update files
    update_version_in_file(pyproject_path, current_version, new_version)
    update_version_in_file(init_path, current_version, new_version)
    
    print()
    print("✓ Version updated successfully!")
    print()
    print("Next steps:")
    print(f"  1. Review changes: git diff")
    print(f"  2. Commit: git add -A && git commit -m 'Bump version to {new_version}'")
    print(f"  3. Tag: git tag v{new_version}")
    print(f"  4. Push: git push origin main && git push origin v{new_version}")


if __name__ == "__main__":
    main()
