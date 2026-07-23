from __future__ import annotations
import json, logging
from datetime import datetime, timezone
from typing import Any
from app.gate.models import ApprovalPolicyConfig, ReviewDecision, ReviewRecord, ReviewerRole
from app.graph.connection import Neo4jConnection
logger = logging.getLogger("argus.gate.store")

class ReviewStore:
    REVIEW_LABEL = "Review"; PROPOSAL_LABEL = "Proposal"
    @staticmethod
    async def ensure_schema() -> list[dict]:
        results = []
        for stmt in [f"CREATE CONSTRAINT {ReviewStore.REVIEW_LABEL.lower()}_id IF NOT EXISTS FOR (n:{ReviewStore.REVIEW_LABEL}) REQUIRE n.id IS UNIQUE", f"CREATE INDEX {ReviewStore.REVIEW_LABEL.lower()}_proposal IF NOT EXISTS FOR (n:{ReviewStore.REVIEW_LABEL}) ON (n.proposal_id)", f"CREATE INDEX {ReviewStore.REVIEW_LABEL.lower()}_reviewer IF NOT EXISTS FOR (n:{ReviewStore.REVIEW_LABEL}) ON (n.reviewer)"]:
            try:
                await Neo4jConnection.run_query(stmt); results.append({"statement": stmt[:60], "status": "ok"})
            except Exception as e: results.append({"statement": stmt[:60], "status": "error", "error": str(e)})
        return results
    @staticmethod
    async def create_review(review: ReviewRecord) -> ReviewRecord:
        data = review.model_dump_for_cypher()
        keys = [k for k in data if data[k] is not None]
        props = ", ".join(f"{k}: ${k}" for k in keys)
        try: await Neo4jConnection.run_query(f"CREATE (n:{ReviewStore.REVIEW_LABEL} {{{props}}}) RETURN n", {k: data[k] for k in keys})
        except Exception as e:
            if "already exists" not in str(e): raise
        await Neo4jConnection.run_query(f"MATCH (r:{ReviewStore.REVIEW_LABEL} {{id: $rid}}), (p:{ReviewStore.PROPOSAL_LABEL} {{id: $pid}}) CREATE (r)-[:REVIEWS]->(p)", {"rid": review.id, "pid": review.proposal_id})
        return review
    @staticmethod
    async def get_review(review_id: str) -> ReviewRecord | None:
        r = await Neo4jConnection.run_query(f"MATCH (n:{ReviewStore.REVIEW_LABEL} {{id: $id}}) RETURN n", {"id": review_id})
        return None if not r else ReviewStore._node_to_review(r[0]["n"])
    @staticmethod
    async def list_reviews(proposal_id: str | None = None, reviewer: str | None = None, decision: ReviewDecision | None = None, limit: int = 50, offset: int = 0) -> list[ReviewRecord]:
        c, p = ["1=1"], {"limit": limit, "offset": offset}
        if proposal_id: c.append("n.proposal_id = $pid"); p["pid"] = proposal_id
        if reviewer: c.append("n.reviewer = $reviewer"); p["reviewer"] = reviewer
        if decision: c.append("n.decision = $decision"); p["decision"] = decision.value
        r = await Neo4jConnection.run_query(f"MATCH (n:{ReviewStore.REVIEW_LABEL}) WHERE {' AND '.join(c)} RETURN n ORDER BY n.created_at DESC SKIP $offset LIMIT $limit", p)
        return [ReviewStore._node_to_review(x["n"]) for x in r]
    @staticmethod
    async def get_reviews_for_proposal(proposal_id: str) -> list[ReviewRecord]:
        return await ReviewStore.list_reviews(proposal_id=proposal_id, limit=100)
    @staticmethod
    async def get_approval_count(proposal_id: str) -> int:
        r = await Neo4jConnection.run_query(f"MATCH (n:{ReviewStore.REVIEW_LABEL}) WHERE n.proposal_id = $pid AND n.decision = 'approved' RETURN count(n) AS cnt", {"pid": proposal_id})
        return r[0]["cnt"] if r else 0
    @staticmethod
    async def get_rejection_count(proposal_id: str) -> int:
        r = await Neo4jConnection.run_query(f"MATCH (n:{ReviewStore.REVIEW_LABEL}) WHERE n.proposal_id = $pid AND n.decision = 'rejected' RETURN count(n) AS cnt", {"pid": proposal_id})
        return r[0]["cnt"] if r else 0
    @staticmethod
    async def has_reviewer_acted(proposal_id: str, reviewer: str) -> bool:
        r = await Neo4jConnection.run_query(f"MATCH (n:{ReviewStore.REVIEW_LABEL}) WHERE n.proposal_id = $pid AND n.reviewer = $reviewer RETURN n LIMIT 1", {"pid": proposal_id, "reviewer": reviewer})
        return len(r) > 0
    @staticmethod
    async def get_policy_config() -> ApprovalPolicyConfig:
        r = await Neo4jConnection.run_query("MATCH (n:GateConfig {id: 'approval_policy'}) RETURN n.config AS config LIMIT 1")
        if not r: return ApprovalPolicyConfig.defaults()
        try:
            d = r[0]["config"]
            if isinstance(d, str): d = json.loads(d)
            if isinstance(d, dict): return ApprovalPolicyConfig(**d)
        except: pass
        return ApprovalPolicyConfig.defaults()
    @staticmethod
    async def save_policy_config(config: ApprovalPolicyConfig) -> None:
        await Neo4jConnection.run_query("MERGE (n:GateConfig {id: 'approval_policy'}) SET n.config = $config, n.updated_at = $now", {"config": config.model_dump_json(), "now": datetime.now(timezone.utc).isoformat()})
    @staticmethod
    def _node_to_review(node: dict) -> ReviewRecord:
        p = dict(node)
        return ReviewRecord(id=p.get("id",""), proposal_id=p.get("proposal_id",""), reviewer=p.get("reviewer",""), reviewer_role=ReviewerRole(p.get("reviewer_role","peer")), decision=ReviewDecision(p.get("decision","abstained")), comment=p.get("comment",""), evidence_checked=p.get("evidence_checked",False), created_at=p.get("created_at", datetime.now(timezone.utc)), updated_at=p.get("updated_at", datetime.now(timezone.utc)))
