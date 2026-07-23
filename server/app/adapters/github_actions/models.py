from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class GitHubWorkflowRun:
    id: int
    name: str
    head_branch: str
    head_sha: str
    status: str
    conclusion: str | None
    html_url: str
    run_number: int
    event: str
    display_title: str
    created_at: datetime
    updated_at: datetime
    run_started_at: datetime | None = None
    actor: dict[str, Any] | None = None
    triggering_actor: dict[str, Any] | None = None
    run_attempt: int = 1

    @classmethod
    def from_api(cls, data: dict) -> GitHubWorkflowRun:
        return cls(
            id=data["id"],
            name=data.get("name", data.get("display_title", "unknown")),
            head_branch=data.get("head_branch", ""),
            head_sha=data.get("head_sha", ""),
            status=data.get("status", "unknown"),
            conclusion=data.get("conclusion"),
            html_url=data.get("html_url", ""),
            run_number=data.get("run_number", 0),
            event=data.get("event", "unknown"),
            display_title=data.get("display_title", ""),
            created_at=_parse_dt(data.get("created_at")),
            updated_at=_parse_dt(data.get("updated_at")),
            run_started_at=_parse_dt(data.get("run_started_at")),
            actor=data.get("actor"),
            triggering_actor=data.get("triggering_actor"),
            run_attempt=data.get("run_attempt", 1),
        )

    @property
    def duration_seconds(self) -> int:
        if self.run_started_at and self.updated_at:
            return int((self.updated_at - self.run_started_at).total_seconds())
        return 0

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    @property
    def is_success(self) -> bool:
        return self.conclusion == "success"

    @property
    def is_failure(self) -> bool:
        return self.conclusion in ("failure", "cancelled", "timed_out")


@dataclass
class GitHubWorkflow:
    id: int
    name: str
    path: str
    state: str
    html_url: str

    @classmethod
    def from_api(cls, data: dict) -> GitHubWorkflow:
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            path=data.get("path", ""),
            state=data.get("state", ""),
            html_url=data.get("html_url", ""),
        )


@dataclass
class PaginatedResponse:
    items: list[Any]
    next_page: int | None = None
    total_count: int | None = None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt
    except (ValueError, TypeError):
        return None
