# Feature Matrix

| Feature | Status | Module |
|---------|--------|--------|
| Multi-repo PR tracking | Working | `github_client.py`, `app.py` |
| CI status (GitHub Check Runs) | Working | `github_client.fetch_check_runs()`, `models.CIStatus` |
| Deploy status (Deployments API) | Working | `github_client.fetch_latest_deployment_sha()`, `pr_service.compute_deploy_status()` |
| Comment thread tracking (GraphQL) | Working | `github_client.fetch_review_threads()`, `pr_service.compute_thread_counts()` |
| Jira integration | Working | `jira.py` — extract ticket from branch/title, open in browser |
| Favourite / My PRs grouping | Working | `PRLabel.FAVOURITE`, `pr_service.group_prs()` |
| Auto-follow (AUTHOR, REVIEW_REQUESTED, COMMENTED) | Working | `pr_service.compute_phase1_labels()`, `compute_phase2_labels()` |
| Auto-refresh (My PRs 60s, All 300s) | Working | `app._auto_refresh_my_prs()`, `app._auto_refresh()` |
| Manual focused refresh | Working | `app._refresh_focused_prs()` |
| Merge detection + deploy tracking | Working | `app._load_prs_progressive()` merge detection block |
| State persistence (instant cached load) | Working | `state.py` version 4 |
| Setup wizard (first-run config) | Working | `setup_wizard.py` |
| Update check (latest version hint) | Working | `app._check_for_updates()` |
| Homebrew distribution | Working | `Kintull/homebrew-tap` formula |
| Key bindings (navigate, open, favourite, help) | Working | `app.py` BINDINGS |
