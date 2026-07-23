from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from app.adapters.github_actions.config import GitHubActionsConfig
from app.adapters.github_actions.models import (
    GitHubWorkflow,
    GitHubWorkflowRun,
    PaginatedResponse,
)

logger = logging.getLogger("argus.adapters.github")


class GitHubClientError(Exception):
    def __init__(self, message: str, status: int = 0, response: Any = None):
        super().__init__(message)
        self.status = status
        self.response = response


class GitHubRateLimitError(GitHubClientError):
    def __init__(self, reset_at: int | None = None):
        super().__init__("GitHub API rate limit exceeded", status=429)
        self.reset_at = reset_at


class GitHubClient:
    BASE_URL = "https://api.github.com"
    ACCEPT_HEADER = "application/vnd.github.v3+json"

    def __init__(self, config: GitHubActionsConfig):
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._pages_consumed = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Accept": self.ACCEPT_HEADER,
                "User-Agent": f"Argus/{self.config.repo_owner}-{self.config.repo_name}",
            }
            if self.config.token:
                headers["Authorization"] = f"Bearer {self.config.token}"
            self._client = httpx.AsyncClient(
                base_url=self.config.api_url,
                headers=headers,
                timeout=self.config.request_timeout,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        retries: int = 0,
    ) -> httpx.Response:
        client = await self._get_client()
        url = path

        for attempt in range(retries + 1):
            try:
                response = await client.request(method, url, params=params)
            except httpx.TimeoutException as e:
                if attempt < retries:
                    wait = self.config.retry_backoff * (2 ** attempt)
                    logger.warning("GitHub API timeout, retrying in %.1fs", wait)
                    await asyncio.sleep(wait)
                    continue
                raise GitHubClientError(f"Request timed out after {retries + 1} attempts: {e}")

            except httpx.HTTPError as e:
                if attempt < retries:
                    wait = self.config.retry_backoff * (2 ** attempt)
                    logger.warning("GitHub API error, retrying in %.1fs: %s", wait, e)
                    await asyncio.sleep(wait)
                    continue
                raise GitHubClientError(f"HTTP error after {retries + 1} attempts: {e}")

            self._pages_consumed += 1

            if response.status_code == 429:
                reset = self._parse_rate_limit_reset(response)
                raise GitHubRateLimitError(reset_at=reset)

            if response.status_code == 401:
                raise GitHubClientError(
                    "Authentication failed. Check your GitHub token.",
                    status=401,
                    response=response,
                )
            if response.status_code == 403:
                raise GitHubClientError(
                    "Access forbidden. Check token permissions.",
                    status=403,
                    response=response,
                )
            if response.status_code == 404:
                raise GitHubClientError(
                    f"Resource not found: {path}",
                    status=404,
                    response=response,
                )

            if response.status_code >= 500 and attempt < retries:
                wait = self.config.retry_backoff * (2 ** attempt)
                logger.warning("GitHub server error %d, retrying in %.1fs", response.status_code, wait)
                await asyncio.sleep(wait)
                continue

            response.raise_for_status()
            return response

        raise GitHubClientError(f"Request failed after {retries + 1} attempts")

    def _parse_rate_limit_reset(self, response: httpx.Response) -> int | None:
        val = response.headers.get("X-RateLimit-Reset")
        if val:
            try:
                return int(val)
            except ValueError:
                pass
        return None

    def _get_next_page(self, response: httpx.Response) -> int | None:
        link = response.headers.get("Link", "")
        if not link:
            return None
        match = re.search(r'page=(\d+)>;\s*rel="next"', link)
        if match:
            return int(match.group(1))
        return None

    async def list_workflows(self) -> list[GitHubWorkflow]:
        path = f"/repos/{self.config.repo_full_name}/actions/workflows"
        response = await self._request("GET", path, retries=self.config.max_retries)
        data = response.json()
        return [GitHubWorkflow.from_api(w) for w in data.get("workflows", [])]

    async def get_workflow_by_name(self, name: str) -> GitHubWorkflow | None:
        workflows = await self.list_workflows()
        for w in workflows:
            if w.name == name or w.path.endswith(name):
                return w
        return None

    async def list_workflow_runs(
        self,
        workflow_id: int | None = None,
        actor: str | None = None,
        branch: str | None = None,
        event: str | None = None,
        status: str | None = None,
        per_page: int = 100,
        max_pages: int = 5,
    ) -> list[GitHubWorkflowRun]:
        if workflow_id:
            path = f"/repos/{self.config.repo_full_name}/actions/workflows/{workflow_id}/runs"
        else:
            path = f"/repos/{self.config.repo_full_name}/actions/runs"

        params: dict[str, Any] = {"per_page": min(per_page, 100)}
        if actor:
            params["actor"] = actor
        if branch:
            params["branch"] = branch
        if event:
            params["event"] = event
        if status:
            params["status"] = status

        runs: list[GitHubWorkflowRun] = []
        page = 1

        while page is not None and len(runs) < self.config.max_runs and page <= max_pages:
            params["page"] = page
            response = await self._request("GET", path, params=params, retries=self.config.max_retries)
            data = response.json()

            for item in data.get("workflow_runs", []):
                runs.append(GitHubWorkflowRun.from_api(item))

            page = self._get_next_page(response)

        return runs[: self.config.max_runs]

    async def list_runs_for_commit(
        self, sha: str, per_page: int = 10
    ) -> list[GitHubWorkflowRun]:
        path = f"/repos/{self.config.repo_full_name}/actions/runs"
        params: dict[str, Any] = {
            "head_sha": sha,
            "per_page": min(per_page, 100),
        }
        response = await self._request("GET", path, params=params, retries=self.config.max_retries)
        data = response.json()
        return [GitHubWorkflowRun.from_api(item) for item in data.get("workflow_runs", [])]
