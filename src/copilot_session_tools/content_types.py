"""Content type constants for controlling what gets included in exports and searches.

Provides a unified vocabulary for the --include / --exclude CLI flags
and programmatic content_set parameters used by exporters, search, and the web API.
"""

from __future__ import annotations

import typer

# ---------------------------------------------------------------------------
# Export content types
# ---------------------------------------------------------------------------

CONTENT_TYPES: dict[str, str] = {
    "thinking": "Include thinking/reasoning blocks",
    "diffs": "Include file change diffs",
    "tool-inputs": "Include tool input parameters",
    "agent-details": "Include full agent/subagent content",
    "tools": "Include tool invocation blocks",
    "commands": "Include command run sections",
    "file-changes": "Include file change sections",
}

DEFAULT_INCLUDES: set[str] = {"agent-details", "tools", "commands", "file-changes"}

# ---------------------------------------------------------------------------
# Search content types
# ---------------------------------------------------------------------------

SEARCH_CONTENT_TYPES: dict[str, str] = {
    "messages": "Search message content",
    "thinking": "Search thinking/reasoning blocks",
    "diffs": "Search file change diffs",
    "tool-inputs": "Search tool input parameters",
    "agent-details": "Search inside agent/subagent content (nesting gate)",
    "tools": "Search tool invocations (names + results)",
    "commands": "Search command runs",
    "file-changes": "Search file change paths + explanations",
}

SEARCH_DEFAULT_INCLUDES: set[str] = set(SEARCH_CONTENT_TYPES.keys())


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _parse_content_tokens(
    raw: list[str] | None,
    known: dict[str, str],
) -> set[str]:
    """Split comma-separated token lists and warn on unknown tokens.

    Args:
        raw: Raw strings from CLI flags (may contain comma-separated values).
        known: The vocabulary dict to validate against.

    Returns:
        Set of validated token strings.
    """
    tokens: set[str] = set()
    if not raw:
        return tokens
    for item in raw:
        for token in item.split(","):
            token = token.strip()
            if not token:
                continue
            if token not in known:
                typer.echo(f"Warning: unknown content type '{token}', ignoring", err=True)
            else:
                tokens.add(token)
    return tokens


# ---------------------------------------------------------------------------
# Resolve functions
# ---------------------------------------------------------------------------


def resolve_content_set(
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> set[str]:
    """Resolve --include/--exclude lists into a final content set.

    Starts from DEFAULT_INCLUDES, adds any --include tokens,
    then removes any --exclude tokens.  Tokens may be comma-separated.

    Args:
        include: Content types to add (may contain comma-separated values).
        exclude: Content types to remove (may contain comma-separated values).

    Returns:
        The resolved set of content type keys.
    """
    result = set(DEFAULT_INCLUDES)
    result |= _parse_content_tokens(include, CONTENT_TYPES)
    for token in _parse_content_tokens(exclude, CONTENT_TYPES):
        result.discard(token)
    return result


def resolve_search_content_set(
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> set[str]:
    """Resolve --include/--exclude lists into a search content set.

    Unlike export, ``--include`` is **exclusive** (replaces defaults) because
    the search default is ALL tokens — additive semantics would be a no-op.
    ``--exclude`` always subtracts from the active set.

    Parent-child auto-inclusion rules:
    - ``diffs`` implies ``file-changes``
    - ``tool-inputs`` implies ``tools``

    Args:
        include: Content types to search (exclusive; replaces defaults).
        exclude: Content types to remove (may contain comma-separated values).

    Returns:
        The resolved set of search content type keys.

    Raises:
        typer.BadParameter: If the resulting set is empty.
    """
    inc_tokens = _parse_content_tokens(include, SEARCH_CONTENT_TYPES)
    result = inc_tokens if inc_tokens else set(SEARCH_DEFAULT_INCLUDES)

    for token in _parse_content_tokens(exclude, SEARCH_CONTENT_TYPES):
        result.discard(token)
        # When excluding a parent, also remove its children
        # (otherwise auto-inclusion re-adds the parent right back)
        if token == "file-changes":
            result.discard("diffs")
        elif token == "tools":
            result.discard("tool-inputs")

    # Parent-child auto-inclusion
    if "diffs" in result:
        result.add("file-changes")
    if "tool-inputs" in result:
        result.add("tools")

    if not result:
        raise typer.BadParameter("No content types remaining after filtering")

    return result
