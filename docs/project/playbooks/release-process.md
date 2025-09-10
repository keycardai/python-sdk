# Release Process Playbook

This document explains how the automated release process works in the KeycardAI Python SDK monorepo and what developers need to do to trigger releases.

## Overview

The SDK uses **Commitizen** with **conventional commits** to automatically:
- Generate changelogs from commit messages
- Bump package versions based on commit types
- Create GitHub releases with proper tags
- Publish packages to PyPI

## How It Works

### 1. Commit-Based Automation
- **Conventional commits** determine version bumps:
  - `feat(scope):` → Minor version bump (0.1.0 → 0.2.0)
  - `fix(scope):` → Patch version bump (0.1.0 → 0.1.1)  
  - `feat(scope)!:` or `BREAKING CHANGE:` → Major version bump (0.1.0 → 1.0.0)
- **Scoped commits** appear in package-specific changelogs
- **Squashed commits** ensure clean release history

### 2. Tag-Based Releases
- Tags follow format: `<version>-<package-name>`
- Examples: `1.0.0-keycardai-oauth`, `0.2.0-keycardai-mcp-fastmcp`
- Pushing tags triggers automated PyPI publishing

### 3. Automated Workflows
- **Version bumping**: Creates commits with updated versions and changelogs
- **Release publishing**: Builds and publishes packages to PyPI
- **Change detection**: Identifies which packages need releases

## Developer Workflow

### Step 1: Make Changes with Proper Commits

Use conventional commit format with exact package scopes:

```bash
# Feature additions
git commit -m "feat(keycardai-oauth): add PKCE support for enhanced security"
git commit -m "feat(keycardai-mcp-fastmcp): implement connection pooling"

# Bug fixes  
git commit -m "fix(keycardai-oauth): resolve token refresh race condition"
git commit -m "fix(keycardai-mcp): handle connection timeouts properly"

# Breaking changes
git commit -m "feat(keycardai-oauth)!: redesign client API for better usability"
# or with footer:
git commit -m "feat(keycardai-oauth): redesign client API

BREAKING CHANGE: Client constructor now requires explicit configuration object"
```

**Required Scopes**:
- `keycardai-oauth`: OAuth package changes
- `keycardai-mcp`: Core MCP package changes  
- `keycardai-mcp-fastmcp`: FastMCP integration changes
- `deps`: Dependency updates
- `docs`: Documentation updates

### Step 2: Preview Changes Before Release

Check what will be included in the release:

```bash
# Preview changelog for all packages
just preview-changelog

# Validate commit messages
just validate-commits

# See which packages have unreleased changes
just detect-changes
```

### Step 3: Create Release (Manual Trigger)

Currently, releases are triggered manually by running the bump workflow for each package:

```bash
# For OAuth package
gh workflow run bump-package.yml -f package_name=keycardai-oauth -f package_dir=packages/oauth

# For MCP package  
gh workflow run bump-package.yml -f package_name=keycardai-mcp -f package_dir=packages/mcp

# For FastMCP integration
gh workflow run bump-package.yml -f package_name=keycardai-mcp-fastmcp -f package_dir=packages/mcp-fastmcp
```

This will:
1. Run `cz bump --changelog --yes` in the package directory
2. Create a commit with version bump and updated changelog
3. Create and push a git tag (e.g., `1.0.0-keycardai-oauth`)
4. Automatically trigger the release workflow

### Step 4: Automated Publishing

Once the tag is pushed, the release workflow automatically:
1. Detects the package from the tag name
2. Builds the package using `uv build`
3. Publishes to PyPI using `uv publish`
4. Creates a GitHub release

## Important Notes

### Commit Message Requirements
- **Always use exact package scopes** (not shortened versions)
- **Squash commits** before merging PRs - only final commit appears in changelog
- **Fix commit messages** with `git commit --amend` if needed before pushing

### Version Bumping Logic
- **Patch** (0.1.0 → 0.1.1): `fix:`, `docs:`, `style:`, `refactor:`, `test:`, `chore:`
- **Minor** (0.1.0 → 0.2.0): `feat:`
- **Major** (0.1.0 → 1.0.0): `feat!:`, `fix!:`, or `BREAKING CHANGE:` footer

### Package Independence
- Each package has its own version and changelog
- Packages can be released independently
- Cross-package dependencies are handled via workspace configuration

## Troubleshooting

### Invalid Commit Messages
```bash
# Check commit validation
just validate-commits

# Fix with interactive rebase
git rebase -i HEAD~3  # Edit last 3 commits

# Or amend the last commit
git commit --amend -m "feat(keycardai-oauth): add proper scope"
```

### No Changes Detected
If `just detect-changes` shows no packages, ensure:
- Commits use proper conventional format
- Commits have correct package scopes
- Changes are committed and pushed to main branch

### Release Failed
Check the GitHub Actions logs for:
- Build errors in the package
- PyPI authentication issues
- Version conflicts (version already exists)

## Helper Commands

Available justfile commands for release management:

```bash
# Validation and preview
just validate-commits [base-branch]    # Validate commit messages
just preview-changelog [base-branch]   # Preview changelog changes  
just detect-changes                    # Show packages with unreleased changes

# Package information
just extract-package <tag>             # Extract package info from tag

# Development
just build                            # Build all packages
just test                             # Run tests
just check                            # Lint code
```

## Configuration Files

### Root pyproject.toml
- Workspace configuration
- Shared dependencies
- Global tool settings

### Package pyproject.toml  
- Package-specific metadata
- Commitizen configuration per package
- Package dependencies

### GitHub Workflows
- `.github/workflows/release.yml`: Automated PyPI publishing
- `.github/workflows/bump-package.yml`: Version bumping and tagging

### Scripts
- `scripts/changelog.py`: Custom tooling for monorepo changelog management

## Security Notes

- PyPI publishing uses **trusted publishing** (OIDC) - no API keys stored
- Releases require **environment protection** rules in GitHub
- Only maintainers can trigger release workflows
- All releases are auditable through GitHub Actions logs

## Example Release Flow

1. **Developer makes changes**:
   ```bash
   git commit -m "feat(keycardai-oauth): add refresh token rotation"
   git commit -m "fix(keycardai-mcp): handle connection errors gracefully"  
   git push origin feature-branch
   ```

2. **PR review and merge** (squash commits):
   ```bash
   # Final commit message in main:
   "feat(keycardai-oauth): add refresh token rotation and fix connection handling"
   ```

3. **Preview before release**:
   ```bash
   just preview-changelog
   # Shows: keycardai-oauth will get minor version bump
   ```

4. **Trigger release**:
   ```bash
   gh workflow run bump-package.yml -f package_name=keycardai-oauth -f package_dir=packages/oauth
   ```

5. **Automated result**:
   - Version: `0.1.0` → `0.2.0`
   - Tag: `0.2.0-keycardai-oauth`
   - Changelog updated with new features
   - Package published to PyPI
   - GitHub release created
