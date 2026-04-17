---
name: publish-brew
description: Publish a new Homebrew version of github-tracker. Use whenever the user says "publish brew", "release new version", "bump version and publish", "new brew version", "publish homebrew", "release to homebrew", "cut a release", "bump and release", or any variation about publishing, releasing, or bumping the version of the app. Also trigger when the user asks to "update the tap" or "update the formula".
---

# Publish a new Homebrew release of github-tracker

This skill handles the full release pipeline: determine version bump, update pyproject.toml, tag, create GitHub release, build a standalone binary with PyInstaller, and push an updated Homebrew formula to the tap.

## Repository context

| Item | Value |
|---|---|
| Main repo | `Kintull/gihub-pr-poller` (the typo is intentional) |
| Homebrew tap | `Kintull/homebrew-tap` |
| Formula path | `Formula/github-tracker.rb` |
| Version source | `pyproject.toml` → `version = "X.Y.Z"` |
| Distribution | Pre-built binary (PyInstaller `--onefile`) |

## Step 0 — Determine version bump type

The version scheme is `MAJOR.MINOR.PATCH`. Check what changed since the last tag to decide which component to bump.

### Minor bump (X.Y.0) — triggers

These represent changes that affect persisted data or user-facing contracts:

- **State file version change**: `CURRENT_VERSION` in `src/github_tracker/state.py` was modified
- **Breaking config changes**: fields removed or renamed in `src/github_tracker/config.py`
- **API contract changes**: serialisation format, CLI flags, or external integration interfaces changed in a backwards-incompatible way

### Patch bump (X.Y.Z) — everything else

- New features, new API methods, non-breaking additions
- Bug fixes
- Test-only changes, documentation, refactors

### How to detect automatically

```bash
LAST_TAG=$(git describe --tags --abbrev=0)

# Check for CURRENT_VERSION change in state.py → minor
git diff "$LAST_TAG"..HEAD -- src/github_tracker/state.py | grep -q 'CURRENT_VERSION'

# Check for removed/renamed fields in config.py → minor
git diff "$LAST_TAG"..HEAD -- src/github_tracker/config.py
```

If either check is positive, propose a minor bump. Otherwise propose a patch bump. Always show the proposed version to the user and wait for confirmation before proceeding.

## Step 1 — Bump version in pyproject.toml

Edit the `version = "X.Y.Z"` line in `pyproject.toml` to the new version.

## Step 2 — Commit, push, tag

```bash
git add pyproject.toml
git commit -m "Bump version to X.Y.Z"
git push
git tag vX.Y.Z
git push origin vX.Y.Z
```

## Step 3 — Create GitHub release

Generate release notes from commits since the last tag:

```bash
git log "$LAST_TAG"..HEAD --oneline --no-decorate
```

Then create the release:

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes "CHANGELOG_HERE"
```

Write a concise changelog in the release notes body. Group changes by type (features, fixes, breaking changes). Use a `## What's Changed` header. If there are breaking config changes, include a migration snippet showing old vs new YAML.

## Step 4 — Build standalone binary

The formula ships a pre-built macOS ARM64 binary. No Python or pip needed at install time.

### 4a — Ensure package metadata is up to date

```bash
.venv/bin/pip install -e .
```

This is needed because PyInstaller bundles `importlib.metadata` info used by `--version`.

### 4b — Build with PyInstaller

```bash
.venv/bin/pyinstaller \
  --onefile \
  --name github-tracker \
  --copy-metadata github-tracker \
  --collect-all textual \
  --hidden-import github_tracker \
  src/github_tracker/__main__.py
```

### 4c — Verify the binary

```bash
./dist/github-tracker --version
# Should print: github-tracker X.Y.Z
```

### 4d — Package and upload

```bash
cd dist && tar czf github-tracker-macos-arm64.tar.gz github-tracker && cd ..
```

Upload to the release:

```bash
gh release upload vX.Y.Z /absolute/path/to/dist/github-tracker-macos-arm64.tar.gz --clobber
```

**Note:** `gh release upload` may fail with relative paths that contain globs. Always use absolute paths.

### 4e — Get the binary tarball SHA256

```bash
shasum -a 256 dist/github-tracker-macos-arm64.tar.gz
```

Save the hash — you need it for the formula.

## Step 5 — Update the Homebrew formula

The formula is a simple binary install — no virtualenv, no pip resources.

1. Get the current file SHA (needed for the GitHub API update):

```bash
gh api repos/Kintull/homebrew-tap/contents/Formula/github-tracker.rb --jq '.sha'
```

2. Write the updated formula to a temp file. The formula template is:

```ruby
class GithubTracker < Formula
  desc "A TUI application for tracking GitHub PRs"
  homepage "https://github.com/Kintull/gihub-pr-poller"
  url "https://github.com/Kintull/gihub-pr-poller/releases/download/vX.Y.Z/github-tracker-macos-arm64.tar.gz"
  sha256 "NEW_SHA256_HERE"
  version "X.Y.Z"
  license "MIT"

  def install
    bin.install "github-tracker"
  end

  test do
    assert_match "github-tracker #{version}", shell_output("#{bin}/github-tracker --version")
  end
end
```

3. Push the updated formula via the GitHub API:

```bash
gh api repos/Kintull/homebrew-tap/contents/Formula/github-tracker.rb \
  -X PUT \
  -f message="Update github-tracker to vX.Y.Z" \
  -f content="$(base64 -i /tmp/github-tracker.rb)" \
  -f sha="CURRENT_FILE_SHA"
```

## Step 6 — Confirm

Print a summary:

- Version: X.Y.Z
- Tag: vX.Y.Z
- Release URL (from `gh release create` output)
- Formula commit URL (from the API response `.commit.html_url`)
- Upgrade command: `brew upgrade github-tracker`
