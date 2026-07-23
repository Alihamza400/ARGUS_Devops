from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.coordinator.models import (
    ConflictRecord,
    ConflictType,
    ProposalRecord,
    ProposalStatus,
    ResourceLock,
    ResourceType,
    ResolutionStrategy,
)
from app.graph.connection import Neo4jConnection

logger = logging.getLogger("argus.coordinator.store")


class ProposalStore:
    NODE_LABEL = "Proposal"
    CONFLICT_LABEL = "ProposalConflict"
    LOCK_LABEL = "ResourceLock"

    # ------------------------------------------------------------------
    # Proposal CRUD
    # ------------------------------------------------------------------

    @staticmethod
    async def create_proposal(proposal: ProposalRecord) -> ProposalRecord:
        data = proposal.model_dump_for_cypher()
        keys = [k for k in data if data[k] is not None]
        props = ", ".join(f"{k}: ${k}" for k in keys)
        try:
            await Neo4jConnection.run_query(
                f"CREATE (n:{ProposalStore.NODE_LABEL} {{{props}}}) RETURN n",
                {k: data[k] for k in keys},
            )
        except Exception as e:
            if "already exists" in str(e):
                logger.info("Proposal already exists: %s", proposal.id)
            else:
                raise
        logger.info("Proposal created: %s (%s)", proposal.id, proposal.action)
        return proposal

    @staticmethod
    async def get_proposal(proposal_id: str) -> ProposalRecord | None:
        result = await Neo4jConnection.run_query(
            f"MATCH (n:{ProposalStore.NODE_LABEL} {{id: $id}}) RETURN n",
            {"id": proposal_id},
        )
        if not result:
            return None
        return ProposalStore._node_to_proposal(result[0]["n"])

    @staticmethod
    async def update_proposal_status(
        proposal_id: str,
        status: ProposalStatus,
        resolution: str = "",
        resolved_by: str = "",
        resolver_notes: str = "",
    ) -> bool:
        result = await Neo4jConnection.run_query(
            f"""
            MATCH (n:{ProposalStore.NODE_LABEL} {{id: $id}})
            SET n.status = $status,
                n.updated_at = $now,
                n.resolution = $resolution,
                n.resolved_by = $resolved_by,
                n.resolver_notes = $resolver_notes
            RETURN n
            """,
            {
                "id": proposal_id,
                "status": status.value,
                "now": datetime.now(timezone.utc).isoformat(),
                "resolution": resolution,
                "resolved_by": resolved_by,
                "resolver_notes": resolver_notes,
            },
        )
        return len(result) > 0

    @staticmethod
    async def list_proposals(
        resource_id: str | None = None,
        resource_type: ResourceType | None = None,
        status: ProposalStatus | None = None,
        agent: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProposalRecord]:
        conditions = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        if resource_id:
            conditions.append("n.target_id = $resource_id")
            params["resource_id"] = resource_id
        if resource_type:
            conditions.append("n.resource_type = $resource_type")
            params["resource_type"] = resource_type.value
        if status:
            conditions.append("n.status = $status")
            params["status"] = status.value
        if agent:
            conditions.append("n.agent = $agent")
            params["agent"] = agent

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        result = await Neo4jConnection.run_query(
            f"""
            MATCH (n:{ProposalStore.NODE_LABEL})
            WHERE {where_clause}
            RETURN n
            ORDER BY n.created_at DESC
            SKIP $offset LIMIT $limit
            """,
            params,
        )
        return [ProposalStore._node_to_proposal(r["n"]) for r in result]

    @staticmethod
    async def count_proposals(
        resource_id: str | None = None,
        status: ProposalStatus | None = None,
    ) -> int:
        conditions = []
        params: dict[str, Any] = {}
        if resource_id:
            conditions.append("n.target_id = $resource_id")
            params["resource_id"] = resource_id
        if status:
            conditions.append("n.status = $status")
            params["status"] = status.value

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        result = await Neo4jConnection.run_query(
            f"""
            MATCH (n:{ProposalStore.NODE_LABEL})
            WHERE {where_clause}
            RETURN count(n) AS cnt
            """,
            params,
        )
        return result[0]["cnt"] if result else 0

    @staticmethod
    async def get_active_proposals_for_resource(
        resource_id: str,
    ) -> list[ProposalRecord]:
        return await ProposalStore.list_proposals(
            resource_id=resource_id,
            status=ProposalStatus.PENDING,
            limit=100,
        )

    # ------------------------------------------------------------------
    # Conflict CRUD
    # ------------------------------------------------------------------

    @staticmethod
    async def create_conflict(conflict: ConflictRecord) -> ConflictRecord:
        data = conflict.model_dump_for_cypher()
        keys = [k for k in data if data[k] is not None]
        props = ", ".join(f"{k}: ${k}" for k in keys)
        try:
            await Neo4jConnection.run_query(
                f"CREATE (n:{ProposalStore.CONFLICT_LABEL} {{{props}}}) RETURN n",
                {k: data[k] for k in keys},
            )
        except Exception as e:
            if "already exists" in str(e):
                logger.info("Conflict already exists: %s", conflict.id)
            else:
                raise

        await Neo4jConnection.run_query(
            f"""
            MATCH (a:{ProposalStore.NODE_LABEL} {{id: $a_id}})
            MATCH (b:{ProposalStore.NODE_LABEL} {{id: $b_id}})
            CREATE (a)-[:CONFLICTS_WITH {{conflict_id: $conflict_id}}]->(b)
            """,
            {
                "a_id": conflict.proposal_a_id,
                "b_id": conflict.proposal_b_id,
                "conflict_id": conflict.id,
            },
        )

        logger.info(
            "Conflict created: %s (%s) between %s and %s",
            conflict.id,
            conflict.conflict_type.value,
            conflict.proposal_a_id,
            conflict.proposal_b_id,
        )
        return conflict

    @staticmethod
    async def get_conflicts_for_proposal(
        proposal_id: str,
        include_resolved: bool = False,
    ) -> list[ConflictRecord]:
        all_conflicts = await ProposalStore.list_conflicts(
            resolved=None if include_resolved else False,
            limit=200,
        )
        return [
            c for c in all_conflicts
            if c.proposal_a_id == proposal_id or c.proposal_b_id == proposal_id
        ]

    @staticmethod
    async def resolve_conflict(
        conflict_id: str,
        resolved: bool = True,
        recommendation: str = "",
    ) -> bool:
        result = await Neo4jConnection.run_query(
            f"""
            MATCH (n:{ProposalStore.CONFLICT_LABEL} {{id: $id}})
            SET n.resolved = $resolved, n.recommendation = $recommendation
            RETURN n
            """,
            {"id": conflict_id, "resolved": resolved, "recommendation": recommendation},
        )
        return len(result) > 0

    @staticmethod
    async def list_conflicts(
        resolved: bool | None = None,
        limit: int = 50,
    ) -> list[ConflictRecord]:
        condition = ""
        params: dict[str, Any] = {"limit": limit}
        if resolved is not None:
            condition = "WHERE n.resolved = $resolved"
            params["resolved"] = resolved

        result = await Neo4jConnection.run_query(
            f"""
            MATCH (n:{ProposalStore.CONFLICT_LABEL})
            {condition}
            RETURN n
            ORDER BY n.created_at DESC
            LIMIT $limit
            """,
            params,
        )
        return [ProposalStore._node_to_conflict(r["n"]) for r in result]

    # ------------------------------------------------------------------
    # Resource Locks
    # ------------------------------------------------------------------

    @staticmethod
    async def acquire_lock(lock: ResourceLock) -> bool:
        existing = await Neo4jConnection.run_query(
            f"""
            MATCH (n:{ProposalStore.LOCK_LABEL})
            WHERE n.resource_id = $rid AND n.released = false
            RETURN n LIMIT 1
            """,
            {"rid": lock.resource_id},
        )
        if existing:
            return False

        data = lock.model_dump()
        for key in ("acquired_at", "expires_at"):
            if data.get(key):
                val = data[key]
                data[key] = val.isoformat() if hasattr(val, "isoformat") else val
        props = ", ".join(f"{k}: ${k}" for k in data)
        await Neo4jConnection.run_query(
            f"CREATE (n:{ProposalStore.LOCK_LABEL} {{{props}}}) RETURN n",
            data,
        )
        return True

    @staticmethod
    async def release_lock(resource_id: str, proposal_id: str) -> bool:
        result = await Neo4jConnection.run_query(
            f"""
            MATCH (n:{ProposalStore.LOCK_LABEL})
            WHERE n.resource_id = $rid AND n.proposal_id = $pid AND n.released = false
            SET n.released = true
            RETURN n
            """,
            {"rid": resource_id, "pid": proposal_id},
        )
        return len(result) > 0

    @staticmethod
    async def is_locked(resource_id: str) -> bool:
        result = await Neo4jConnection.run_query(
            f"""
            MATCH (n:{ProposalStore.LOCK_LABEL})
            WHERE n.resource_id = $rid AND n.released = false
            RETURN n LIMIT 1
            """,
            {"rid": resource_id},
        )
        return len(result) > 0

    @staticmethod
    async def release_expired_locks() -> int:
        result = await Neo4jConnection.run_query(
            f"""
            MATCH (n:{ProposalStore.LOCK_LABEL})
            WHERE n.released = false AND n.expires_at < $now
            SET n.released = true
            RETURN count(n) AS cnt
            """,
            {"now": datetime.now(timezone.utc).isoformat()},
        )
        return result[0]["cnt"] if result else 0

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    @staticmethod
    async def ensure_schema() -> list[dict]:
        statements = [
            f"CREATE CONSTRAINT {ProposalStore.NODE_LABEL.lower()}_id IF NOT EXISTS FOR (n:{ProposalStore.NODE_LABEL}) REQUIRE n.id IS UNIQUE",
            f"CREATE CONSTRAINT {ProposalStore.CONFLICT_LABEL.lower()}_id IF NOT EXISTS FOR (n:{ProposalStore.CONFLICT_LABEL}) REQUIRE n.id IS UNIQUE",
            f"CREATE CONSTRAINT {ProposalStore.LOCK_LABEL.lower()}_id IF NOT EXISTS FOR (n:{ProposalStore.LOCK_LABEL}) REQUIRE n.id IS UNIQUE",
            f"CREATE INDEX {ProposalStore.NODE_LABEL.lower()}_target IF NOT EXISTS FOR (n:{ProposalStore.NODE_LABEL}) ON (n.target_id)",
            f"CREATE INDEX {ProposalStore.NODE_LABEL.lower()}_status IF NOT EXISTS FOR (n:{ProposalStore.NODE_LABEL}) ON (n.status)",
            f"CREATE INDEX {ProposalStore.NODE_LABEL.lower()}_agent IF NOT EXISTS FOR (n:{ProposalStore.NODE_LABEL}) ON (n.agent)",
        ]
        results = []
        for stmt in statements:
            try:
                await Neo4jConnection.run_query(stmt)
                results.append({"statement": stmt[:60], "status": "ok"})
            except Exception as e:
                results.append({"statement": stmt[:60], "status": "error", "error": str(e)})
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_to_proposal(node: dict) -> ProposalRecord:
        props = dict(node)
        tags = props.get("tags", [])
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        metadata = props.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        return ProposalRecord(
            id=props.get("id", ""),
            agent=props.get("agent", ""),
            agent_version=props.get("agent_version", "0.1.0"),
            title=props.get("title", ""),
            description=props.get("description", ""),
            action=props.get("action", ""),
            target_id=props.get("target_id", ""),
            target_type=props.get("target_type", ""),
            resource_type=ResourceType(props.get("resource_type", "Service")),
            rationale=props.get("rationale", ""),
            evidence_count=props.get("evidence_count", 0),
            evidence_summary=props.get("evidence_summary", ""),
            confidence=props.get("confidence", 0.0),
            risk_level=props.get("risk_level", "medium"),
            severity=props.get("severity", "medium"),
            pr_body=props.get("pr_body", ""),
            status=ProposalStatus(props.get("status", "pending")),
            resolution=props.get("resolution", ""),
            resolved_by=props.get("resolved_by", ""),
            resolver_notes=props.get("resolver_notes", ""),
            created_at=props.get("created_at", datetime.now(timezone.utc)),
            updated_at=props.get("updated_at", datetime.now(timezone.utc)),
            tags=tags,
            metadata=metadata,
        )

    @staticmethod
    def _node_to_conflict(node: dict) -> ConflictRecord:
        props = dict(node)
        return ConflictRecord(
            id=props.get("id", ""),
            proposal_a_id=props.get("proposal_a_id", ""),
            proposal_b_id=props.get("proposal_b_id", ""),
            conflict_type=ConflictType(props.get("conflict_type", "indirect")),
            severity=props.get("severity", "minor"),
            description=props.get("description", ""),
            affected_resource=props.get("affected_resource", ""),
            resource_type=ResourceType(props.get("resource_type", "Service")),
            score_a=props.get("score_a", 0.0),
            score_b=props.get("score_b", 0.0),
            resolution_strategy=ResolutionStrategy(props.get("resolution_strategy", "flag_for_review")),
            recommendation=props.get("recommendation", ""),
            resolved=props.get("resolved", False),
            created_at=props.get("created_at", datetime.now(timezone.utc)),
        )
