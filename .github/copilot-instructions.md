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

### 4. Run Tests

Run the test suite to verify changes don't break existing functionality:

```bash
uv run pytest tests/ --ignore=tests/test_webapp_e2e.py -v
```

## Workflow Summary

Before committing any changes:

1. Run `uv run ruff check .` - fix any linting errors
2. Run `uv run ruff format .` - format the code
3. Run `uv run ty check` - fix any type errors  
4. Run `uv run pytest tests/ --ignore=tests/test_webapp_e2e.py` - ensure tests pass

All of these checks are also enforced in CI via GitHub Actions and must pass before merging.

## Project Structure

- `packages/common/` - Shared library code
- `packages/cli/` - Command-line interface
- `packages/web/` - Web application
- `tests/` - Test suite

## Dependencies

This project uses `uv` for dependency management. To install dependencies:

```bash
uv sync --all-packages
```
