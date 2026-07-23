from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class ReviewDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    ABSTAINED = "abstained"

class ReviewerRole(str, Enum):
    SENIOR = "senior"
    TEAM_LEAD = "team_lead"
    PEER = "peer"
    AUTOMATED = "automated"

class ReviewRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"rev-{uuid.uuid4().hex[:12]}")
    proposal_id: str = ""
    reviewer: str = ""
    reviewer_role: ReviewerRole = ReviewerRole.PEER
    decision: ReviewDecision = ReviewDecision.ABSTAINED
    comment: str = ""
    evidence_checked: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    def model_dump_for_cypher(self) -> dict[str, Any]:
        data = self.model_dump()
        for k in ("created_at", "updated_at"):
            if data.get(k):
                v = data[k]; data[k] = v.isoformat() if hasattr(v, "isoformat") else v
        return data

class ApprovalPolicyConfig(BaseModel):
    rules: dict[str, Any] = Field(default_factory=lambda: {"min_reviewers": 1, "no_self_approval": True, "senior_for_critical": True, "min_evidence_count": 1, "min_confidence": 0.3, "max_pending_days": 7, "enforce_two_factor": False})
    description: str = "Default"
    @classmethod
    def defaults(cls) -> ApprovalPolicyConfig:
        return cls()

class ReviewSubmission(BaseModel):
    proposal_id: str = ""
    reviewer: str = ""
    reviewer_role: ReviewerRole = ReviewerRole.PEER
    decision: ReviewDecision = ReviewDecision.APPROVED
    comment: str = ""
    evidence_checked: bool = True

class ReviewResponse(BaseModel):
    review: ReviewRecord
    proposal: dict[str, Any] | None = None
    policy_violations: list[str] = Field(default_factory=list)
    proposal_status: str = ""
    message: str = ""

class PolicyCheckResult(BaseModel):
    passed: bool = True
    violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
