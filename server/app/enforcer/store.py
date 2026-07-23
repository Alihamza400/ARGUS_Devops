from __future__ import annotations
import json, logging
from datetime import datetime, timezone
from typing import Any
from app.enforcer.models import EnforcementAction, EnforcementRecord, EnforcementStatus, EnforcerConfig, ChangeWindow
from app.graph.connection import Neo4jConnection
logger = logging.getLogger("argus.enforcer.store")

class EnforcementStore:
    LABEL = "Enforcement"
    PROPOSAL_LABEL = "Proposal"
    CONFIG_LABEL = "EnforcerConfig"

    @staticmethod
    async def ensure_schema() -> list[dict]:
        results = []
        for stmt in [
            f"CREATE CONSTRAINT {EnforcementStore.LABEL.lower()}_id IF NOT EXISTS FOR (n:{EnforcementStore.LABEL}) REQUIRE n.id IS UNIQUE",
            f"CREATE INDEX {EnforcementStore.LABEL.lower()}_proposal IF NOT EXISTS FOR (n:{EnforcementStore.LABEL}) ON (n.proposal_id)",
            f"CREATE INDEX {EnforcementStore.LABEL.lower()}_status IF NOT EXISTS FOR (n:{EnforcementStore.LABEL}) ON (n.status)",
        ]:
            try:
                await Neo4jConnection.run_query(stmt); results.append({"statement": stmt[:60], "status": "ok"})
            except Exception as e: results.append({"statement": stmt[:60], "status": "error", "error": str(e)})
        return results

    @staticmethod
    async def create(enforcement: EnforcementRecord) -> EnforcementRecord:
        data = enforcement.model_dump_for_cypher()
        keys = [k for k in data if data[k] is not None and data[k] != ""]
        props = ", ".join(f"{k}: ${k}" for k in keys)
        try:
            await Neo4jConnection.run_query(f"CREATE (n:{EnforcementStore.LABEL} {{{props}}}) RETURN n", {k: data[k] for k in keys})
        except Exception as e:
            if "already exists" not in str(e): raise
        await Neo4jConnection.run_query(
            f"MATCH (e:{EnforcementStore.LABEL} {{id: $eid}}), (p:{EnforcementStore.PROPOSAL_LABEL} {{id: $pid}}) CREATE (e)-[:ENFORCES]->(p)",
            {"eid": enforcement.id, "pid": enforcement.proposal_id},
        )
        return enforcement

    @staticmethod
    async def get(enforcement_id: str) -> EnforcementRecord | None:
        r = await Neo4jConnection.run_query(f"MATCH (n:{EnforcementStore.LABEL} {{id: $id}}) RETURN n", {"id": enforcement_id})
        if not r: return None
        return EnforcementStore._node_to_record(r[0]["n"])

    @staticmethod
    async def update_status(enforcement_id: str, status: EnforcementStatus, result: str = "", error: str = "") -> bool:
        r = await Neo4jConnection.run_query(
            f"MATCH (n:{EnforcementStore.LABEL} {{id: $id}}) SET n.status = $s, n.execution_result = $res, n.error_message = $err, n.completed_at = $now RETURN n",
            {"id": enforcement_id, "s": status.value, "res": result, "err": error, "now": datetime.now(timezone.utc).isoformat()},
        )
        return len(r) > 0

    @staticmethod
    async def list(proposal_id: str | None = None, status: EnforcementStatus | None = None, limit: int = 50, offset: int = 0) -> list[EnforcementRecord]:
        c, p = ["1=1"], {"limit": limit, "offset": offset}
        if proposal_id: c.append("n.proposal_id = $pid"); p["pid"] = proposal_id
        if status: c.append("n.status = $s"); p["s"] = status.value
        r = await Neo4jConnection.run_query(f"MATCH (n:{EnforcementStore.LABEL}) WHERE {' AND '.join(c)} RETURN n ORDER BY n.started_at DESC SKIP $offset LIMIT $limit", p)
        return [EnforcementStore._node_to_record(x["n"]) for x in r]

    @staticmethod
    async def get_config() -> EnforcerConfig:
        r = await Neo4jConnection.run_query(f"MATCH (n:{EnforcementStore.CONFIG_LABEL} {{id: 'enforcer'}}) RETURN n.config AS config LIMIT 1")
        if not r: return EnforcerConfig()
        try:
            d = r[0]["config"]
            if isinstance(d, str): d = json.loads(d)
            if isinstance(d, dict): return EnforcerConfig(**d)
        except: pass
        return EnforcerConfig()

    @staticmethod
    async def save_config(config: EnforcerConfig) -> None:
        await Neo4jConnection.run_query(f"MERGE (n:{EnforcementStore.CONFIG_LABEL} {{id: 'enforcer'}}) SET n.config = $config, n.updated_at = $now", {"config": config.model_dump_json(), "now": datetime.now(timezone.utc).isoformat()})

    @staticmethod
    def _node_to_record(node: dict) -> EnforcementRecord:
        p = dict(node)
        meta = p.get("metadata", {})
        if isinstance(meta, str):
            try: meta = json.loads(meta)
            except: meta = {}
        return EnforcementRecord(
            id=p.get("id",""), proposal_id=p.get("proposal_id",""), proposal_action=p.get("proposal_action",""),
            proposal_target=p.get("proposal_target",""), proposal_title=p.get("proposal_title",""),
            action=EnforcementAction(p.get("action","custom")), status=EnforcementStatus(p.get("status","pending")),
            executed_by=p.get("executed_by",""), dry_run=p.get("dry_run",False),
            precheck_passed=p.get("precheck_passed",False), precheck_details=p.get("precheck_details",""),
            execution_result=p.get("execution_result",""), verification_result=p.get("verification_result",""),
            verification_status=p.get("verification_status",""), error_message=p.get("error_message",""),
            started_at=p.get("started_at"), completed_at=p.get("completed_at"),
            pr_url=p.get("pr_url",""), commit_sha=p.get("commit_sha",""), duration_seconds=p.get("duration_seconds",0.0),
            metadata=meta,
        )
