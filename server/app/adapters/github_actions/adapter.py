from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.adapters.base import BaseAdapter
from app.adapters.github_actions.client import (
    GitHubClient,
    GitHubClientError,
    GitHubRateLimitError,
)
from app.adapters.github_actions.config import GitHubActionsConfig
from app.adapters.github_actions.models import GitHubWorkflowRun
from app.graph.connection import Neo4jConnection
from app.graph.schema import EdgeType, NodeType

logger = logging.getLogger("argus.adapters.github")


class GitHubActionsAdapter(BaseAdapter):
    agent_type = "github_actions"
    agent_version = "1.0.0"

    def __init__(self, config: GitHubActionsConfig):
        self.config = config
        self._client: GitHubClient | None = None

    async def _get_client(self) -> GitHubClient:
        if self._client is None:
            self._client = GitHubClient(self.config)
        return self._client

    async def sync(self) -> dict:
        start = datetime.now(timezone.utc)
        result: dict[str, Any] = {
            "repo": self.config.repo_full_name,
            "runs_fetched": 0,
            "pipeline_runs_created": 0,
            "commit_links_created": 0,
            "deployment_links_created": 0,
            "errors": [],
            "sync_duration_ms": 0,
        }

        try:
            client = await self._get_client()
        except ImportError as e:
            result["errors"].append(f"httpx not installed: {e}")
            return result

        try:
            runs = await self._fetch_runs(client)
            result["runs_fetched"] = len(runs)
        except GitHubRateLimitError as e:
            result["errors"].append(f"GitHub API rate limited (reset at {e.reset_at})")
            return result
        except GitHubClientError as e:
            result["errors"].append(f"GitHub API error: {e}")
            return result
        except Exception as e:
            result["errors"].append(f"Unexpected error fetching runs: {e}")
            return result

        for run in runs:
            try:
                pr_created = await self._sync_pipeline_run(run)
                if pr_created:
                    result["pipeline_runs_created"] += 1

                commit_linked = await self._link_run_to_commit(run)
                if commit_linked:
                    result["commit_links_created"] += 1

                dep_linked = await self._link_run_to_deployment(run)
                if dep_linked:
                    result["deployment_links_created"] += 1

            except Exception as e:
                result["errors"].append(
                    f"Error syncing run #{run.run_number} ({run.name}): {e}"
                )

        result["sync_duration_ms"] = (
            datetime.now(timezone.utc) - start
        ).total_seconds() * 1000

        return result

    async def _fetch_runs(self, client: GitHubClient) -> list[GitHubWorkflowRun]:
        target_workflow_id = None
        if self.config.workflow_name:
            workflow = await client.get_workflow_by_name(self.config.workflow_name)
            if workflow:
                target_workflow_id = workflow.id
            else:
                logger.warning(
                    "Workflow '%s' not found, fetching all runs",
                    self.config.workflow_name,
                )

        status = self.config.status
        if status and status not in GitHubActionsConfig.ALLOWED_STATUSES:
            logger.warning(
                "Invalid status filter '%s', ignoring. Allowed: %s",
                status,
                ", ".join(GitHubActionsConfig.ALLOWED_STATUSES),
            )
            status = None

        return await client.list_workflow_runs(
            workflow_id=target_workflow_id,
            branch=self.config.branch,
            status=status,
            per_page=self.config.per_page,
            max_pages=max(1, self.config.max_runs // self.config.per_page + 1),
        )

    async def _sync_pipeline_run(self, run: GitHubWorkflowRun) -> bool:
        run_id = f"{self.config.pipeline_run_id_prefix}{run.id}"
        status = run.conclusion if run.is_completed else run.status

        started_at = run.run_started_at.isoformat() if run.run_started_at else None
        duration = run.duration_seconds

        properties: dict[str, Any] = {
            "workflow": run.name,
            "status": status,
            "trigger": run.event,
        }
        if started_at:
            properties["started_at"] = started_at
        if duration:
            properties["duration_seconds"] = duration

        return await self._upsert_node(
            NodeType.PIPELINE_RUN,
            run_id,
            properties,
        )

    async def _link_run_to_commit(self, run: GitHubWorkflowRun) -> bool:
        sha = run.head_sha
        if not sha:
            return False

        commit_exists = await Neo4jConnection.run_query(
            "MATCH (c:Commit {sha: $sha}) RETURN c LIMIT 1",
            {"sha": sha},
        )
        if not commit_exists:
            return False

        run_id = f"{self.config.pipeline_run_id_prefix}{run.id}"
        started_at = run.run_started_at.isoformat() if run.run_started_at else None
        props = {}
        if started_at:
            props["started_at"] = started_at

        return await self._upsert_edge(
            EdgeType.TRIGGERED,
            commit_exists[0]["c"]["id"],
            run_id,
            props if props else None,
        )

    async def _link_run_to_deployment(self, run: GitHubWorkflowRun) -> bool:
        if not run.is_success:
            return False

        run_id = f"{self.config.pipeline_run_id_prefix}{run.id}"

        deployment = await Neo4jConnection.run_query(
            """
            MATCH (dep:Deployment {namespace: $ns})
            RETURN dep LIMIT 1
            """,
            {"ns": run.head_branch or "default"},
        )
        if not deployment:
            return False

        props = {
            "at": (run.run_started_at.isoformat() if run.run_started_at
                   else datetime.now(timezone.utc).isoformat()),
        }
        return await self._upsert_edge(
            EdgeType.PRODUCES,
            run_id,
            deployment[0]["dep"]["id"],
            props,
        )

    async def close(self):
        if self._client:
            await self._client.close()
            self._client = None
