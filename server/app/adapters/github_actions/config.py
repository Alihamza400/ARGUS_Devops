from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class GitHubActionsConfig:
    repo_owner: str
    repo_name: str
    token: str = ""
    repo_id: str = ""
    workflow_name: str | None = None
    branch: str | None = None
    status: str | None = None
    max_runs: int = 50
    per_page: int = 100
    api_url: str = "https://api.github.com"
    request_timeout: float = 30.0
    max_retries: int = 3
    retry_backoff: float = 1.0

    pipeline_run_id_prefix: str = "gh-run-"

    def __post_init__(self):
        if not self.repo_id:
            self.repo_id = f"repo-gh-{uuid.uuid4().hex[:8]}"

    @property
    def repo_full_name(self) -> str:
        return f"{self.repo_owner}/{self.repo_name}"

    ALLOWED_STATUSES: tuple[str, ...] = (
        "queued", "in_progress", "completed", "waiting", "requested", "pending"
    )

    @classmethod
    def from_env(cls) -> GitHubActionsConfig:
        import os

        return cls(
            repo_owner=os.getenv("ARGUS_GITHUB_OWNER", ""),
            repo_name=os.getenv("ARGUS_GITHUB_REPO", ""),
            token=os.getenv("ARGUS_GITHUB_TOKEN", ""),
            workflow_name=os.getenv("ARGUS_GITHUB_WORKFLOW"),
            branch=os.getenv("ARGUS_GITHUB_BRANCH"),
            max_runs=int(os.getenv("ARGUS_GITHUB_MAX_RUNS", "50")),
        )
