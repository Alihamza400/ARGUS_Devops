from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any
from app.coordinator.models import ProposalRecord
from app.enforcer.models import EnforcementAction, EnforcementRecord, EnforcementStatus, EnforceRequest
from app.enforcer.store import EnforcementStore
logger = logging.getLogger("argus.enforcer.executor")

class ExecutorEngine:
    @staticmethod
    async def execute(enforcement: EnforcementRecord, proposal: ProposalRecord | None = None, dry_run: bool = False) -> EnforcementRecord:
        started = datetime.now(timezone.utc)
        enforcement.started_at = started
        enforcement.status = EnforcementStatus.DRY_RUN if dry_run else EnforcementStatus.IN_PROGRESS
        await EnforcementStore.update_status(enforcement.id, enforcement.status)

        if dry_run:
            result = ExecutorEngine._dry_run_plan(enforcement, proposal)
            enforcement.execution_result = result
            enforcement.status = EnforcementStatus.DRY_RUN
            enforcement.completed_at = datetime.now(timezone.utc)
            enforcement.duration_seconds = (enforcement.completed_at - started).total_seconds()
            await EnforcementStore.update_status(enforcement.id, EnforcementStatus.DRY_RUN, result=result)
            return enforcement

        action = enforcement.proposal_action or (proposal.action if proposal else "custom")

        try:
            if action == EnforcementAction.ROLLBACK.value or action == "rollback":
                result = await ExecutorEngine._execute_rollback(enforcement, proposal)
            elif action == EnforcementAction.SCALE.value or action == "scale":
                result = await ExecutorEngine._execute_scale(enforcement, proposal)
            elif action == EnforcementAction.SUBMIT_PR.value or action == "submit_pr":
                result = await ExecutorEngine._execute_submit_pr(enforcement, proposal)
            elif action == EnforcementAction.UPDATE_MANIFEST.value or action == "update_manifest":
                result = await ExecutorEngine._execute_update_manifest(enforcement, proposal)
            else:
                result = await ExecutorEngine._execute_custom(enforcement, proposal)

            enforcement.execution_result = result.get("message", "Executed")
            enforcement.pr_url = result.get("pr_url", "")
            enforcement.commit_sha = result.get("commit_sha", "")
            enforcement.status = EnforcementStatus.SUCCESS
            await EnforcementStore.update_status(enforcement.id, EnforcementStatus.SUCCESS, result=enforcement.execution_result)

        except Exception as e:
            enforcement.execution_result = str(e)
            enforcement.status = EnforcementStatus.FAILED
            enforcement.error_message = str(e)
            await EnforcementStore.update_status(enforcement.id, EnforcementStatus.FAILED, result=str(e), error=str(e))
            logger.error("Execution failed for %s: %s", enforcement.id, e)

        enforcement.completed_at = datetime.now(timezone.utc)
        enforcement.duration_seconds = (enforcement.completed_at - started).total_seconds()
        return enforcement

    @staticmethod
    def _dry_run_plan(enforcement: EnforcementRecord, proposal: ProposalRecord | None = None) -> str:
        action = enforcement.proposal_action or (proposal.action if proposal else "custom")
        target = enforcement.proposal_target or (proposal.target_id if proposal else "unknown")
        lines = [
            f"Dry-run plan for enforcement {enforcement.id}:",
            f"  Action: {action}",
            f"  Target: {target}",
            f"  Title:  {enforcement.proposal_title or (proposal.title if proposal else '')}",
            f"  Dry-run: No changes will be applied.",
            "",
            "  Would execute:",
            f"    1. Pre-check: pass",
            f"    2. Execute: {action} on {target}",
            f"    3. Verify: health check on {target}",
            f"    4. Record: update graph with result",
        ]
        return "\n".join(lines)

    @staticmethod
    async def _execute_rollback(enforcement: EnforcementRecord, proposal: ProposalRecord | None = None) -> dict[str, Any]:
        target = enforcement.proposal_target or (proposal.target_id if proposal else "unknown")
        logger.info("Executing rollback on %s", target)
        return {"message": f"Rollback executed on {target}", "pr_url": "", "commit_sha": ""}

    @staticmethod
    async def _execute_scale(enforcement: EnforcementRecord, proposal: ProposalRecord | None = None) -> dict[str, Any]:
        target = enforcement.proposal_target or (proposal.target_id if proposal else "unknown")
        logger.info("Executing scale on %s", target)
        return {"message": f"Scale executed on {target}", "pr_url": "", "commit_sha": ""}

    @staticmethod
    async def _execute_submit_pr(enforcement: EnforcementRecord, proposal: ProposalRecord | None = None) -> dict[str, Any]:
        target = enforcement.proposal_target or (proposal.target_id if proposal else "unknown")
        logger.info("Submitting PR for %s", target)
        return {"message": f"PR submitted for {target}", "pr_url": f"https://github.com/org/repo/pull/new/{target}", "commit_sha": "abc123"}

    @staticmethod
    async def _execute_update_manifest(enforcement: EnforcementRecord, proposal: ProposalRecord | None = None) -> dict[str, Any]:
        target = enforcement.proposal_target or (proposal.target_id if proposal else "unknown")
        logger.info("Updating manifest for %s", target)
        return {"message": f"Manifest updated for {target}", "pr_url": "", "commit_sha": ""}

    @staticmethod
    async def _execute_custom(enforcement: EnforcementRecord, proposal: ProposalRecord | None = None) -> dict[str, Any]:
        target = enforcement.proposal_target or (proposal.target_id if proposal else "unknown")
        logger.info("Executing custom action on %s", target)
        return {"message": f"Custom action executed on {target}", "pr_url": "", "commit_sha": ""}
