"""GitHub API client for fetching PR data."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime, timezone

import httpx

from github_tracker.jira import build_jira_url, extract_jira_ticket
from github_tracker.models import CIStatus, PullRequest

logger = logging.getLogger("github_tracker.github_client")

GITHUB_API = "https://api.github.com"


class GitHubAuthError(Exception):
    """Raised when GitHub authentication fails."""


class GitHubAPIError(Exception):
    """Raised when a GitHub API request fails."""


def get_gh_token() -> str:
    """Get GitHub token from gh CLI."""
    logger.debug("Running: gh auth token")
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        token = result.stdout.strip()
        if not token:
            logger.error("gh auth token returned empty result")
            raise GitHubAuthError("gh auth token returned empty result")
        logger.debug("gh auth token succeeded (token length: %d)", len(token))
        return token
    except FileNotFoundError:
        logger.error("gh CLI not found on PATH")
        raise GitHubAuthError("gh CLI not found. Install it from https://cli.github.com/")
    except subprocess.CalledProcessError as e:
        logger.error("gh auth failed: %s", e.stderr.strip())
        raise GitHubAuthError(f"gh auth failed: {e.stderr.strip()}")
    except subprocess.TimeoutExpired:
        logger.error("gh auth token timed out after 10s")
        raise GitHubAuthError("gh auth token timed out")


class GitHubClient:
    """Async client for GitHub REST API."""

    def __init__(self, token: str) -> None:
        logger.info("Initializing GitHubClient (base_url=%s)", GITHUB_API)
        self._client = httpx.AsyncClient(
            base_url=GITHUB_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        logger.debug("Closing HTTP client")
        await self._client.aclose()

    async def _get(self, path: str) -> list | dict:
        """Make a GET request to the GitHub API."""
        logger.debug("GET %s%s", GITHUB_API, path)
        response = await self._client.get(path)
        logger.debug(
            "Response: %d %s (%.0f bytes)",
            response.status_code,
            response.reason_phrase,
            len(response.content),
        )
        if response.status_code == 403 and "rate limit" in response.text.lower():
            logger.error("GitHub API rate limit exceeded")
            raise GitHubAPIError("GitHub API rate limit exceeded")
        if response.status_code != 200:
            logger.error(
                "GitHub API error: %d — %s", response.status_code, response.text[:300]
            )
            raise GitHubAPIError(
                f"GitHub API error: {response.status_code} {response.text[:200]}"
            )
        return response.json()

    async def fetch_open_prs(self, repo: str) -> list[dict]:
        """Fetch open pull requests for a repository."""
        logger.info("Fetching open PRs for %s", repo)
        data = await self._get(f"/repos/{repo}/pulls?state=open&per_page=100")
        if not isinstance(data, list):
            logger.error("Expected list of PRs, got %s", type(data).__name__)
            raise GitHubAPIError(f"Expected list of PRs, got {type(data).__name__}")
        logger.info("Found %d open PRs for %s", len(data), repo)
        return data

    async def fetch_reviews(self, repo: str, pr_number: int) -> list[dict]:
        """Fetch reviews for a pull request."""
        logger.debug("Fetching reviews for %s#%d", repo, pr_number)
        data = await self._get(f"/repos/{repo}/pulls/{pr_number}/reviews")
        if not isinstance(data, list):
            logger.warning("Reviews response not a list for %s#%d", repo, pr_number)
            return []
        logger.debug("Found %d reviews for %s#%d", len(data), repo, pr_number)
        return data

    async def fetch_check_runs(self, repo: str, ref: str) -> list[dict]:
        """Fetch check runs for a commit ref."""
        logger.debug("Fetching check runs for %s @ %s", repo, ref[:8])
        data = await self._get(f"/repos/{repo}/commits/{ref}/check-runs")
        if not isinstance(data, dict):
            logger.warning("Check runs response not a dict for %s @ %s", repo, ref[:8])
            return []
        runs = data.get("check_runs", [])
        logger.debug("Found %d check runs for %s @ %s", len(runs), repo, ref[:8])
        return runs

    async def fetch_workflow_runs(
        self, repo: str, workflow_name: str, branch: str = "master", per_page: int = 5
    ) -> list[dict]:
        """Fetch recent workflow runs for a named workflow."""
        logger.debug("Fetching workflows for %s (looking for %r)", repo, workflow_name)
        workflows_data = await self._get(f"/repos/{repo}/actions/workflows")
        if not isinstance(workflows_data, dict):
            logger.warning("Workflows response not a dict for %s", repo)
            return []

        workflow_id = None
        for wf in workflows_data.get("workflows", []):
            if wf.get("name") == workflow_name:
                workflow_id = wf["id"]
                break

        if workflow_id is None:
            logger.warning("Workflow %r not found in %s", workflow_name, repo)
            return []

        runs_data = await self._get(
            f"/repos/{repo}/actions/workflows/{workflow_id}/runs"
            f"?branch={branch}&per_page={per_page}"
        )
        if not isinstance(runs_data, dict):
            logger.warning("Workflow runs response not a dict for %s", repo)
            return []

        runs = runs_data.get("workflow_runs", [])
        logger.debug("Found %d workflow runs for %s/%r", len(runs), repo, workflow_name)
        return runs

    async def fetch_workflow_run_jobs(self, repo: str, run_id: int) -> list[dict]:
        """Fetch jobs for a specific workflow run."""
        logger.debug("Fetching jobs for %s run %d", repo, run_id)
        data = await self._get(f"/repos/{repo}/actions/runs/{run_id}/jobs")
        if not isinstance(data, dict):
            logger.warning("Jobs response not a dict for %s run %d", repo, run_id)
            return []
        return data.get("jobs", [])

    async def fetch_review_threads(self, repo: str, pr_number: int) -> list[dict]:
        """Fetch review threads for a PR via the GraphQL API."""
        owner, name = repo.split("/", 1)
        query = (
            "query($owner:String!,$name:String!,$number:Int!){"
            "repository(owner:$owner,name:$name){"
            "pullRequest(number:$number){"
            "reviewThreads(first:100){nodes{isResolved "
            "comments(first:100){nodes{author{login}}}}}}}}"
        )
        logger.debug("Fetching review threads for %s#%d", repo, pr_number)
        try:
            response = await self._client.post(
                "/graphql",
                json={"query": query, "variables": {"owner": owner, "name": name, "number": pr_number}},
            )
            if response.status_code != 200:
                logger.warning("GraphQL error for %s#%d: %d", repo, pr_number, response.status_code)
                return []
            data = response.json()
            threads = (
                (data.get("data") or {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
                .get("nodes", [])
            )
            result = threads if isinstance(threads, list) else []
            logger.debug("Found %d review threads for %s#%d", len(result), repo, pr_number)
            return result
        except Exception as e:
            logger.warning("Error fetching review threads for %s#%d: %s", repo, pr_number, e)
            return []

    async def fetch_pr_detail(self, repo: str, pr_number: int) -> dict:
        """Fetch full detail for a single pull request."""
        logger.debug("Fetching PR detail for %s#%d", repo, pr_number)
        data = await self._get(f"/repos/{repo}/pulls/{pr_number}")
        if not isinstance(data, dict):
            logger.warning("PR detail response not a dict for %s#%d", repo, pr_number)
            return {}
        return data

    def parse_pr_basic(self, raw_pr: dict, repo: str, jira_base_url: str) -> PullRequest:
        """Parse a raw GitHub PR dict into a PullRequest with basic info.

        Reviews and CI status are left as defaults (0 approvals, PENDING CI).
        """
        branch_name = raw_pr["head"]["ref"]
        title = raw_pr["title"]

        jira_ticket = extract_jira_ticket(branch_name, title)
        jira_url = (
            build_jira_url(jira_ticket, jira_base_url)
            if jira_ticket and jira_base_url
            else None
        )

        updated_str = raw_pr.get("updated_at", "")
        try:
            updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            logger.warning("Invalid date for PR #%d: %r", raw_pr["number"], updated_str)
            updated_at = datetime.now(tz=timezone.utc)

        return PullRequest(
            number=raw_pr["number"],
            title=title,
            url=raw_pr["html_url"],
            branch_name=branch_name,
            comment_count=0,
            approval_count=0,
            ci_status=CIStatus.PENDING,
            jira_ticket=jira_ticket,
            jira_url=jira_url,
            author=raw_pr["user"]["login"],
            updated_at=updated_at,
            repo=repo,
        )

    async def fetch_pull_requests(
        self, repo: str, jira_base_url: str = ""
    ) -> list[PullRequest]:
        """Fetch all open PRs with reviews and CI status for a repo."""
        logger.info("Fetching full PR data for %s (jira_base_url=%r)", repo, jira_base_url)
        raw_prs = await self.fetch_open_prs(repo)
        pull_requests = []

        for raw_pr in raw_prs:
            pr_number = raw_pr["number"]
            head_sha = raw_pr["head"]["sha"]
            branch_name = raw_pr["head"]["ref"]
            title = raw_pr["title"]

            logger.debug("Processing PR #%d: %s (branch: %s)", pr_number, title, branch_name)

            reviews, check_runs, pr_detail = await asyncio.gather(
                self.fetch_reviews(repo, pr_number),
                self.fetch_check_runs(repo, head_sha),
                self.fetch_pr_detail(repo, pr_number),
            )
            approval_count = count_approvals(reviews)
            ci_status = _aggregate_ci_status(check_runs)

            jira_ticket = extract_jira_ticket(branch_name, title)
            jira_url = (
                build_jira_url(jira_ticket, jira_base_url)
                if jira_ticket and jira_base_url
                else None
            )

            logger.debug(
                "PR #%d: approvals=%d, ci=%s, jira=%s",
                pr_number, approval_count, ci_status.value, jira_ticket,
            )

            updated_str = raw_pr.get("updated_at", "")
            try:
                updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                logger.warning("Invalid date for PR #%d: %r", pr_number, updated_str)
                updated_at = datetime.now(tz=timezone.utc)

            pull_requests.append(
                PullRequest(
                    number=pr_number,
                    title=title,
                    url=raw_pr["html_url"],
                    branch_name=branch_name,
                    comment_count=pr_detail.get("comments", 0)
                    + pr_detail.get("review_comments", 0),
                    approval_count=approval_count,
                    ci_status=ci_status,
                    jira_ticket=jira_ticket,
                    jira_url=jira_url,
                    author=raw_pr["user"]["login"],
                    updated_at=updated_at,
                    repo=repo,
                )
            )

        logger.info("Fetched %d PRs for %s", len(pull_requests), repo)
        return pull_requests


def count_approvals(reviews: list[dict]) -> int:
    """Count unique approvers from a list of review events.

    Matches GitHub's UI: for each reviewer, only their latest significant
    review (APPROVED or CHANGES_REQUESTED) determines their state.
    COMMENTED and PENDING reviews do not change approval status.
    """
    latest: dict[str, str] = {}
    for review in reviews:
        user = (review.get("user") or {}).get("login", "")
        state = review.get("state", "")
        if state in ("APPROVED", "CHANGES_REQUESTED"):
            latest[user] = state
    return sum(1 for state in latest.values() if state == "APPROVED")


def _aggregate_ci_status(check_runs: list[dict]) -> CIStatus:
    """Determine overall CI status from a list of check runs."""
    if not check_runs:
        return CIStatus.UNKNOWN

    statuses = []
    for run in check_runs:
        status = run.get("status", "")
        conclusion = run.get("conclusion")
        statuses.append(CIStatus.from_github(status, conclusion))

    if CIStatus.FAILURE in statuses:
        return CIStatus.FAILURE
    if CIStatus.RUNNING in statuses:
        return CIStatus.RUNNING
    if CIStatus.PENDING in statuses:
        return CIStatus.PENDING
    if all(s == CIStatus.SUCCESS for s in statuses):
        return CIStatus.SUCCESS
    return CIStatus.UNKNOWN
