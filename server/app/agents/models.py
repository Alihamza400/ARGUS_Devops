from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProposalAction(str, Enum):
    ROLLBACK = "rollback"
    SCALE = "scale"
    CONFIG_CHANGE = "config_change"
    MANUAL_INTERVENTION = "manual_intervention"
    PR_APPROVAL = "pr_approval"


class EvidenceCategory(str, Enum):
    POD_STATE = "pod_state"
    SERVICE_CONFIG = "service_config"
    DEPLOYMENT_HISTORY = "deployment_history"
    COMMIT_CHANGE = "commit_change"
    RESOURCE_USAGE = "resource_usage"
    SECURITY_FINDING = "security_finding"
    COST_ANOMALY = "cost_anomaly"
    PIPELINE_FAILURE = "pipeline_failure"


class EvidenceItem(BaseModel):
    category: EvidenceCategory
    label: str
    detail: str
    source_id: str
    source_type: str
    status: str | None = None
    timestamp: datetime | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class TimelineEvent(BaseModel):
    at: datetime
    event_type: str
    summary: str
    detail: str
    source_id: str | None = None


class AnalysisResult(BaseModel):
    summary: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    query_time_ms: float = 0.0
    agent: str = ""
    agent_version: str = ""


class Proposal(BaseModel):
    title: str
    description: str
    action: ProposalAction
    target_id: str
    target_type: str
    rationale: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    pr_body: str = ""
    risk_level: RiskLevel = RiskLevel.MEDIUM
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentQuery(BaseModel):
    query: str
    pod_name: str | None = None
    pod_id: str | None = None
    service_name: str | None = None
    namespace: str | None = None
    include_timeline: bool = True
    max_commits: int = 20
    generate_proposal: bool = False


class AgentResponse(BaseModel):
    analysis: AnalysisResult
    proposal: Proposal | None = None
    errors: list[str] = Field(default_factory=list)
