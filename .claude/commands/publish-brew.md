---
name: publish-brew
description: Publish a new Homebrew version of github-tracker. Use whenever the user says "publish brew", "release new version", "bump version and publish", "new brew version", "publish homebrew", "release to homebrew", "cut a release", "bump and release", or any variation about publishing, releasing, or bumping the version of the app. Also trigger when the user asks to "update the tap" or "update the formula".
---

# Publish a new Homebrew release of github-tracker

This skill handles the full release pipeline: determine version bump, update pyproject.toml, tag, create GitHub release, compute tarball SHA, and push an updated Homebrew formula to the tap.

## Repository context

| Item | Value |
|---|---|
| Main repo | `Kintull/gihub-pr-poller` (the typo is intentional) |
| Homebrew tap | `Kintull/homebrew-tap` |
| Formula path | `Formula/github-tracker.rb` |
| Version source | `pyproject.toml` → `version = "X.Y.Z"` |

## Step 0 — Determine version bump type

The version scheme is `0.MINOR.PATCH`. Check what changed since the last tag to decide which component to bump.

### Minor bump (0.X.0) — triggers

These represent changes that affect persisted data or user-facing contracts:

- **State file version change**: `CURRENT_VERSION` in `src/github_tracker/state.py` was modified
- **Breaking config changes**: fields removed or renamed in `src/github_tracker/config.py`
- **API contract changes**: serialisation format, CLI flags, or external integration interfaces changed in a backwards-incompatible way

### Patch bump (0.0.X) — everything else

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

## Step 4 — Get tarball SHA256

```bash
curl -sL "https://github.com/Kintull/gihub-pr-poller/archive/refs/tags/vX.Y.Z.tar.gz" | shasum -a 256
```

Save the hash — you need it for the formula.

## Step 5 — Check for dependency changes

Compare the current virtualenv against the existing formula to see if any Python dependency versions changed:

```bash
# Current versions
.venv/bin/pip freeze | grep -iE 'textual|httpx|pyyaml|rich|httpcore|h11|anyio|certifi|idna|markdown|mdit|mdurl|platformdirs|pygments|linkify|uc-micro|typing.ext' | sort

# Existing formula versions
gh api repos/Kintull/homebrew-tap/contents/Formula/github-tracker.rb --jq '.content' | base64 -d
```

If versions match, only the `url` and `sha256` lines need updating. If any dependency version changed, regenerate the corresponding `resource` blocks with new URLs and SHA256 hashes. PyPI source tarball URLs follow this pattern:

```
https://files.pythonhosted.org/packages/source/{first_letter}/{name}/{name}-{version}.tar.gz
```

To get the SHA256 for a PyPI package:

```bash
curl -sL "https://files.pythonhosted.org/packages/source/t/textual/textual-X.Y.Z.tar.gz" | shasum -a 256
```

## Step 6 — Update the Homebrew formula

1. Get the current file SHA (needed for the GitHub API update):

```bash
gh api repos/Kintull/homebrew-tap/contents/Formula/github-tracker.rb --jq '.sha'
```

2. Read the current formula, update the `url` and `sha256` lines (and any changed resource blocks), and write to a temp file:

```bash
# The url line should become:
url "https://github.com/Kintull/gihub-pr-poller/archive/refs/tags/vX.Y.Z.tar.gz"
sha256 "NEW_SHA256_HERE"
```

3. Push the updated formula via the GitHub API:

```bash
gh api repos/Kintull/homebrew-tap/contents/Formula/github-tracker.rb \
  -X PUT \
  -f message="Update github-tracker to vX.Y.Z" \
  -f content="$(base64 -i /tmp/github-tracker.rb)" \
  -f sha="CURRENT_FILE_SHA"
```

## Step 7 — Confirm

Print a summary:

- Version: X.Y.Z
- Tag: vX.Y.Z
- Release URL (from `gh release create` output)
- Formula commit URL (from the API response `.commit.html_url`)
- Upgrade command: `brew upgrade github-tracker`
