from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.coordinator.analyzer import ConflictAnalyzer
from app.coordinator.models import (
    ConflictRecord,
    ConflictResolutionRequest,
    ProposalRecord,
    ProposalStatus,
    ResolutionStrategy,
)
from app.coordinator.store import ProposalStore

logger = logging.getLogger("argus.coordinator.resolver")


class ConflictResolver:
    @staticmethod
    async def resolve(
        conflict: ConflictRecord,
        request: ConflictResolutionRequest | None = None,
    ) -> ConflictRecord:
        proposal_a = await ProposalStore.get_proposal(conflict.proposal_a_id)
        proposal_b = await ProposalStore.get_proposal(conflict.proposal_b_id)

        if not proposal_a or not proposal_b:
            logger.warning("Cannot resolve conflict %s: proposals not found", conflict.id)
            return conflict

        ranked = ConflictAnalyzer.rank_proposals([proposal_a, proposal_b])

        if request and request.override:
            strategy = request.resolution_strategy
        else:
            strategy = ConflictAnalyzer.recommend_resolution(conflict, ranked)

        recommendation = ConflictAnalyzer.generate_recommendation(conflict, ranked)

        logger.info(
            "Resolving conflict %s with strategy %s",
            conflict.id, strategy.value,
        )

        if strategy == ResolutionStrategy.AUTO_APPROVE:
            await ConflictResolver._apply_auto_approve(conflict, ranked)
        elif strategy == ResolutionStrategy.AUTO_BLOCK:
            await ConflictResolver._apply_auto_block(conflict, ranked)
        elif strategy == ResolutionStrategy.RANK_AND_PICK:
            await ConflictResolver._apply_rank_and_pick(conflict, ranked)
        elif strategy == ResolutionStrategy.FLAG_FOR_REVIEW:
            await ConflictResolver._apply_flag_for_review(conflict)
        elif strategy == ResolutionStrategy.MERGE_IF_COMPATIBLE:
            await ConflictResolver._apply_merge_if_compatible(conflict, ranked)

        conflict.resolution_strategy = strategy
        conflict.recommendation = recommendation
        conflict.resolved = True

        await ProposalStore.resolve_conflict(
            conflict.id,
            resolved=True,
            recommendation=recommendation,
        )

        return conflict

    @staticmethod
    async def _apply_auto_approve(
        conflict: ConflictRecord,
        ranked: list[tuple[ProposalRecord, float]],
    ) -> None:
        for proposal, score in ranked:
            await ProposalStore.update_proposal_status(
                proposal.id,
                ProposalStatus.APPROVED,
                resolution=f"Auto-approved (score: {score:.3f})",
                resolved_by="ConflictResolver",
                resolver_notes="No conflict detected.",
            )
            logger.info("Auto-approved proposal %s (score: %.3f)", proposal.id, score)

    @staticmethod
    async def _apply_auto_block(
        conflict: ConflictRecord,
        ranked: list[tuple[ProposalRecord, float]],
    ) -> None:
        winner = ranked[0] if ranked else None
        for proposal, score in ranked:
            if winner and proposal.id == winner[0].id:
                await ProposalStore.update_proposal_status(
                    proposal.id,
                    ProposalStatus.PENDING,
                    resolution="Highest ranked, awaiting resolution of blocked proposals",
                    resolved_by="ConflictResolver",
                    resolver_notes=f"Ranked first with score {score:.3f}",
                )
            else:
                await ProposalStore.update_proposal_status(
                    proposal.id,
                    ProposalStatus.BLOCKED,
                    resolution=f"Blocked by higher-ranked proposal '{winner[0].id if winner else 'unknown'}'",
                    resolved_by="ConflictResolver",
                    resolver_notes=f"Lower score ({score:.3f}) in direct conflict",
                )
                logger.info("Blocked proposal %s (score: %.3f)", proposal.id, score)

    @staticmethod
    async def _apply_rank_and_pick(
        conflict: ConflictRecord,
        ranked: list[tuple[ProposalRecord, float]],
    ) -> None:
        ranked.sort(key=lambda x: x[1], reverse=True)
        for i, (proposal, score) in enumerate(ranked):
            if i == 0:
                await ProposalStore.update_proposal_status(
                    proposal.id,
                    ProposalStatus.APPROVED,
                    resolution=f"Selected by ranking (score: {score:.3f})",
                    resolved_by="ConflictResolver",
                    resolver_notes=f"Ranked #{i+1} of {len(ranked)}",
                )
            else:
                await ProposalStore.update_proposal_status(
                    proposal.id,
                    ProposalStatus.SUPERSEDED,
                    resolution=f"Superseded by '{ranked[0][0].id}' (score: {score:.3f} vs {ranked[0][1]:.3f})",
                    resolved_by="ConflictResolver",
                    resolver_notes=f"Ranked #{i+1} of {len(ranked)}",
                )

    @staticmethod
    async def _apply_flag_for_review(
        conflict: ConflictRecord,
    ) -> None:
        for pid in (conflict.proposal_a_id, conflict.proposal_b_id):
            await ProposalStore.update_proposal_status(
                pid,
                ProposalStatus.PENDING,
                resolution="Flagged for human review due to conflict",
                resolved_by="ConflictResolver",
                resolver_notes=f"Conflict {conflict.id} requires manual resolution",
            )

    @staticmethod
    async def _apply_merge_if_compatible(
        conflict: ConflictRecord,
        ranked: list[tuple[ProposalRecord, float]],
    ) -> None:
        for proposal, score in ranked:
            await ProposalStore.update_proposal_status(
                proposal.id,
                ProposalStatus.APPROVED,
                resolution=f"Approved (compatible, score: {score:.3f})",
                resolved_by="ConflictResolver",
                resolver_notes="Compatible actions merged.",
            )

    @staticmethod
    async def resolve_all_for_proposal(proposal_id: str) -> list[ConflictRecord]:
        conflicts = await ProposalStore.get_conflicts_for_proposal(
            proposal_id, include_resolved=False,
        )
        resolved = []
        for conflict in conflicts:
            result = await ConflictResolver.resolve(conflict)
            resolved.append(result)
        return resolved
