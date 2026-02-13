# Copilot Instructions

This document provides instructions for GitHub Copilot when working on this repository.

## Code Quality Requirements

Before completing any code changes, you MUST run the following checks and ensure they pass:

### 1. Linting with Ruff

Run the linter to check for code style and potential issues:

```bash
uv run ruff check .
```

If there are issues, you can auto-fix many of them with:

```bash
uv run ruff check . --fix
```

### 2. Format Check with Ruff

Ensure code is properly formatted:

```bash
uv run ruff format --check .
```

To auto-format:

```bash
uv run ruff format .
```

### 3. Type Checking with Ty

Run the type checker to ensure type safety:

```bash
uv run ty check
```

### 4. Run Affected Tests

Rather than running the full test suite, identify and run only the tests affected by your changes. The full test suite is enforced by CI on every PR.

```bash
# Map changed source files to their test files:
#   src/copilot_session_tools/database.py  → tests/test_database.py
#   src/copilot_session_tools/scanner/     → tests/test_scanner.py
#   src/copilot_session_tools/cli.py       → tests/test_cli.py
#   src/copilot_session_tools/web/         → tests/test_webapp.py
#   src/copilot_session_tools/markdown_exporter.py → tests/test_markdown_exporter.py
#   src/copilot_session_tools/html_exporter.py     → tests/test_html_exporter.py

# Example: if you changed database.py and scanner/
uv run pytest tests/test_database.py tests/test_scanner.py -v
```

If unsure which tests are affected, run the full suite:

```bash
uv run pytest tests/ --ignore=tests/test_webapp_e2e.py -v
```

## Workflow Summary

Before committing any changes:

1. Run `uv run ruff check .` - fix any linting errors
2. Run `uv run ruff format .` - format the code
3. Run `uv run ty check` - fix any type errors  
4. Run `uv run pytest tests/test_<affected>.py` - run affected tests (CI runs the full suite)

Linting, formatting, and type checks are also enforced by a sessionEnd hook. The full test suite is enforced in CI via GitHub Actions and must pass before merging.

## Project Structure

- `src/copilot_session_tools/` - Main package (database, scanner, CLI, web)
- `tests/` - Test suite

## Dependencies

This project uses `uv` for dependency management. To install dependencies:

```bash
uv sync --all-extras
```
