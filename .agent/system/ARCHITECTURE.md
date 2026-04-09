# Architecture

## High-Level Design

```
CLI (__main__.py)
  -> Config (YAML: ~/.github-tracker-config.yaml)
  -> GitHubClient (httpx async, REST + GraphQL)
  -> GitHubTrackerApp (Textual)
       -> TrackerHeader
       -> PRTable ("My PRs")    -- FAVOURITE-labelled
       -> PRTable ("Other PRs") -- everything else
       -> StatusBar
```

The app is a single-process Textual TUI. All GitHub API calls are async via httpx. State is persisted to `~/.github-tracker-state.json` for instant display on next launch.

## Data Flow

### PR Loading (Two-Phase Progressive)

**Phase 1 — Fast list** (one API call per repo):
1. `fetch_open_prs(repo)` -> raw PR dicts
2. `parse_pr_basic()` -> `PullRequest` with defaults (CI=PENDING, approvals=0)
3. `compute_phase1_labels()` -> AUTHOR, REVIEW_REQUESTED, MENTIONED
4. Auto-FAVOURITE logic: preserve existing favourites, auto-follow new PRs with interest
5. Display immediately in tables

**Phase 2 — Detail backfill** (per-PR, concurrent):
1. `fetch_reviews()`, `fetch_check_runs()`, `fetch_pr_detail()`, `fetch_review_threads()` (GraphQL)
2. `compute_phase2_labels()` -> adds COMMENTED
3. Auto-follow new PRs where COMMENTED discovered
4. Update each PR in-place in tables
5. Re-group and save state

### Merge Detection

When a previously-known open PR disappears from the open list:
1. `fetch_pr_detail()` to check `merged_at`
2. If merged, add to `_merged_prs` with `ACC_DEPLOYING` status
3. Track `merge_commit_sha` for deployment comparison

### Deployment Status

For each merged PR not yet `ACC_DEPLOYED`:
1. `fetch_latest_deployment_sha(repo, environment)` -> latest successful deployment SHA
2. `compare_commits(merge_commit_sha, deploy_sha)` -> ahead/behind/identical/diverged
3. `compute_deploy_status()`:
   - `ahead` or `identical` -> `ACC_DEPLOYED` (or `ACC_ARGO` if within cooldown)
   - Otherwise -> `ACC_DEPLOYING`
4. Expired deployed PRs removed after `acc_retention_days`

## Refresh Cycle

| Timer | Interval | What it does |
|-------|----------|-------------|
| Full refresh | `refresh_interval` (default 300s) | Re-runs full two-phase progressive load |
| My PRs auto-refresh | 60s | Refreshes details for My PRs table only |
| Manual refresh (`r`) | On demand | Refreshes the currently focused table |
| Spinner tick | 100ms | Animates CI/deploy spinners |
| Label update | 10s | Updates "My PRs — updated Xs ago" staleness |

## State File (`~/.github-tracker-state.json`)

Version 4 schema. Stores:
- `pull_requests[]` — open PRs with labels (for favourite persistence)
- `merged_prs[]` — merged PRs with `merged_at`, `acc_deploy`, `merge_commit_sha`

State enables: instant cached display on launch, favourite tracking across sessions, merge/deploy tracking continuity.

## Config (`~/.github-tracker-config.yaml`)

| Key | Default | Purpose |
|-----|---------|---------|
| `github_repos` | `[]` | List of `owner/repo` to track |
| `github_username` | `""` | Current user (for label computation) |
| `jira_base_url` | `""` | Jira instance URL (empty = disabled) |
| `refresh_interval` | `300` | Full refresh interval in seconds |
| `acc_deploy_environment` | `"acceptance"` | GitHub Deployments environment name |
| `acc_retention_days` | `2` | Days to show deployed PRs |
| `argo_cooldown_minutes` | `20` | Minutes to show ArgoCD sync spinner |

## Key Design Decisions

- **Async everywhere**: All GitHub API calls use httpx AsyncClient. No blocking calls in the Textual event loop.
- **Dataclass models**: `PullRequest` is a frozen-friendly dataclass; updates use `dataclasses.replace()`.
- **Two-phase loading**: Phase 1 gives instant feedback; Phase 2 fills in details without blocking the UI.
- **Label-based grouping**: `PRLabel.FAVOURITE` determines My vs Other. Labels are a frozenset on each PR.
- **100% test coverage**: Enforced via `pytest-cov` with `fail_under = 100`. No exceptions without `pragma: no cover` justification.
- **gh CLI for auth**: Token obtained via `gh auth token` — no token storage in config.
