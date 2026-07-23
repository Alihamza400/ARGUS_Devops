from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class EnforcementStatus(str, Enum):
    PENDING = "pending"
    PRECHECK_PASSED = "precheck_passed"
    PRECHECK_FAILED = "precheck_failed"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    DRY_RUN = "dry_run"
    SKIPPED = "skipped"


class EnforcementAction(str, Enum):
    ROLLBACK = "rollback"
    UPDATE_MANIFEST = "update_manifest"
    SUBMIT_PR = "submit_pr"
    SCALE = "scale"
    CONFIG_CHANGE = "config_change"
    CUSTOM = "custom"


class PreCheckRule(str, Enum):
    CHANGE_WINDOW = "change_window"
    BLAST_RADIUS = "blast_radius"
    RATE_LIMIT = "rate_limit"
    POLICY_COMPLIANCE = "policy_compliance"
    MAINTENANCE_MODE = "maintenance_mode"


class EnforcementRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"enf-{uuid.uuid4().hex[:12]}")
    proposal_id: str = ""
    proposal_action: str = ""
    proposal_target: str = ""
    proposal_title: str = ""
    action: EnforcementAction = EnforcementAction.CUSTOM
    status: EnforcementStatus = EnforcementStatus.PENDING
    executed_by: str = ""
    dry_run: bool = False
    precheck_passed: bool = False
    precheck_details: str = ""
    execution_result: str = ""
    verification_result: str = ""
    verification_status: str = ""
    error_message: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    pr_url: str = ""
    commit_sha: str = ""
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_dump_for_cypher(self) -> dict[str, Any]:
        data = self.model_dump()
        for k in ("started_at", "completed_at"):
            if data.get(k):
                v = data[k]; data[k] = v.isoformat() if hasattr(v, "isoformat") else v
        if isinstance(data.get("metadata"), dict):
            import json; data["metadata"] = json.dumps(data["metadata"])
        return data


class PreCheckRequest(BaseModel):
    proposal_id: str = ""
    action: EnforcementAction = EnforcementAction.CUSTOM
    target_id: str = ""
    target_type: str = "Service"
    agent: str = ""
    dry_run: bool = False


class PreCheckResult(BaseModel):
    passed: bool = True
    checks: dict[str, Any] = Field(default_factory=lambda: {
        "change_window": True, "blast_radius": True, "rate_limit": True,
        "policy_compliance": True, "maintenance_mode": True,
    })
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    details: str = ""


class EnforceRequest(BaseModel):
    proposal_id: str = ""
    executed_by: str = "argus-enforcer"
    dry_run: bool = False
    skip_precheck: bool = False
    skip_verification: bool = False


class EnforceResponse(BaseModel):
    enforcement: EnforcementRecord
    precheck: PreCheckResult | None = None
    success: bool = False
    message: str = ""


class ChangeWindow(BaseModel):
    start_hour: int = 0
    end_hour: int = 23
    timezone: str = "UTC"
    allowed_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    enabled: bool = False


class EnforcerConfig(BaseModel):
    dry_run_default: bool = True
    require_approval: bool = True
    max_concurrent_enforcements: int = 3
    verification_timeout_seconds: int = 120
    auto_rollback_on_failure: bool = True
    change_window: ChangeWindow = Field(default_factory=ChangeWindow)
    allowed_actions: list[str] = Field(default_factory=lambda: ["rollback", "scale", "config_change"])
    blocked_actions: list[str] = Field(default_factory=list)


class EnforcementQuery(BaseModel):
    proposal_id: str | None = None
    status: EnforcementStatus | None = None
    action: EnforcementAction | None = None
    limit: int = 50
    offset: int = 0
