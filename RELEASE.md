# Release Process

This document describes how to release new versions of copilot-repository-tools to PyPI.

## Overview

Releases are automated using GitHub Actions and PyPI trusted publishing (OIDC). When you push a version tag (e.g., `v0.1.1`), the release workflow automatically:

1. Validates that the tag matches the version in `pyproject.toml`
2. Runs linters and tests
3. Builds the package
4. Publishes to PyPI using trusted publishing (no API tokens needed)

## One-Time Setup (Repository Owner)

### 1. Configure PyPI Trusted Publishing

1. Create a PyPI account at https://pypi.org/account/register/
2. Enable 2FA on your PyPI account (required for trusted publishing)
3. Go to https://pypi.org/manage/account/publishing/
4. Add a "pending publisher" with these settings:
   - **PyPI Project Name**: `copilot-repository-tools`
   - **Owner**: `Arithmomaniac`
   - **Repository name**: `copilot-repository-tools`
   - **Workflow name**: `release.yml`
   - **Environment name**: `pypi`

5. The first time you push a version tag, PyPI will automatically create the project and link it to your repository

### 2. Configure GitHub Environment (Optional but Recommended)

1. Go to your repository Settings → Environments
2. Create an environment named `pypi`
3. (Optional) Add required reviewers to manually approve releases before they go to PyPI

## Releasing a New Version

### Step 1: Bump the Version

Use the version bump script to update version numbers in both locations:

```bash
# Bump patch version (0.1.0 → 0.1.1)
python scripts/bump_version.py patch

# Bump minor version (0.1.0 → 0.2.0)
python scripts/bump_version.py minor

# Bump major version (0.1.0 → 1.0.0)
python scripts/bump_version.py major

# Set specific version
python scripts/bump_version.py 1.2.3
```

The script will update:
- `pyproject.toml`: `version = "X.Y.Z"`
- `src/copilot_repository_tools/__init__.py`: `__version__ = "X.Y.Z"`

### Step 2: Review and Commit

```bash
# Review the changes
git diff

# Commit the version bump
git add pyproject.toml src/copilot_repository_tools/__init__.py
git commit -m "Bump version to X.Y.Z"
```

### Step 3: Create and Push Tag

```bash
# Create the tag
git tag vX.Y.Z

# Push both the commit and tag
git push origin main
git push origin vX.Y.Z
```

### Step 4: Monitor the Release

1. Go to https://github.com/Arithmomaniac/copilot-repository-tools/actions
2. Watch the "Release to PyPI" workflow
3. If configured with required reviewers, approve the deployment when prompted
4. Once complete, verify at https://pypi.org/project/copilot-repository-tools/

## Release Workflow Details

The `.github/workflows/release.yml` workflow runs three jobs:

1. **validate-version**: Ensures tag version matches `pyproject.toml` version
2. **lint-and-test**: Runs ruff, ty, and pytest to verify quality
3. **build-and-publish**: Builds the package and publishes to PyPI

The workflow uses:
- **uv** for dependency management and building
- **PyPI trusted publishing** (OIDC) for secure authentication without API tokens
- **GitHub environment** (`pypi`) for optional manual approval

## Installation Instructions for Users

After release, users can install the package with:

```bash
# Install base package (CLI only)
pip install copilot-repository-tools

# Install with web interface
pip install copilot-repository-tools[web]

# Install everything
pip install copilot-repository-tools[all]

# Use with pipx (recommended for CLI tools)
pipx install copilot-repository-tools
pipx install copilot-repository-tools[web]
```

## Troubleshooting

### Tag version doesn't match pyproject.toml

If the workflow fails with a version mismatch:

1. Check `pyproject.toml` and ensure the version matches your tag
2. Delete the tag: `git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`
3. Fix the version and create a new tag

### PyPI trusted publishing not working

1. Verify the pending publisher configuration at https://pypi.org/manage/account/publishing/
2. Ensure all fields match exactly (Owner, Repository, Workflow, Environment)
3. Check that 2FA is enabled on your PyPI account
4. Review the workflow logs for detailed error messages

### Build fails

1. Run the checks locally before tagging:
   ```bash
   uv sync --all-extras
   uv run ruff check src/ tests/
   uv run ruff format --check src/ tests/
   uv run ty check
   uv run pytest tests/ --ignore=tests/test_webapp_e2e.py
   ```
2. Fix any issues and commit
3. Create a new version tag

## Version Numbering

We follow [Semantic Versioning](https://semver.org/):

- **MAJOR** (X.0.0): Incompatible API changes
- **MINOR** (0.X.0): New functionality, backward compatible
- **PATCH** (0.0.X): Bug fixes, backward compatible

For pre-1.0.0 versions, minor version bumps may include breaking changes.
