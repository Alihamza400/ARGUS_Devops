from __future__ import annotations
import pytest, pytest_asyncio
from httpx import ASGITransport, AsyncClient
from app.coordinator.coordinator import ConflictCoordinator
from app.coordinator.models import ProposalRecord, ProposalStatus, ResourceType
from app.coordinator.store import ProposalStore
from app.enforcer.enforcer import EnforcerCoordinator
from app.enforcer.executor import ExecutorEngine
from app.enforcer.models import (
    EnforceRequest, EnforcementAction, EnforcementRecord, EnforcementStatus, EnforcerConfig, PreCheckRequest,
)
from app.enforcer.precheck import PreCheckEngine
from app.enforcer.store import EnforcementStore
from app.enforcer.verifier import VerifierEngine
from app.graph.connection import Neo4jConnection
from app.main import app

@pytest_asyncio.fixture(autouse=True)
async def ensure_neo4j():
    for attempt in range(3):
        import asyncio; connected = await Neo4jConnection.verify_connectivity()
        if connected: break
        await asyncio.sleep(1)
    else: pytest.skip("Neo4j not available")
    try:
        await ConflictCoordinator.ensure_schema(); await EnforcementStore.ensure_schema()
    except: pass
    yield
    await Neo4jConnection.run_query("MATCH (n) DETACH DELETE n")

async def _seed_approved_proposal(**kw) -> ProposalRecord:
    d = dict(id="enf-test-prop", agent="test-agent", agent_version="1.0", title="Test enforcement", description="Test", action="rollback", target_id="svc-enf-test", target_type="Service", resource_type=ResourceType.SERVICE, rationale="Testing enforcement", evidence_count=5, confidence=0.85, risk_level="medium", severity="high", status=ProposalStatus.APPROVED)
    d.update(kw)
    p = ProposalRecord(**d); await ProposalStore.create_proposal(p); return p

# ---------------------------------------------------------------------------
# Store Tests
# ---------------------------------------------------------------------------
class TestEnforcementStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        e = EnforcementRecord(proposal_id="p1", proposal_action="rollback", proposal_target="svc-1")
        await EnforcementStore.create(e)
        got = await EnforcementStore.get(e.id)
        assert got is not None and got.id == e.id and got.proposal_id == "p1"

    @pytest.mark.asyncio
    async def test_update_status(self):
        e = EnforcementRecord(proposal_id="p2"); await EnforcementStore.create(e)
        ok = await EnforcementStore.update_status(e.id, EnforcementStatus.SUCCESS, result="ok")
        assert ok is True
        got = await EnforcementStore.get(e.id)
        assert got.status == EnforcementStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_list_by_proposal(self):
        e1 = EnforcementRecord(proposal_id="lp1"); e2 = EnforcementRecord(proposal_id="lp1"); e3 = EnforcementRecord(proposal_id="lp2")
        await EnforcementStore.create(e1); await EnforcementStore.create(e2); await EnforcementStore.create(e3)
        items = await EnforcementStore.list(proposal_id="lp1")
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_config_save_load(self):
        cfg = EnforcerConfig(dry_run_default=False, max_concurrent_enforcements=10)
        await EnforcementStore.save_config(cfg)
        loaded = await EnforcementStore.get_config()
        assert loaded.dry_run_default is False
        assert loaded.max_concurrent_enforcements == 10

# ---------------------------------------------------------------------------
# PreCheck Tests
# ---------------------------------------------------------------------------
class TestPreCheckEngine:
    @pytest.mark.asyncio
    async def test_precheck_passes_by_default(self):
        req = PreCheckRequest(proposal_id="pc1", action=EnforcementAction.ROLLBACK, target_id="svc-pc")
        result = await PreCheckEngine.run(req)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_precheck_dry_run_warning(self):
        req = PreCheckRequest(proposal_id="pc2", action=EnforcementAction.ROLLBACK, dry_run=True)
        result = await PreCheckEngine.run(req)
        assert len(result.warnings) >= 1

    @pytest.mark.asyncio
    async def test_precheck_rate_limit(self):
        cfg = await EnforcementStore.get_config(); cfg.max_concurrent_enforcements = 0
        await EnforcementStore.save_config(cfg)
        req = PreCheckRequest(proposal_id="pc3", action=EnforcementAction.ROLLBACK)
        result = await PreCheckEngine.run(req)
        assert result.passed is False
        assert any("rate" in f.lower() for f in result.failures)
        cfg.max_concurrent_enforcements = 3; await EnforcementStore.save_config(cfg)

    @pytest.mark.asyncio
    async def test_precheck_blocked_action(self):
        cfg = await EnforcementStore.get_config(); cfg.blocked_actions = ["rollback"]
        await EnforcementStore.save_config(cfg)
        req = PreCheckRequest(proposal_id="pc4", action=EnforcementAction.ROLLBACK)
        result = await PreCheckEngine.run(req)
        assert result.passed is False
        assert any("blocked" in f.lower() for f in result.failures)
        cfg.blocked_actions = []; await EnforcementStore.save_config(cfg)

    @pytest.mark.asyncio
    async def test_precheck_change_window(self):
        cfg = await EnforcementStore.get_config()
        cfg.change_window.enabled = True
        cfg.change_window.start_hour = 23; cfg.change_window.end_hour = 0
        await EnforcementStore.save_config(cfg)
        req = PreCheckRequest(proposal_id="pc5", action=EnforcementAction.ROLLBACK)
        result = await PreCheckEngine.run(req)
        f = cfg.change_window.enabled = False; await EnforcementStore.save_config(cfg)
        assert result.passed is False or (not result.passed)

    @pytest.mark.asyncio
    async def test_precheck_blast_radius(self):
        req = PreCheckRequest(proposal_id="pc6", action=EnforcementAction.ROLLBACK, target_id="nonexistent")
        result = await PreCheckEngine.run(req)
        assert result.passed is True

# ---------------------------------------------------------------------------
# Executor Tests
# ---------------------------------------------------------------------------
class TestExecutorEngine:
    @pytest.mark.asyncio
    async def test_dry_run(self):
        e = EnforcementRecord(proposal_id="ex1", proposal_action="rollback", proposal_target="svc-ex1", dry_run=True)
        result = await ExecutorEngine.execute(e, dry_run=True)
        assert result.status == EnforcementStatus.DRY_RUN
        assert "Dry-run" in (result.execution_result or "")

    @pytest.mark.asyncio
    async def test_execute_rollback(self):
        e = EnforcementRecord(proposal_id="ex2", proposal_action="rollback", proposal_target="svc-ex2")
        await EnforcementStore.create(e)
        result = await ExecutorEngine.execute(e)
        assert result.status == EnforcementStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_execute_submit_pr(self):
        e = EnforcementRecord(proposal_id="ex3", proposal_action="submit_pr", proposal_target="svc-ex3")
        await EnforcementStore.create(e)
        result = await ExecutorEngine.execute(e)
        assert result.status == EnforcementStatus.SUCCESS
        assert result.pr_url != ""

    @pytest.mark.asyncio
    async def test_execute_failure_handling(self):
        e = EnforcementRecord(proposal_id="ex4", proposal_action="unknown_action", proposal_target="svc-ex4")
        await EnforcementStore.create(e)
        result = await ExecutorEngine.execute(e)
        assert result.status in (EnforcementStatus.SUCCESS, EnforcementStatus.FAILED)

# ---------------------------------------------------------------------------
# Verifier Tests
# ---------------------------------------------------------------------------
class TestVerifierEngine:
    @pytest.mark.asyncio
    async def test_verify_successful_enforcement(self):
        e = EnforcementRecord(proposal_id="v1", proposal_action="rollback", proposal_target="svc-v1", status=EnforcementStatus.SUCCESS, execution_result="ok")
        result = await VerifierEngine.verify(e)
        assert result.verification_status == "passed" or result.verification_status == "skipped"

    @pytest.mark.asyncio
    async def test_verify_skipped_for_failed_enforcement(self):
        e = EnforcementRecord(proposal_id="v2", status=EnforcementStatus.FAILED)
        result = await VerifierEngine.verify(e)
        assert result.verification_status == "skipped"

    @pytest.mark.asyncio
    async def test_verify_dry_run_skipped(self):
        e = EnforcementRecord(proposal_id="v3", dry_run=True, status=EnforcementStatus.DRY_RUN)
        result = await VerifierEngine.verify(e)
        assert result.verification_status == "skipped"

# ---------------------------------------------------------------------------
# EnforcerCoordinator Tests
# ---------------------------------------------------------------------------
class TestEnforcerCoordinator:
    @pytest.mark.asyncio
    async def test_enforce_approved_proposal(self):
        await _seed_approved_proposal(id="ec1")
        req = EnforceRequest(proposal_id="ec1", executed_by="tester")
        resp = await EnforcerCoordinator.enforce(req)
        assert resp.success is True
        assert resp.enforcement.status in (EnforcementStatus.SUCCESS, EnforcementStatus.DRY_RUN)

    @pytest.mark.asyncio
    async def test_enforce_not_found(self):
        req = EnforceRequest(proposal_id="nonexistent")
        resp = await EnforcerCoordinator.enforce(req)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_enforce_not_approved(self):
        await _seed_approved_proposal(id="ec2", status=ProposalStatus.PENDING)
        req = EnforceRequest(proposal_id="ec2")
        resp = await EnforcerCoordinator.enforce(req)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_enforce_dry_run(self):
        await _seed_approved_proposal(id="ec3")
        req = EnforceRequest(proposal_id="ec3", dry_run=True)
        resp = await EnforcerCoordinator.enforce(req)
        assert resp.enforcement.status == EnforcementStatus.DRY_RUN

    @pytest.mark.asyncio
    async def test_enforce_skip_precheck(self):
        await _seed_approved_proposal(id="ec4")
        req = EnforceRequest(proposal_id="ec4", skip_precheck=True)
        resp = await EnforcerCoordinator.enforce(req)
        assert resp.enforcement.precheck_passed is False

    @pytest.mark.asyncio
    async def test_list_enforcements(self):
        await _seed_approved_proposal(id="ec5")
        req = EnforceRequest(proposal_id="ec5"); await EnforcerCoordinator.enforce(req)
        items = await EnforcerCoordinator.list_enforcements(proposal_id="ec5")
        assert len(items) >= 1

    @pytest.mark.asyncio
    async def test_get_config(self):
        cfg = await EnforcerCoordinator.get_config()
        assert cfg.dry_run_default is True

    @pytest.mark.asyncio
    async def test_update_config(self):
        cfg = EnforcerConfig(dry_run_default=False)
        await EnforcerCoordinator.update_config(cfg)
        loaded = await EnforcerCoordinator.get_config()
        assert loaded.dry_run_default is False

    @pytest.mark.asyncio
    async def test_health(self):
        h = await EnforcerCoordinator.health()
        assert "status" in h

# ---------------------------------------------------------------------------
# API Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_api_execute(client: AsyncClient, auth_headers: dict):
    await _seed_approved_proposal(id="api-enf-1")
    resp = await client.post("/enforce/execute", json={"proposal_id": "api-enf-1", "executed_by": "tester"}, headers=auth_headers)
    assert resp.status_code == 200
    d = resp.json(); assert d["success"] is True

@pytest.mark.asyncio
async def test_api_execute_missing_id(client: AsyncClient, auth_headers: dict):
    resp = await client.post("/enforce/execute", json={}, headers=auth_headers)
    assert resp.status_code == 400

@pytest.mark.asyncio
async def test_api_get_enforcement(client: AsyncClient, auth_headers: dict):
    await _seed_approved_proposal(id="api-enf-2")
    rr = await EnforcerCoordinator.enforce(EnforceRequest(proposal_id="api-enf-2"))
    eid = rr.enforcement.id
    resp = await client.get(f"/enforce/enforcements/{eid}", headers=auth_headers)
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_api_list_enforcements(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/enforce/enforcements", headers=auth_headers)
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_api_config(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/enforce/config", headers=auth_headers)
    assert resp.status_code == 200
    d = resp.json(); assert "dry_run_default" in d

@pytest.mark.asyncio
async def test_api_update_config(client: AsyncClient, auth_headers: dict):
    resp = await client.put("/enforce/config", json={"dry_run_default": False}, headers=auth_headers)
    assert resp.status_code == 200
    cfg = await EnforcerCoordinator.get_config()
    assert cfg.dry_run_default is False; cfg.dry_run_default = True; await EnforcerCoordinator.update_config(cfg)

@pytest.mark.asyncio
async def test_api_health(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/enforce/health", headers=auth_headers)
    assert resp.status_code == 200

# ---------------------------------------------------------------------------
# End-to-End: Full Pipeline
# ---------------------------------------------------------------------------
class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_full_approve_enforce_verify(self):
        p = await _seed_approved_proposal(id="e2e-1")
        req = EnforceRequest(proposal_id="e2e-1", executed_by="e2e-test")
        resp = await EnforcerCoordinator.enforce(req)
        assert resp.success is True
        enf = await EnforcementStore.get(resp.enforcement.id)
        assert enf is not None
        assert enf.status in (EnforcementStatus.SUCCESS, EnforcementStatus.DRY_RUN)

    @pytest.mark.asyncio
    async def test_enforcement_creates_edge(self):
        await _seed_approved_proposal(id="e2e-2")
        req = EnforceRequest(proposal_id="e2e-2")
        resp = await EnforcerCoordinator.enforce(req)
        r = await Neo4jConnection.run_query("MATCH (e:Enforcement {id: $eid})-[rel:ENFORCES]->(p:Proposal {id: $pid}) RETURN rel", {"eid": resp.enforcement.id, "pid": "e2e-2"})
        assert len(r) >= 1

    @pytest.mark.asyncio
    async def test_enforce_with_precheck(self):
        await _seed_approved_proposal(id="e2e-3")
        req = EnforceRequest(proposal_id="e2e-3", skip_precheck=False)
        resp = await EnforcerCoordinator.enforce(req)
        assert resp.precheck is not None

    @pytest.mark.asyncio
    async def test_enforce_twice(self):
        await _seed_approved_proposal(id="e2e-4")
        r1 = await EnforcerCoordinator.enforce(EnforceRequest(proposal_id="e2e-4"))
        r2 = await EnforcerCoordinator.enforce(EnforceRequest(proposal_id="e2e-4"))
        assert r1.success is True
        items = await EnforcementStore.list(proposal_id="e2e-4")
        assert len(items) == 2
