from __future__ import annotations
import logging
from typing import Any
from app.gate.models import ApprovalPolicyConfig, PolicyCheckResult, ReviewerRole
from app.gate.store import ReviewStore
logger = logging.getLogger("argus.gate.policy")

class ApprovalPolicyEngine:
    @staticmethod
    async def check(proposal: dict[str, Any] | None = None, reviewer: str = "", reviewer_role: ReviewerRole = ReviewerRole.PEER) -> PolicyCheckResult:
        config = await ReviewStore.get_policy_config()
        violations, warnings = [], []
        if proposal is None: return PolicyCheckResult(violations=["Proposal data required"])
        if config.rules.get("no_self_approval", True):
            agent = proposal.get("agent", "")
            if agent and agent == reviewer: violations.append(f"Self-approval not allowed: agent '{agent}'")
        sev = proposal.get("severity", "low")
        if config.rules.get("senior_for_critical", True) and sev == "critical" and reviewer_role != ReviewerRole.SENIOR:
            violations.append("Critical severity requires senior reviewer")
        min_ev = int(config.rules.get("min_evidence_count", 1))
        if int(proposal.get("evidence_count", 0)) < min_ev: violations.append(f"Need {min_ev} evidence items, have {proposal.get('evidence_count', 0)}")
        min_cf = float(config.rules.get("min_confidence", 0.0))
        if float(proposal.get("confidence", 0.0)) < min_cf: violations.append(f"Confidence {proposal.get('confidence', 0):.2f} < min {min_cf:.2f}")
        return PolicyCheckResult(passed=len(violations) == 0, violations=violations, warnings=warnings)
    @staticmethod
    async def is_approval_complete(proposal_id: str) -> bool:
        config = await ReviewStore.get_policy_config()
        return (await ReviewStore.get_approval_count(proposal_id)) >= int(config.rules.get("min_reviewers", 1))
    @staticmethod
    async def update_policy(config: ApprovalPolicyConfig) -> None:
        await ReviewStore.save_policy_config(config)
