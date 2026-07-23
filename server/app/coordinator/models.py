from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ConflictType(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    CASCADING = "cascading"
    COMPLEMENTARY = "complementary"


class ConflictSeverity(str, Enum):
    BLOCKING = "blocking"
    MAJOR = "major"
    MINOR = "minor"
    NONE = "none"


class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    BLOCKED = "blocked"
    MERGED = "merged"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ResolutionStrategy(str, Enum):
    AUTO_APPROVE = "auto_approve"
    AUTO_BLOCK = "auto_block"
    RANK_AND_PICK = "rank_and_pick"
    FLAG_FOR_REVIEW = "flag_for_review"
    MERGE_IF_COMPATIBLE = "merge_if_compatible"


class ResourceType(str, Enum):
    POD = "Pod"
    SERVICE = "Service"
    DEPLOYMENT = "Deployment"
    NAMESPACE = "Namespace"
    COMMIT = "Commit"
    REPOSITORY = "Repository"
    PIPELINE_RUN = "PipelineRun"
    CLUSTER = "Cluster"


class ProposalRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"prop-{uuid.uuid4().hex[:12]}")
    agent: str = ""
    agent_version: str = ""
    title: str = ""
    description: str = ""
    action: str = ""
    target_id: str = ""
    target_type: str = ""
    resource_type: ResourceType = ResourceType.SERVICE
    rationale: str = ""
    evidence_count: int = 0
    evidence_summary: str = ""
    confidence: float = 0.0
    risk_level: str = "medium"
    severity: str = "medium"
    pr_body: str = ""
    status: ProposalStatus = ProposalStatus.PENDING
    resolution: str = ""
    resolved_by: str = ""
    resolver_notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_dump_for_cypher(self) -> dict[str, Any]:
        data = self.model_dump()
        for key in ("created_at", "updated_at", "expires_at"):
            if data.get(key):
                val = data[key]
                data[key] = val.isoformat() if hasattr(val, "isoformat") else val
        for key in ("tags",):
            if key in data and isinstance(data[key], list):
                data[key] = json.dumps(data[key])
        for key in ("metadata",):
            if key in data and isinstance(data[key], dict):
                data[key] = json.dumps(data[key])
        return data


class ConflictRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"conf-{uuid.uuid4().hex[:12]}")
    proposal_a_id: str = ""
    proposal_b_id: str = ""
    conflict_type: ConflictType = ConflictType.INDIRECT
    severity: ConflictSeverity = ConflictSeverity.MINOR
    description: str = ""
    affected_resource: str = ""
    resource_type: ResourceType = ResourceType.SERVICE
    score_a: float = 0.0
    score_b: float = 0.0
    resolution_strategy: ResolutionStrategy = ResolutionStrategy.FLAG_FOR_REVIEW
    recommendation: str = ""
    resolved: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_dump_for_cypher(self) -> dict[str, Any]:
        data = self.model_dump()
        if data.get("created_at"):
            val = data["created_at"]
            data["created_at"] = val.isoformat() if hasattr(val, "isoformat") else val
        return data


class ResourceLock(BaseModel):
    id: str = Field(default_factory=lambda: f"lock-{uuid.uuid4().hex[:12]}")
    resource_id: str = ""
    resource_type: ResourceType = ResourceType.SERVICE
    proposal_id: str = ""
    agent: str = ""
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    released: bool = False


class SubmitProposalRequest(BaseModel):
    agent: str = ""
    agent_version: str = "0.1.0"
    title: str = ""
    description: str = ""
    action: str = ""
    target_id: str = ""
    target_type: str = ""
    resource_type: ResourceType = ResourceType.SERVICE
    rationale: str = ""
    evidence_count: int = 0
    evidence_summary: str = ""
    confidence: float = 0.0
    risk_level: str = "medium"
    severity: str = "medium"
    pr_body: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubmitProposalResponse(BaseModel):
    proposal: ProposalRecord
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    status: ProposalStatus = ProposalStatus.PENDING
    message: str = ""
    resolution: str = ""


class ConflictQuery(BaseModel):
    resource_id: str | None = None
    resource_type: ResourceType | None = None
    status: ProposalStatus | None = None
    agent: str | None = None
    include_resolved: bool = False
    limit: int = 50
    offset: int = 0


class ConflictResolutionRequest(BaseModel):
    conflict_id: str = ""
    resolution_strategy: ResolutionStrategy = ResolutionStrategy.FLAG_FOR_REVIEW
    notes: str = ""
    resolved_by: str = ""
    override: bool = False
