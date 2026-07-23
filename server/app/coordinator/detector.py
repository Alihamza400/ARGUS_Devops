from __future__ import annotations

import logging
from typing import Any

from app.coordinator.models import (
    ConflictRecord,
    ConflictSeverity,
    ConflictType,
    ProposalRecord,
    ProposalStatus,
    ResolutionStrategy,
    ResourceType,
)
from app.coordinator.store import ProposalStore
from app.graph.connection import Neo4jConnection

logger = logging.getLogger("argus.coordinator.detector")


class ConflictDetector:
    CONFLICTING_ACTIONS: dict[str, list[str]] = {
        "rollback": ["scale", "config_change", "deploy"],
        "scale": ["rollback", "config_change"],
        "config_change": ["rollback", "scale", "deploy"],
        "deploy": ["rollback", "config_change"],
    }

    @staticmethod
    async def detect_for_proposal(proposal: ProposalRecord) -> list[ConflictRecord]:
        conflicts: list[ConflictRecord] = []

        seen_ids = {proposal.id}

        existing = await ProposalStore.get_active_proposals_for_resource(
            proposal.target_id,
        )
        for existing_proposal in existing:
            if existing_proposal.id in seen_ids:
                continue
            seen_ids.add(existing_proposal.id)
            conflict = await ConflictDetector._evaluate_conflict(
                existing_proposal, proposal,
            )
            if conflict:
                conflicts.append(conflict)

        same_type_existing = await ProposalStore.list_proposals(
            resource_type=proposal.resource_type,
            status=ProposalStatus.PENDING,
            limit=100,
        )
        for existing_proposal in same_type_existing:
            if existing_proposal.id in seen_ids:
                continue
            seen_ids.add(existing_proposal.id)
            conflict = await ConflictDetector._evaluate_conflict(
                existing_proposal, proposal,
            )
            if conflict:
                conflicts.append(conflict)

        cascading = await ConflictDetector._detect_cascading_conflicts(proposal)
        conflicts.extend(cascading)

        unique = {}
        for c in conflicts:
            key = tuple(sorted([c.proposal_a_id, c.proposal_b_id]))
            if key not in unique:
                unique[key] = c
        return list(unique.values())

    @staticmethod
    async def _evaluate_conflict(
        a: ProposalRecord,
        b: ProposalRecord,
    ) -> ConflictRecord | None:
        same_resource = a.target_id == b.target_id
        same_type = a.resource_type == b.resource_type
        a_action = a.action or ""
        b_action = b.action or ""

        if not same_resource and not same_type:
            return None

        action_conflict = ConflictDetector._actions_conflict(a_action, b_action)

        if same_resource and action_conflict:
            return ConflictRecord(
                proposal_a_id=a.id,
                proposal_b_id=b.id,
                conflict_type=ConflictType.DIRECT,
                severity=ConflictSeverity.BLOCKING,
                description=(
                    f"Direct conflict: '{a_action}' on {a.resource_type.value} "
                    f"'{a.target_id}' conflicts with '{b_action}' "
                    f"from agent '{b.agent}'"
                ),
                affected_resource=a.target_id,
                resource_type=a.resource_type,
                resolution_strategy=ResolutionStrategy.AUTO_BLOCK,
                recommendation=(
                    f"Proposal '{b.id}' ({b_action}) conflicts with active "
                    f"proposal '{a.id}' ({a_action}) on the same resource. "
                    f"Auto-blocking until '{a.id}' is resolved."
                ),
            )

        if same_resource and not action_conflict:
            return ConflictRecord(
                proposal_a_id=a.id,
                proposal_b_id=b.id,
                conflict_type=ConflictType.INDIRECT,
                severity=ConflictSeverity.MAJOR,
                description=(
                    f"Indirect conflict: '{a_action}' on {a.resource_type.value} "
                    f"'{a.target_id}' may be affected by '{b_action}' "
                    f"from agent '{b.agent}'"
                ),
                affected_resource=a.target_id,
                resource_type=a.resource_type,
                resolution_strategy=ResolutionStrategy.RANK_AND_PICK,
                recommendation=(
                    f"Proposals '{a.id}' and '{b.id}' target the same resource "
                    f"with compatible but different actions. "
                    f"Rank by evidence score."
                ),
            )

        if not same_resource and same_type:
            return ConflictRecord(
                proposal_a_id=a.id,
                proposal_b_id=b.id,
                conflict_type=ConflictType.COMPLEMENTARY,
                severity=ConflictSeverity.NONE,
                description=(
                    f"Complementary: '{a_action}' on '{a.target_id}' and "
                    f"'{b_action}' on '{b.target_id}' are compatible "
                    f"({a.resource_type.value})"
                ),
                affected_resource=f"{a.target_id}, {b.target_id}",
                resource_type=a.resource_type,
                resolution_strategy=ResolutionStrategy.AUTO_APPROVE,
                recommendation="No conflict detected. Both proposals can proceed.",
            )

        return None

    @staticmethod
    async def _detect_cascading_conflicts(
        proposal: ProposalRecord,
    ) -> list[ConflictRecord]:
        conflicts: list[ConflictRecord] = []
        related = await ConflictDetector._find_related_resources(
            proposal.target_id, proposal.resource_type,
        )

        for related_id in related:
            existing = await ProposalStore.get_active_proposals_for_resource(related_id)
            for ex in existing:
                if ex.id == proposal.id:
                    continue
                conflicts.append(
                    ConflictRecord(
                        proposal_a_id=ex.id,
                        proposal_b_id=proposal.id,
                        conflict_type=ConflictType.CASCADING,
                        severity=ConflictSeverity.MINOR,
                        description=(
                            f"Cascading: '{proposal.action}' on "
                            f"'{proposal.target_id}' affects '{related_id}' "
                            f"which has active proposal '{ex.id}' ({ex.action})"
                        ),
                        affected_resource=related_id,
                        resource_type=proposal.resource_type,
                        resolution_strategy=ResolutionStrategy.FLAG_FOR_REVIEW,
                        recommendation=(
                            f"Cascading impact detected. "
                            f"Review compatibility of '{proposal.id}' "
                            f"with existing proposal '{ex.id}'."
                        ),
                    )
                )

        return conflicts

    @staticmethod
    async def _find_related_resources(
        resource_id: str,
        resource_type: ResourceType,
    ) -> list[str]:
        label = resource_type.value
        related: list[str] = []

        result = await Neo4jConnection.run_query(
            f"""
            MATCH (n:{label} {{id: $rid}})-[:IN|BELONGS_TO|DEPLOYS|DEPLOYED_FROM|RUNS_ON]-(related)
            RETURN DISTINCT related.id AS id
            """,
            {"rid": resource_id},
        )
        for r in result:
            related.append(r["id"])

        result2 = await Neo4jConnection.run_query(
            f"""
            MATCH (n:{label} {{id: $rid}})<-[:IN|BELONGS_TO|DEPLOYS|DEPLOYED_FROM|RUNS_ON]-(related)
            RETURN DISTINCT related.id AS id
            """,
            {"rid": resource_id},
        )
        for r in result2:
            related.append(r["id"])

        return list(set(related))

    @staticmethod
    def _actions_conflict(action_a: str, action_b: str) -> bool:
        if action_a == action_b:
            return True
        return action_b in ConflictDetector.CONFLICTING_ACTIONS.get(action_a, [])

    @staticmethod
    async def get_all_unresolved_conflicts() -> list[ConflictRecord]:
        return await ProposalStore.list_conflicts(resolved=False, limit=100)

    @staticmethod
    async def get_resource_conflict_summary(
        resource_id: str,
    ) -> dict[str, Any]:
        proposals = await ProposalStore.get_active_proposals_for_resource(resource_id)
        conflicts = []
        for p in proposals:
            for c in await ProposalStore.get_conflicts_for_proposal(p.id, include_resolved=False):
                conflicts.append(c)

        return {
            "resource_id": resource_id,
            "active_proposals": len(proposals),
            "unresolved_conflicts": len(conflicts),
            "conflicts": [c.model_dump() for c in conflicts],
            "proposals": [p.model_dump() for p in proposals],
        }
