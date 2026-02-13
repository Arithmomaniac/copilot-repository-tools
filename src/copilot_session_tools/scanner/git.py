"""Git repository URL detection and normalization."""

import re
import subprocess
from pathlib import Path

# Cache for detect_repository_url to avoid repeated subprocess calls
_repository_url_cache: dict[str, str | None] = {}


def _clear_repository_url_cache() -> None:
    """Clear the repository URL cache (for testing)."""
    _repository_url_cache.clear()


def detect_repository_url(workspace_path: str | None) -> str | None:
    """Detect the git repository URL from a workspace path.

    Results are cached to avoid repeated subprocess calls for the same workspace.

    This function looks for a .git directory and reads the remote origin URL.
    This enables repository-scoped memories that work across multiple worktrees
    of the same repository.

    Args:
        workspace_path: Path to the workspace directory.

    Returns:
        The normalized repository URL (e.g., "github.com/owner/repo"), or None
        if no git repository is found or no remote is configured.
    """
    if not workspace_path:
        return None

    # Check cache first
    if workspace_path in _repository_url_cache:
        return _repository_url_cache[workspace_path]

    workspace = Path(workspace_path)
    result_url: str | None = None

    # Check if this is a git repository (handles both regular repos and worktrees)
    try:
        # Use git rev-parse to find the actual git directory
        # This works for both regular repos and worktrees
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],  # noqa: S607 - trusted git command
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            # Get the remote origin URL
            result = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],  # noqa: S607 - trusted git command
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                remote_url = result.stdout.strip()
                if remote_url:
                    result_url = _normalize_git_url(remote_url)

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Cache the result (including None)
    _repository_url_cache[workspace_path] = result_url
    return result_url


def _normalize_git_url(url: str) -> str:
    """Normalize a git remote URL to a consistent format.

    Converts various URL formats to a normalized form:
    - https://github.com/owner/repo.git -> github.com/owner/repo
    - git@github.com:owner/repo.git -> github.com/owner/repo
    - ssh://git@github.com/owner/repo.git -> github.com/owner/repo

    Args:
        url: The raw git remote URL.

    Returns:
        Normalized URL string in format "host/owner/repo".
    """
    # Remove trailing .git
    url = url.rstrip("/")
    url = url.removesuffix(".git")

    # Handle SSH format: git@github.com:owner/repo
    ssh_match = re.match(r"^git@([^:]+):(.+)$", url)
    if ssh_match:
        host, path = ssh_match.groups()
        return f"{host}/{path}"

    # Handle SSH URL format: ssh://git@github.com/owner/repo
    ssh_url_match = re.match(r"^ssh://(?:git@)?([^/]+)/(.+)$", url)
    if ssh_url_match:
        host, path = ssh_url_match.groups()
        return f"{host}/{path}"

    # Handle HTTPS format: https://github.com/owner/repo
    https_match = re.match(r"^https?://([^/]+)/(.+)$", url)
    if https_match:
        host, path = https_match.groups()
        return f"{host}/{path}"

    # Return as-is if we can't parse it
    return url
