# GitHub Tracker — Development Context

## Quick Navigation

| Document | When to Read |
|----------|-------------|
| This file | Every session (auto-loaded) |
| `system/ARCHITECTURE.md` | System design, data flow, refresh lifecycle |
| `system/FEATURE-MATRIX.md` | What's implemented vs planned |
| `system/PR-CHECKLIST.md` | Before opening a PR |
| `sops/release.md` | Publishing a new version |
| `tasks/TASK-XX.md` | Active task details |

## Current State

**Version:** v0.3.1
**Tech Stack:** Python 3.11+ / Textual 0.47+ / httpx 0.27+ / pytest (100% coverage)
**Distribution:** Homebrew via `Kintull/homebrew-tap`

### Key Components

| Component | Status | Notes |
|-----------|--------|-------|
| TUI App (`app.py`) | Working | Textual App with two PRTable widgets |
| GitHub Client (`github_client.py`) | Working | REST + GraphQL via httpx |
| PR Service (`pr_service.py`) | Working | Label computation, grouping, deploy status |
| State (`state.py`) | Working | JSON file, version 4 schema |
| Config (`config.py`) | Working | YAML, `~/.github-tracker-config.yaml` |
| Jira Integration (`jira.py`) | Working | Ticket extraction from branch/title |
| Setup Wizard (`setup_wizard.py`) | Working | First-run interactive config |
| Widgets | Working | PRTable, TrackerHeader, StatusBar |

## Project Structure

```
src/github_tracker/
  __main__.py          # CLI entry point (argparse)
  app.py               # GitHubTrackerApp (Textual)
  github_client.py     # GitHubClient (httpx async)
  pr_service.py        # Business logic (labels, grouping, deploy)
  models.py            # PullRequest, CIStatus, DeployStatus, PRLabel
  config.py            # Config dataclass + YAML loader
  state.py             # State persistence (JSON, version 4)
  jira.py              # Jira ticket extraction + URL building
  theme.py             # Color constants
  logging_config.py    # Log setup
  setup_wizard.py      # Interactive first-run wizard
  widgets/
    pr_table.py        # PRTable (DataTable subclass)
    header.py          # TrackerHeader
    status_bar.py      # StatusBar
tests/                 # pytest, 100% coverage required
```

## Active Work

_See GitHub Issues with `pilot` label for current queue._
