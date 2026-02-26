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
