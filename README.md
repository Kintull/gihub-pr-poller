# GitHub Tracker

A terminal UI for tracking pull requests across GitHub repositories.

## 💬 Comment Thread Display

The `💬` column shows the state of review threads for each PR.

| PR type | Display | Meaning |
|---|---|---|
| My PR — unresolved threads | `3` (yellow) | 3 reviewer threads still open |
| My PR — all threads resolved | `✓` (green) | all reviewer feedback addressed |
| My PR — no threads | `—` | no review comments |
| Others' PR — I have unresolved threads | `3` (yellow) | author hasn't addressed my 3 threads yet |
| Others' PR — all my threads resolved | `✓` (green) | author addressed all my threads |
| Others' PR — I left no comments | `—` | nothing to track |

**My PRs** use all threads on the PR.
**Others' PRs** show only threads where you left at least one comment.

## Releasing a New Version

### Version Numbering

| Change type | Bump | Example |
|---|---|---|
| State file version bump, breaking config changes, API contract changes | **Minor** (0.X.0) | State v3→v4, renamed config keys |
| New features, new API methods, non-breaking additions | **Patch** (0.0.X) | Added deployment tracking |
| Bug fixes, test-only changes, docs | **Patch** (0.0.X) | Fixed crash on toggle |

### Steps

1. **Bump version** in `pyproject.toml`
2. **Commit and push** the version bump to `main`
3. **Create a git tag** matching the version (e.g. `v0.3.0`) and push it
4. **Create a GitHub release** from the tag with changelog notes
5. **Get the SHA256** of the release tarball from GitHub
6. **Update the Homebrew formula** in `Kintull/homebrew-tap` — change the `url` version and `sha256` hash in `Formula/github-tracker.rb`

The formula lives at `Kintull/homebrew-tap`. Dependencies only need updating if `pyproject.toml` dependencies changed.

## Key Bindings

| Key | Action |
|---|---|
| `↑` / `↓` | Navigate PRs |
| `Tab` | Switch between My PRs / Others |
| `Enter` | Open PR in browser |
| `j` | Open Jira ticket in browser |
| `r` | Refresh focused table |
| `f` | Toggle Favourite (moves PR to My PRs) |
| `q` | Quit |
| `?` | Help |
