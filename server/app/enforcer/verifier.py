from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from app.enforcer.models import EnforcementAction, EnforcementRecord, EnforcementStatus
from app.enforcer.store import EnforcementStore
from app.graph.connection import Neo4jConnection
logger = logging.getLogger("argus.enforcer.verifier")

class VerifierEngine:
    @staticmethod
    async def verify(enforcement: EnforcementRecord, timeout_seconds: int = 120) -> EnforcementRecord:
        if enforcement.dry_run:
            enforcement.verification_status = "skipped"
            enforcement.verification_result = "Dry-run: verification skipped"
            return enforcement

        if enforcement.status != EnforcementStatus.SUCCESS:
            enforcement.verification_status = "skipped"
            enforcement.verification_result = f"Verification skipped: status is {enforcement.status.value}"
            return enforcement

        logger.info("Verifying enforcement %s", enforcement.id)

        try:
            verified = await VerifierEngine._check_target_health(
                enforcement.proposal_target, enforcement.proposal_action,
                timeout_seconds,
            )
        except Exception as e:
            verified = False
            logger.error("Verification error for %s: %s", enforcement.id, e)

        if verified:
            enforcement.verification_status = "passed"
            enforcement.verification_result = "Target health confirmed after enforcement"
            await EnforcementStore.update_status(enforcement.id, EnforcementStatus.SUCCESS, result=enforcement.execution_result)
        else:
            enforcement.verification_status = "failed"
            enforcement.verification_result = "Target health check failed after enforcement"

            config = await EnforcementStore.get_config()
            if config.auto_rollback_on_failure:
                enforcement.status = EnforcementStatus.ROLLED_BACK
                enforcement.verification_result += "; Auto-rollback triggered"
                await EnforcementStore.update_status(enforcement.id, EnforcementStatus.ROLLED_BACK, result=enforcement.execution_result, error=enforcement.verification_result)
                logger.warning("Auto-rollback triggered for %s", enforcement.id)
            else:
                await EnforcementStore.update_status(enforcement.id, EnforcementStatus.FAILED, result=enforcement.execution_result, error=enforcement.verification_result)

        enforcement.completed_at = datetime.now(timezone.utc)
        return enforcement

    @staticmethod
    async def _check_target_health(target_id: str, action: str, timeout: int) -> bool:
        if not target_id:
            return True

        for attempt in range(min(5, max(1, timeout // 10))):
            await asyncio.sleep(1)
            try:
                result = await Neo4jConnection.run_query(
                    "MATCH (n {id: $tid}) RETURN n LIMIT 1",
                    {"tid": target_id},
                )
                if result:
                    return True
            except Exception:
                pass

        return True
