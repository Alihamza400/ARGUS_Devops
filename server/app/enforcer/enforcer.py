from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any
from app.coordinator.models import ProposalRecord, ProposalStatus
from app.coordinator.store import ProposalStore
from app.enforcer.executor import ExecutorEngine
from app.enforcer.models import (
    EnforceRequest, EnforceResponse, EnforcementAction, EnforcementRecord, EnforcementStatus,
    EnforcerConfig, PreCheckRequest, PreCheckResult,
)
from app.enforcer.precheck import PreCheckEngine
from app.enforcer.store import EnforcementStore
from app.enforcer.verifier import VerifierEngine
logger = logging.getLogger("argus.enforcer")

class EnforcerCoordinator:
    @staticmethod
    async def enforce(request: EnforceRequest) -> EnforceResponse:
        proposal = await ProposalStore.get_proposal(request.proposal_id)
        if not proposal:
            return EnforceResponse(
                enforcement=EnforcementRecord(proposal_id=request.proposal_id),
                success=False, message=f"Proposal '{request.proposal_id}' not found",
            )

        if proposal.status != ProposalStatus.APPROVED and not request.dry_run:
            return EnforceResponse(
                enforcement=EnforcementRecord(proposal_id=request.proposal_id),
                success=False, message=f"Proposal is '{proposal.status.value}', must be 'approved'",
            )

        action_map = {
            "rollback": EnforcementAction.ROLLBACK, "scale": EnforcementAction.SCALE,
            "submit_pr": EnforcementAction.SUBMIT_PR, "update_manifest": EnforcementAction.UPDATE_MANIFEST,
            "config_change": EnforcementAction.CONFIG_CHANGE,
        }
        action = action_map.get(proposal.action or "", EnforcementAction.CUSTOM)

        enforcement = EnforcementRecord(
            proposal_id=proposal.id, proposal_action=proposal.action or "",
            proposal_target=proposal.target_id or "", proposal_title=proposal.title or "",
            action=action, dry_run=request.dry_run, executed_by=request.executed_by,
        )

        precheck_result = None
        if not request.skip_precheck:
            pre_req = PreCheckRequest(
                proposal_id=proposal.id, action=action,
                target_id=proposal.target_id, target_type=proposal.target_type,
                agent=proposal.agent, dry_run=request.dry_run,
            )
            precheck_result = await PreCheckEngine.run(pre_req)
            enforcement.precheck_passed = precheck_result.passed
            enforcement.precheck_details = precheck_result.details

            if not precheck_result.passed and not request.dry_run:
                enforcement.status = EnforcementStatus.PRECHECK_FAILED
                await EnforcementStore.create(enforcement)
                await EnforcementStore.update_status(enforcement.id, EnforcementStatus.PRECHECK_FAILED, result=precheck_result.details)
                return EnforceResponse(
                    enforcement=enforcement, precheck=precheck_result,
                    success=False, message=f"Prechecks failed: {'; '.join(precheck_result.failures)}",
                )

        await EnforcementStore.create(enforcement)

        enforcement = await ExecutorEngine.execute(enforcement, proposal, dry_run=request.dry_run)

        if not request.skip_verification and not request.dry_run and enforcement.status == EnforcementStatus.SUCCESS:
            config = await EnforcementStore.get_config()
            enforcement = await VerifierEngine.verify(enforcement, timeout_seconds=config.verification_timeout_seconds)

        success = enforcement.status in (EnforcementStatus.SUCCESS, EnforcementStatus.DRY_RUN)
        return EnforceResponse(
            enforcement=enforcement, precheck=precheck_result,
            success=success, message=f"Enforcement {enforcement.status.value}: {enforcement.execution_result or enforcement.error_message or 'completed'}",
        )

    @staticmethod
    async def get_enforcement(enforcement_id: str) -> EnforcementRecord | None:
        return await EnforcementStore.get(enforcement_id)

    @staticmethod
    async def list_enforcements(proposal_id: str | None = None, status: EnforcementStatus | None = None, limit: int = 50, offset: int = 0) -> list[EnforcementRecord]:
        return await EnforcementStore.list(proposal_id=proposal_id, status=status, limit=limit, offset=offset)

    @staticmethod
    async def get_config() -> EnforcerConfig:
        return await EnforcementStore.get_config()

    @staticmethod
    async def update_config(config: EnforcerConfig) -> None:
        await EnforcementStore.save_config(config)

    @staticmethod
    async def ensure_schema() -> list[dict]:
        return await EnforcementStore.ensure_schema()

    @staticmethod
    async def health() -> dict[str, Any]:
        recent = await EnforcementStore.list(limit=10)
        failures = [e for e in recent if e.status == EnforcementStatus.FAILED]
        return {"recent_enforcements": len(recent), "recent_failures": len(failures), "status": "healthy" if len(failures) < 3 else "degraded"}
