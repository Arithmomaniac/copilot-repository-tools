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

## Visual Self-Verification

This project renders Copilot chat sessions as HTML (web viewer) and Markdown (export). Rendering changes are invisible to unit tests alone — you must verify them visually.

### Snapshot baselines (automated regression detection)

Golden-file baselines in `tests/snapshots/baselines/` catch unintended rendering changes. The `tests/test_snapshot.py` suite parses real session fixtures through the scanner and exporters, then diffs the output against saved baselines using `pytest-regressions`.

```bash
# Run snapshot tests (compares against baselines)
uv run pytest tests/test_snapshot.py -v

# Regenerate only failing baselines after intentional changes
uv run pytest tests/test_snapshot.py --force-regen -v

# Regenerate ALL baselines from scratch
uv run pytest tests/test_snapshot.py --regen-all -v
```

**Important:** Baselines are gitignored but shared across worktrees via NTFS junctions pointing to `C:\_SRC\copilot-repository-tools.snapshots\`. When updating baselines for a PR, force-add them: `git add -f tests/snapshots/baselines/`.

After any change to the scanner, exporters, or web templates (`session.html`), run snapshot tests. If baselines change, regenerate them and verify the diff is intentional.

### Playwright screenshots (visual spot-checking)

For rendering changes (CSS, HTML structure, new content block types), capture screenshots via Playwright to verify the web viewer looks correct:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto("http://127.0.0.1:5000/session/<session-id>")
    page.wait_for_load_state("networkidle")
    page.screenshot(path="temp_export/screenshot.png", full_page=False)
    browser.close()
```

Upload the captured PNGs to yourself (use the `view` tool on the image file) to visually confirm:
- New content blocks render with readable formatting
- Layout is consistent with existing blocks
- No regressions in adjacent elements (CSS changes can cascade)
- Collapsible/interactive elements work in both collapsed and expanded states

### Showboat demos (documenting visual changes)

For rendering-heavy changes, create a [Showboat](https://github.com/simonw/showboat) demo document in `temp_export/` that combines prose, screenshots, and sample data into a single reviewable artifact. This serves as:
- **Proof of work** — the user can see exactly what changed before approving a PR
- **Visual changelog** — shows before/after or new rendering in context
- **Regression reference** — future scanner refreshes can compare against prior demos

```powershell
showboat init temp_export/demo.md "Feature: New Block Type"
showboat note temp_export/demo.md "## Collapsed view"
showboat image temp_export/demo.md "temp_export/collapsed.png"
showboat note temp_export/demo.md "## Expanded view"
showboat image temp_export/demo.md "temp_export/expanded.png"
```

Showboat demos are not committed — they live in `temp_export/` (gitignored) for session-scoped review.

## Project Structure

- `src/copilot_session_tools/` - Main package (database, scanner, CLI, web)
- `tests/` - Test suite
- `tests/snapshots/` - Snapshot fixtures and baselines (gitignored, shared via junctions)
- `temp_export/` - Working directory for demos, screenshots, and intermediate artifacts (gitignored)

## Dependencies

This project uses `uv` for dependency management. To install dependencies:

```bash
uv sync --all-extras
```
