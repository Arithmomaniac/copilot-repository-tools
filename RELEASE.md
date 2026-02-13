# Release Process

This document describes how to release a new version of `copilot-session-tools` to PyPI.

## Prerequisites (One-Time Setup)

### 1. Register as Trusted Publisher on PyPI

Before the first release, register the GitHub repo as a trusted publisher:

**PyPI:**
1. Go to https://pypi.org/manage/account/publishing/
2. Add a new pending publisher:
   - PyPI project name: `copilot-session-tools`
   - Owner: `Arithmomaniac`
   - Repository: `copilot-session-tools`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

**TestPyPI:**
1. Go to https://test.pypi.org/manage/account/publishing/
2. Add a new pending publisher with the same details, but:
   - Environment name: `testpypi`

### 2. Create GitHub Environments

1. Go to https://github.com/Arithmomaniac/copilot-session-tools/settings/environments
2. Create environment `pypi`
   - Optionally add a deployment protection rule (required reviewers) for extra safety
3. Create environment `testpypi`

## Releasing a New Version

### 1. Bump the Version

```bash
python scripts/bump_version.py <new-version>
```

This updates the version in both `pyproject.toml` and `src/copilot_session_tools/__init__.py`.

### 2. Update the Changelog

Add a new section to `CHANGELOG.md` for the new version:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- ...

### Changed
- ...

### Fixed
- ...
```

### 3. Commit and Tag

```bash
git add pyproject.toml src/copilot_session_tools/__init__.py CHANGELOG.md
git commit -m "Release vX.Y.Z"
git tag vX.Y.Z
git push origin main --tags
```

### 4. What Happens Automatically

The `release.yml` workflow will:

1. **Lint & Test** — Runs ruff, ty, and pytest to validate the release
2. **Build** — Creates wheel and sdist, verifies tag matches package version
3. **Publish to TestPyPI** — Uploads to test.pypi.org first
4. **Publish to PyPI** — Uploads to pypi.org
5. **Create GitHub Release** — Auto-generates release notes from commits

### 5. Verify the Release

```bash
# Check TestPyPI
pip install --index-url https://test.pypi.org/simple/ copilot-session-tools

# Check PyPI
pip install copilot-session-tools

# Verify version
python -c "import copilot_session_tools; print(copilot_session_tools.__version__)"
```

## Version Numbering

This project follows [Semantic Versioning](https://semver.org/):

- **MAJOR** (X.0.0): Breaking changes to the public API or database schema
- **MINOR** (0.X.0): New features, backward-compatible
- **PATCH** (0.0.X): Bug fixes, backward-compatible

The CI enforces that every PR to `main` must bump the version.

## Troubleshooting

### Tag doesn't match package version
The release workflow verifies that the git tag matches `pyproject.toml`. If they don't match:
```bash
git tag -d vX.Y.Z           # delete local tag
git push --delete origin vX.Y.Z  # delete remote tag
# Fix version, re-tag, and push
```

### Trusted Publisher not working
- Verify the publisher is registered on PyPI/TestPyPI with the exact workflow filename and environment name
- Check that the GitHub Environment exists and matches
- Ensure the workflow has `permissions: id-token: write`
