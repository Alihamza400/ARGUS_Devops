from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any
from app.enforcer.models import EnforcementAction, PreCheckRequest, PreCheckResult
from app.enforcer.store import EnforcementStore
from app.graph.connection import Neo4jConnection
logger = logging.getLogger("argus.enforcer.precheck")

class PreCheckEngine:
    @staticmethod
    async def run(request: PreCheckRequest) -> PreCheckResult:
        config = await EnforcementStore.get_config()
        checks: dict[str, bool] = {}
        failures: list[str] = []
        warnings: list[str] = []

        if request.dry_run:
            warnings.append("Dry-run mode: no changes will be executed")

        cw = config.change_window
        if cw.enabled:
            now = datetime.now(timezone.utc)
            hour = now.hour
            day = now.weekday()
            in_window = cw.start_hour <= hour < cw.end_hour
            in_day = day in cw.allowed_days
            checks["change_window"] = in_window and in_day
            if not in_window:
                failures.append(f"Outside change window ({cw.start_hour}:00-{cw.end_hour}:00 UTC, hour={hour})")
            if not in_day:
                failures.append(f"Day {day} not in allowed days {cw.allowed_days}")
        else:
            checks["change_window"] = True

        if request.target_id:
            count = await PreCheckEngine._count_related_resources(request.target_id, request.target_type)
            radius = count.get("total", 0)
            max_radius = 50
            checks["blast_radius"] = radius <= max_radius
            if radius > max_radius:
                failures.append(f"Blast radius {radius} exceeds max {max_radius} resources")
        else:
            checks["blast_radius"] = True

        recent = await EnforcementStore.list(limit=config.max_concurrent_enforcements)
        in_progress = [e for e in recent if e.status.value == "in_progress"]
        checks["rate_limit"] = len(in_progress) < config.max_concurrent_enforcements
        if len(in_progress) >= config.max_concurrent_enforcements:
            failures.append(f"Rate limit: {len(in_progress)} enforcements in progress, max {config.max_concurrent_enforcements}")

        action_str = request.action.value if hasattr(request.action, 'value') else str(request.action)
        blocked = config.blocked_actions or []
        if action_str in blocked:
            checks["policy_compliance"] = False
            failures.append(f"Action '{action_str}' is in blocked actions list")
        else:
            checks["policy_compliance"] = True

        maintenance = await PreCheckEngine._is_maintenance_mode()
        checks["maintenance_mode"] = not maintenance
        if maintenance:
            failures.append("System is in maintenance mode, enforcement blocked")

        passed = len(failures) == 0
        details = "; ".join(failures + warnings) if failures or warnings else "All prechecks passed"
        return PreCheckResult(passed=passed, checks=checks, failures=failures, warnings=warnings, details=details)

    @staticmethod
    async def _count_related_resources(target_id: str, target_type: str) -> dict[str, int]:
        try:
            r = await Neo4jConnection.run_query(
                f"MATCH (n:{target_type} {{id: $tid}})-[:IN|BELONGS_TO|DEPLOYS|DEPLOYED_FROM|RUNS_ON]-(related) RETURN count(DISTINCT related) AS cnt",
                {"tid": target_id},
            )
            direct = r[0]["cnt"] if r else 0
            r2 = await Neo4jConnection.run_query(
                f"MATCH (n:{target_type} {{id: $tid}})<-[:IN|BELONGS_TO|DEPLOYS|DEPLOYED_FROM|RUNS_ON]-(related) RETURN count(DISTINCT related) AS cnt",
                {"tid": target_id},
            )
            inbound = r2[0]["cnt"] if r2 else 0
            return {"direct": direct, "inbound": inbound, "total": direct + inbound}
        except Exception:
            return {"direct": 0, "inbound": 0, "total": 0}

    @staticmethod
    async def _is_maintenance_mode() -> bool:
        try:
            r = await Neo4jConnection.run_query("MATCH (n:GateConfig {id: 'maintenance'}) RETURN n.active AS active LIMIT 1")
            if r and r[0].get("active"):
                return True
        except Exception:
            pass
        return False
