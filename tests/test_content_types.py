"""Tests for content_types module and CLI --include/--exclude flags."""

import pytest
import typer
from typer.testing import CliRunner

from copilot_session_tools.content_types import (
    CONTENT_TYPES,
    DEFAULT_INCLUDES,
    SEARCH_CONTENT_TYPES,
    SEARCH_DEFAULT_INCLUDES,
    resolve_content_set,
    resolve_search_content_set,
)


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# resolve_content_set() unit tests
# ---------------------------------------------------------------------------


class TestResolveContentSet:
    """Unit tests for resolve_content_set()."""

    def test_resolve_default(self):
        """Default content_set matches DEFAULT_INCLUDES."""
        result = resolve_content_set()
        assert result == DEFAULT_INCLUDES

    def test_resolve_default_none_args(self):
        """Explicit None args produce defaults."""
        result = resolve_content_set(include=None, exclude=None)
        assert result == DEFAULT_INCLUDES

    def test_resolve_empty_include_exclude(self):
        """Empty lists don't change defaults."""
        result = resolve_content_set(include=[], exclude=[])
        assert result == DEFAULT_INCLUDES

    def test_resolve_include_single(self):
        """--include adds to defaults."""
        result = resolve_content_set(include=["thinking"])
        assert "thinking" in result
        # defaults still present
        for token in DEFAULT_INCLUDES:
            assert token in result

    def test_resolve_include_comma_separated(self):
        """--include supports comma-separated values."""
        result = resolve_content_set(include=["thinking,diffs"])
        assert "thinking" in result
        assert "diffs" in result

    def test_resolve_include_comma_separated_with_spaces(self):
        """Comma-separated values with surrounding spaces are trimmed."""
        result = resolve_content_set(include=["thinking , diffs"])
        assert "thinking" in result
        assert "diffs" in result

    def test_resolve_include_multiple_flags(self):
        """Multiple --include flags are additive."""
        result = resolve_content_set(include=["thinking", "diffs"])
        assert "thinking" in result
        assert "diffs" in result

    def test_resolve_exclude(self):
        """--exclude removes from defaults."""
        result = resolve_content_set(exclude=["agent-details"])
        assert "agent-details" not in result
        # other defaults still present
        assert (DEFAULT_INCLUDES - {"agent-details"}).issubset(result)

    def test_resolve_exclude_wins_over_include(self):
        """--exclude takes precedence over --include."""
        result = resolve_content_set(include=["thinking"], exclude=["thinking"])
        assert "thinking" not in result

    def test_resolve_unknown_token_ignored(self, capsys):
        """Unknown tokens produce a warning and are ignored."""
        result = resolve_content_set(include=["unknown-thing"])
        assert "unknown-thing" not in result
        # Defaults unchanged
        assert result == DEFAULT_INCLUDES
        captured = capsys.readouterr()
        assert "unknown-thing" in captured.err

    def test_resolve_all_content_types(self):
        """Including everything produces full set."""
        all_tokens = list(CONTENT_TYPES.keys())
        result = resolve_content_set(include=all_tokens)
        assert result == set(all_tokens)

    def test_resolve_exclude_nonexistent_is_harmless(self):
        """Excluding a token not in the set is a no-op (no error)."""
        result = resolve_content_set(exclude=["thinking"])
        assert result == DEFAULT_INCLUDES  # thinking wasn't in defaults anyway

    def test_resolve_exclude_all_defaults(self):
        """Excluding every default leaves an empty set."""
        result = resolve_content_set(exclude=list(DEFAULT_INCLUDES))
        assert result == set()

    def test_resolve_include_duplicate(self):
        """Including the same token twice doesn't cause issues."""
        result = resolve_content_set(include=["thinking", "thinking"])
        assert "thinking" in result
        assert result == DEFAULT_INCLUDES | {"thinking"}


# ---------------------------------------------------------------------------
# resolve_search_content_set() unit tests
# ---------------------------------------------------------------------------


class TestResolveSearchContentSet:
    """Unit tests for resolve_search_content_set()."""

    def test_default_returns_all_tokens(self):
        """No args → all 8 search tokens."""
        result = resolve_search_content_set()
        assert result == SEARCH_DEFAULT_INCLUDES
        assert len(result) == 8
        assert "messages" in result

    def test_include_is_exclusive(self):
        """--include replaces defaults (exclusive semantics)."""
        result = resolve_search_content_set(include=["tools"])
        assert result == {"tools"}

    def test_include_diffs_auto_adds_file_changes(self):
        """--include diffs → {diffs, file-changes} (parent auto-inclusion)."""
        result = resolve_search_content_set(include=["diffs"])
        assert result == {"diffs", "file-changes"}

    def test_include_tool_inputs_auto_adds_tools(self):
        """--include tool-inputs → {tool-inputs, tools} (parent auto-inclusion)."""
        result = resolve_search_content_set(include=["tool-inputs"])
        assert result == {"tool-inputs", "tools"}

    def test_exclude_messages(self):
        """--exclude messages → 7 tokens remaining."""
        result = resolve_search_content_set(exclude=["messages"])
        assert "messages" not in result
        assert len(result) == 7

    def test_exclude_everything_raises(self):
        """Excluding all tokens raises BadParameter."""
        import typer

        with pytest.raises(typer.BadParameter, match="No content types remaining"):
            resolve_search_content_set(exclude=list(SEARCH_CONTENT_TYPES.keys()))

    def test_include_then_exclude_same_raises(self):
        """--include tools --exclude tools → empty → raises."""
        import typer

        with pytest.raises(typer.BadParameter, match="No content types remaining"):
            resolve_search_content_set(include=["tools"], exclude=["tools"])

    def test_include_comma_separated(self):
        """Comma-separated include tokens work."""
        result = resolve_search_content_set(include=["messages,thinking"])
        assert result == {"messages", "thinking"}

    def test_exclude_comma_separated(self):
        """Comma-separated exclude tokens work."""
        result = resolve_search_content_set(exclude=["messages,thinking"])
        assert "messages" not in result
        assert "thinking" not in result
        assert len(result) == 6

    def test_unknown_token_warned(self, capsys):
        """Unknown tokens produce a warning and are ignored."""
        result = resolve_search_content_set(include=["bogus"])
        # No valid tokens parsed → falls back to defaults
        assert result == SEARCH_DEFAULT_INCLUDES
        captured = capsys.readouterr()
        assert "bogus" in captured.err

    def test_default_none_args(self):
        """Explicit None args produce all defaults."""
        result = resolve_search_content_set(include=None, exclude=None)
        assert result == SEARCH_DEFAULT_INCLUDES

    def test_auto_inclusion_after_exclude(self):
        """Excluding a parent also removes its children, preventing re-add loop."""
        # Include diffs only, exclude file-changes explicitly —
        # parent exclusion cascades to child, so result is empty → error
        with pytest.raises(typer.BadParameter):
            resolve_search_content_set(include=["diffs"], exclude=["file-changes"])


# ---------------------------------------------------------------------------
# CLI integration tests for --include / --exclude flags
# ---------------------------------------------------------------------------


class TestCLIIncludeExcludeFlags:
    """Test that --include/--exclude appear in CLI help and old flags are gone."""

    def test_export_markdown_help_shows_include_exclude(self, runner):
        """--include and --exclude appear in export-markdown help text."""
        from copilot_session_tools.cli import app

        result = runner.invoke(app, ["export-markdown", "--help"])
        assert result.exit_code == 0
        assert "--include" in result.output
        assert "--exclude" in result.output
        # old boolean flags should be gone
        assert "--include-thinking" not in result.output

    def test_export_html_help_shows_include_exclude(self, runner):
        """--include and --exclude appear in export-html help text."""
        from copilot_session_tools.cli import app

        result = runner.invoke(app, ["export-html", "--help"])
        assert result.exit_code == 0
        assert "--include" in result.output
        assert "--exclude" in result.output
        assert "--include-thinking" not in result.output
