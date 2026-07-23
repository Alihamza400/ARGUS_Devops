from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.coordinator.analyzer import ConflictAnalyzer
from app.coordinator.detector import ConflictDetector
from app.coordinator.models import (
    ConflictRecord,
    ConflictResolutionRequest,
    ProposalRecord,
    ProposalStatus,
    ResourceLock,
    ResourceType,
    ResolutionStrategy,
    SubmitProposalRequest,
    SubmitProposalResponse,
)
from app.coordinator.resolver import ConflictResolver
from app.coordinator.store import ProposalStore

logger = logging.getLogger("argus.coordinator")


class ConflictCoordinator:
    @staticmethod
    async def submit_proposal(
        request: SubmitProposalRequest,
    ) -> SubmitProposalResponse:
        proposal = ProposalRecord(
            agent=request.agent,
            agent_version=request.agent_version,
            title=request.title,
            description=request.description,
            action=request.action,
            target_id=request.target_id,
            target_type=request.target_type,
            resource_type=request.resource_type,
            rationale=request.rationale,
            evidence_count=request.evidence_count,
            evidence_summary=request.evidence_summary,
            confidence=min(max(request.confidence, 0.0), 1.0),
            risk_level=request.risk_level,
            severity=request.severity,
            pr_body=request.pr_body,
            tags=request.tags,
            metadata=request.metadata,
        )

        await ProposalStore.create_proposal(proposal)
        logger.info("Proposal submitted: %s by agent '%s'", proposal.id, proposal.agent)

        conflicts = await ConflictDetector.detect_for_proposal(proposal)

        if conflicts:
            for conflict in conflicts:
                await ProposalStore.create_conflict(conflict)
                logger.info(
                    "Conflict detected: %s (%s)", conflict.id, conflict.conflict_type.value,
                )

                resolved = await ConflictResolver.resolve(conflict)
                if resolved:
                    logger.info("Conflict resolved: %s", conflict.id)

            active_conflicts = [
                c for c in conflicts
                if c.severity.value in ("blocking", "major")
            ]

            if active_conflicts:
                final_status = ProposalStatus.PENDING
                message = (
                    f"Proposal submitted with {len(active_conflicts)} "
                    f"active conflict(s). Awaiting resolution."
                )
            else:
                final_status = ProposalStatus.APPROVED
                message = "Proposal approved. No blocking conflicts."
        else:
            final_status = ProposalStatus.APPROVED
            message = "Proposal approved. No conflicts detected."

        await ProposalStore.update_proposal_status(
            proposal.id,
            final_status,
            resolution=message,
            resolved_by="ConflictCoordinator",
        )
        proposal.status = final_status

        return SubmitProposalResponse(
            proposal=proposal,
            conflicts=conflicts,
            status=final_status,
            message=message,
            resolution=ConflictAnalyzer.generate_recommendation(
                ConflictRecord(
                    proposal_a_id="",
                    proposal_b_id="",
                    affected_resource=proposal.target_id,
                    resource_type=proposal.resource_type,
                ),
                [(proposal, ConflictAnalyzer.score_proposal(proposal))],
            ) if not conflicts else "",
        )

    @staticmethod
    async def acquire_proposal_lock(
        resource_id: str,
        resource_type: ResourceType,
        proposal_id: str,
        agent: str,
        ttl_minutes: int = 30,
    ) -> bool:
        lock = ResourceLock(
            resource_id=resource_id,
            resource_type=resource_type,
            proposal_id=proposal_id,
            agent=agent,
            expires_at=datetime.now(timezone.utc) + __import__("datetime").timedelta(minutes=ttl_minutes) if ttl_minutes else None,
        )
        acquired = await ProposalStore.acquire_lock(lock)
        if acquired:
            logger.info("Lock acquired: %s by %s for %s", resource_id, agent, proposal_id)
        else:
            logger.warning("Lock not acquired: %s is already locked", resource_id)
        return acquired

    @staticmethod
    async def release_proposal_lock(resource_id: str, proposal_id: str) -> bool:
        released = await ProposalStore.release_lock(resource_id, proposal_id)
        if released:
            logger.info("Lock released: %s by %s", resource_id, proposal_id)
        return released

    @staticmethod
    async def resolve_conflict_manually(
        request: ConflictResolutionRequest,
    ) -> ConflictRecord | None:
        conflict = await ProposalStore.get_conflicts_for_proposal(
            request.conflict_id, include_resolved=True,
        )
        if not conflict:
            return None

        c = conflict[0]
        c.resolved = True
        c.resolution_strategy = request.resolution_strategy
        c.recommendation = request.notes

        result = await ConflictResolver.resolve(c, request)
        return result

    @staticmethod
    async def get_proposal(proposal_id: str) -> ProposalRecord | None:
        return await ProposalStore.get_proposal(proposal_id)

    @staticmethod
    async def list_proposals(
        resource_id: str | None = None,
        resource_type: ResourceType | None = None,
        status: ProposalStatus | None = None,
        agent: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProposalRecord]:
        return await ProposalStore.list_proposals(
            resource_id=resource_id,
            resource_type=resource_type,
            status=status,
            agent=agent,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    async def list_conflicts(resolved: bool | None = None) -> list[ConflictRecord]:
        return await ProposalStore.list_conflicts(resolved=resolved, limit=100)

    @staticmethod
    async def get_resource_summary(resource_id: str) -> dict[str, Any]:
        return await ConflictDetector.get_resource_conflict_summary(resource_id)

    @staticmethod
    async def ensure_schema() -> list[dict]:
        return await ProposalStore.ensure_schema()

    @staticmethod
    async def release_expired_locks() -> int:
        return await ProposalStore.release_expired_locks()

    @staticmethod
    async def health() -> dict[str, Any]:
        active_proposals = await ProposalStore.count_proposals(status=ProposalStatus.PENDING)
        unresolved = len(await ProposalStore.list_conflicts(resolved=False))

        return {
            "active_proposals": active_proposals,
            "unresolved_conflicts": unresolved,
            "status": "healthy" if unresolved == 0 else "conflicts_pending",
        }
