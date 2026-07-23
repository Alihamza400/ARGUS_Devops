from __future__ import annotations

import pytest
import pytest_asyncio

from app.coordinator.analyzer import ConflictAnalyzer
from app.coordinator.coordinator import ConflictCoordinator
from app.coordinator.detector import ConflictDetector
from app.coordinator.models import (
    ConflictRecord,
    ConflictResolutionRequest,
    ConflictSeverity,
    ConflictType,
    ProposalRecord,
    ProposalStatus,
    ResolutionStrategy,
    ResourceType,
    SubmitProposalRequest,
)
from app.coordinator.resolver import ConflictResolver
from app.coordinator.store import ProposalStore
from app.graph.connection import Neo4jConnection
from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def ensure_neo4j():
    for attempt in range(3):
        import asyncio
        connected = await Neo4jConnection.verify_connectivity()
        if connected:
            break
        await asyncio.sleep(1)
    else:
        pytest.skip("Neo4j not available")
    try:
        await ConflictCoordinator.ensure_schema()
    except Exception:
        pass
    yield
    await Neo4jConnection.run_query("MATCH (n) DETACH DELETE n")


def make_proposal(**kwargs) -> ProposalRecord:
    defaults = dict(
        id="prop-test-1",
        agent="test-agent",
        agent_version="1.0.0",
        title="Test proposal",
        description="A test proposal",
        action="rollback",
        target_id="svc-api-gateway",
        target_type="Service",
        resource_type=ResourceType.SERVICE,
        rationale="Testing",
        evidence_count=5,
        evidence_summary="5 evidence items",
        confidence=0.85,
        risk_level="medium",
        severity="critical",
        status=ProposalStatus.PENDING,
        tags=["test"],
    )
    defaults.update(kwargs)
    return ProposalRecord(**defaults)


async def _seed_proposal(**kwargs) -> ProposalRecord:
    p = make_proposal(**kwargs)
    await ProposalStore.create_proposal(p)
    return p


# ---------------------------------------------------------------------------
# Store Tests
# ---------------------------------------------------------------------------

class TestProposalStore:
    @pytest.mark.asyncio
    async def test_create_and_get_proposal(self):
        p = make_proposal(id="store-test-1")
        await ProposalStore.create_proposal(p)

        fetched = await ProposalStore.get_proposal("store-test-1")
        assert fetched is not None
        assert fetched.id == "store-test-1"
        assert fetched.action == "rollback"
        assert fetched.target_id == "svc-api-gateway"
        assert fetched.status == ProposalStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_nonexistent_proposal(self):
        fetched = await ProposalStore.get_proposal("nonexistent")
        assert fetched is None

    @pytest.mark.asyncio
    async def test_update_proposal_status(self):
        await _seed_proposal(id="store-test-2")
        updated = await ProposalStore.update_proposal_status(
            "store-test-2",
            ProposalStatus.APPROVED,
            resolution="Test resolution",
            resolved_by="tester",
        )
        assert updated is True

        fetched = await ProposalStore.get_proposal("store-test-2")
        assert fetched.status == ProposalStatus.APPROVED
        assert fetched.resolution == "Test resolution"

    @pytest.mark.asyncio
    async def test_list_proposals_by_resource(self):
        await _seed_proposal(id="list-1", target_id="svc-1")
        await _seed_proposal(id="list-2", target_id="svc-1")
        await _seed_proposal(id="list-3", target_id="svc-2")

        results = await ProposalStore.list_proposals(resource_id="svc-1")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_proposals_by_status(self):
        await _seed_proposal(id="status-1", status=ProposalStatus.PENDING)
        await _seed_proposal(id="status-2", status=ProposalStatus.APPROVED)
        await _seed_proposal(id="status-3", status=ProposalStatus.BLOCKED)

        pending = await ProposalStore.list_proposals(status=ProposalStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].id == "status-1"

        blocked = await ProposalStore.list_proposals(status=ProposalStatus.BLOCKED)
        assert len(blocked) == 1

    @pytest.mark.asyncio
    async def test_list_proposals_by_agent(self):
        await _seed_proposal(id="agent-1", agent="agent-a")
        await _seed_proposal(id="agent-2", agent="agent-b")

        results = await ProposalStore.list_proposals(agent="agent-a")
        assert len(results) == 1
        assert results[0].id == "agent-1"

    @pytest.mark.asyncio
    async def test_create_conflict(self):
        await _seed_proposal(id="conf-prop-a")
        await _seed_proposal(id="conf-prop-b")

        conflict = ConflictRecord(
            proposal_a_id="conf-prop-a",
            proposal_b_id="conf-prop-b",
            conflict_type=ConflictType.DIRECT,
            severity=ConflictSeverity.BLOCKING,
            description="Test conflict",
            affected_resource="svc-1",
            resource_type=ResourceType.SERVICE,
        )
        created = await ProposalStore.create_conflict(conflict)
        assert created.id == conflict.id

    @pytest.mark.asyncio
    async def test_get_conflicts_for_proposal(self):
        for pid in ["c-prop-a", "c-prop-b", "c-prop-c"]:
            await _seed_proposal(id=pid)

        for left, right in [("c-prop-a", "c-prop-b"), ("c-prop-a", "c-prop-c")]:
            c = ConflictRecord(
                proposal_a_id=left,
                proposal_b_id=right,
                conflict_type=ConflictType.DIRECT,
                severity=ConflictSeverity.BLOCKING,
                description="test",
                affected_resource="svc-1",
                resource_type=ResourceType.SERVICE,
            )
            await ProposalStore.create_conflict(c)

        conflicts = await ProposalStore.get_conflicts_for_proposal("c-prop-a")
        all_conflicts = await ProposalStore.list_conflicts(resolved=None)
        assert len(all_conflicts) >= 2, (
            f"Expected at least 2 total conflicts, got {len(all_conflicts)}"
        )
        assert len(conflicts) >= 1

    @pytest.mark.asyncio
    async def test_list_conflicts(self):
        await _seed_proposal(id="cl-prop-a")
        await _seed_proposal(id="cl-prop-b")
        c = ConflictRecord(
            proposal_a_id="cl-prop-a",
            proposal_b_id="cl-prop-b",
            affected_resource="svc-1",
            resource_type=ResourceType.SERVICE,
        )
        await ProposalStore.create_conflict(c)

        all_c = await ProposalStore.list_conflicts()
        assert len(all_c) >= 1

    @pytest.mark.asyncio
    async def test_resource_lock(self):
        from app.coordinator.models import ResourceLock

        lock1 = ResourceLock(
            id="lock-1",
            resource_id="svc-locked",
            resource_type=ResourceType.SERVICE,
            proposal_id="prop-lock",
            agent="agent",
        )
        lock_acquired = await ProposalStore.acquire_lock(lock1)
        assert lock_acquired is True

        lock2 = ResourceLock(
            id="lock-2",
            resource_id="svc-locked",
            resource_type=ResourceType.SERVICE,
            proposal_id="prop-lock-2",
            agent="agent-2",
        )
        second_lock = await ProposalStore.acquire_lock(lock2)
        assert second_lock is False

        released = await ProposalStore.release_lock("svc-locked", "prop-lock")
        assert released is True

        lock3 = ResourceLock(
            id="lock-3",
            resource_id="svc-locked",
            resource_type=ResourceType.SERVICE,
            proposal_id="prop-lock-3",
            agent="agent-3",
        )
        third_lock = await ProposalStore.acquire_lock(lock3)
        assert third_lock is True


# ---------------------------------------------------------------------------
# Detector Tests
# ---------------------------------------------------------------------------

class TestConflictDetector:
    @pytest.mark.asyncio
    async def test_direct_conflict_same_action(self):
        await _seed_proposal(id="det-a", action="rollback", target_id="svc-1")
        proposal_b = make_proposal(id="det-b", action="rollback", target_id="svc-1")

        conflicts = await ConflictDetector.detect_for_proposal(proposal_b)
        direct = [c for c in conflicts if c.conflict_type == ConflictType.DIRECT]
        assert len(direct) >= 1
        assert direct[0].severity == ConflictSeverity.BLOCKING

    @pytest.mark.asyncio
    async def test_direct_conflict_conflicting_actions(self):
        await _seed_proposal(id="det-c", action="rollback", target_id="svc-1")
        proposal_b = make_proposal(id="det-d", action="scale", target_id="svc-1")

        conflicts = await ConflictDetector.detect_for_proposal(proposal_b)
        direct = [c for c in conflicts if c.conflict_type == ConflictType.DIRECT]
        assert len(direct) >= 1

    @pytest.mark.asyncio
    async def test_indirect_conflict_same_resource(self):
        await _seed_proposal(id="det-e", action="rollback", target_id="svc-1")
        # "manual_intervention" and "rollback" are not conflicting actions
        proposal_b = make_proposal(id="det-f", action="manual_intervention", target_id="svc-1")

        conflicts = await ConflictDetector.detect_for_proposal(proposal_b)
        indirect = [c for c in conflicts if c.conflict_type == ConflictType.INDIRECT]
        assert len(indirect) >= 1

    @pytest.mark.asyncio
    async def test_complementary_different_resources(self):
        await _seed_proposal(id="det-g", action="rollback", target_id="svc-1")
        proposal_b = make_proposal(id="det-h", action="scale", target_id="svc-2")

        conflicts = await ConflictDetector.detect_for_proposal(proposal_b)
        complementary = [c for c in conflicts if c.conflict_type == ConflictType.COMPLEMENTARY]
        assert len(complementary) >= 1

    @pytest.mark.asyncio
    async def test_actions_conflict(self):
        assert ConflictDetector._actions_conflict("rollback", "rollback") is True
        assert ConflictDetector._actions_conflict("rollback", "scale") is True
        assert ConflictDetector._actions_conflict("scale", "rollback") is True
        assert ConflictDetector._actions_conflict("scale", "scale") is True
        assert ConflictDetector._actions_conflict("config_change", "rollback") is True
        assert ConflictDetector._actions_conflict("rollback", "pr_approval") is False

    @pytest.mark.asyncio
    async def test_complementary_conflict(self):
        await _seed_proposal(id="det-i", action="rollback", target_id="svc-1", resource_type=ResourceType.SERVICE)
        proposal_b = make_proposal(id="det-j", action="rollback", target_id="svc-2", resource_type=ResourceType.SERVICE)

        conflicts = await ConflictDetector.detect_for_proposal(proposal_b)
        complementary = [c for c in conflicts if c.conflict_type == ConflictType.COMPLEMENTARY]
        assert len(complementary) >= 1
        assert complementary[0].resolution_strategy == ResolutionStrategy.AUTO_APPROVE


# ---------------------------------------------------------------------------
# Analyzer Tests
# ---------------------------------------------------------------------------

class TestConflictAnalyzer:
    def test_score_high_confidence_proposal(self):
        p = make_proposal(confidence=0.95, evidence_count=10, risk_level="low", severity="critical")
        score = ConflictAnalyzer.score_proposal(p)
        assert score >= 0.7
        assert score <= 1.0

    def test_score_low_confidence_proposal(self):
        p = make_proposal(confidence=0.1, evidence_count=1, risk_level="high", severity="info")
        score = ConflictAnalyzer.score_proposal(p)
        assert score <= 0.5

    def test_rank_proposals(self):
        high = make_proposal(id="high", confidence=0.9, evidence_count=10, risk_level="low", severity="critical")
        low = make_proposal(id="low", confidence=0.2, evidence_count=1, risk_level="high", severity="info")

        ranked = ConflictAnalyzer.rank_proposals([low, high])
        assert ranked[0][0].id == "high"
        assert ranked[1][0].id == "low"
        assert ranked[0][1] > ranked[1][1]

    def test_assess_conflict_severity_direct(self):
        conflict = ConflictRecord(
            proposal_a_id="a",
            proposal_b_id="b",
            conflict_type=ConflictType.DIRECT,
            score_a=0.9,
            score_b=0.4,
            affected_resource="svc-1",
            resource_type=ResourceType.SERVICE,
        )
        severity = ConflictAnalyzer.assess_conflict_severity(conflict)
        assert severity == ConflictSeverity.MAJOR

    def test_assess_conflict_severity_direct_close_scores(self):
        conflict = ConflictRecord(
            proposal_a_id="a",
            proposal_b_id="b",
            conflict_type=ConflictType.DIRECT,
            score_a=0.6,
            score_b=0.55,
            affected_resource="svc-1",
            resource_type=ResourceType.SERVICE,
        )
        severity = ConflictAnalyzer.assess_conflict_severity(conflict)
        assert severity == ConflictSeverity.BLOCKING

    def test_recommend_resolution_direct_high_score(self):
        conflict = ConflictRecord(
            proposal_a_id="a",
            proposal_b_id="b",
            conflict_type=ConflictType.DIRECT,
            affected_resource="svc-1",
            resource_type=ResourceType.SERVICE,
        )
        high = make_proposal(id="high", confidence=0.95, evidence_count=10)
        low = make_proposal(id="low", confidence=0.3, evidence_count=1)
        ranked = [(high, 0.9), (low, 0.3)]

        strategy = ConflictAnalyzer.recommend_resolution(conflict, ranked)
        assert strategy == ResolutionStrategy.RANK_AND_PICK

    def test_recommend_resolution_direct_low_score(self):
        conflict = ConflictRecord(
            proposal_a_id="a",
            proposal_b_id="b",
            conflict_type=ConflictType.DIRECT,
            affected_resource="svc-1",
            resource_type=ResourceType.SERVICE,
        )
        low_a = make_proposal(id="low-a", confidence=0.3, evidence_count=1)
        low_b = make_proposal(id="low-b", confidence=0.2, evidence_count=1)
        ranked = [(low_a, 0.3), (low_b, 0.2)]

        strategy = ConflictAnalyzer.recommend_resolution(conflict, ranked)
        assert strategy == ResolutionStrategy.AUTO_BLOCK


# ---------------------------------------------------------------------------
# Resolver Tests
# ---------------------------------------------------------------------------

class TestConflictResolver:
    @pytest.mark.asyncio
    async def test_auto_approve_resolution(self):
        await _seed_proposal(id="res-a", target_id="svc-1")
        await _seed_proposal(id="res-b", target_id="svc-1")

        conflict = ConflictRecord(
            proposal_a_id="res-a",
            proposal_b_id="res-b",
            conflict_type=ConflictType.COMPLEMENTARY,
            severity=ConflictSeverity.NONE,
            affected_resource="svc-1",
            resource_type=ResourceType.SERVICE,
            resolution_strategy=ResolutionStrategy.AUTO_APPROVE,
        )
        await ProposalStore.create_conflict(conflict)

        resolved = await ConflictResolver.resolve(conflict)
        assert resolved.resolved is True

        prop_a = await ProposalStore.get_proposal("res-a")
        assert prop_a.status == ProposalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_auto_block_resolution(self):
        await _seed_proposal(id="res-c", target_id="svc-2", confidence=0.3, evidence_count=2)
        await _seed_proposal(id="res-d", target_id="svc-2", confidence=0.2, evidence_count=1)

        conflict = ConflictRecord(
            proposal_a_id="res-c",
            proposal_b_id="res-d",
            conflict_type=ConflictType.DIRECT,
            severity=ConflictSeverity.BLOCKING,
            affected_resource="svc-2",
            resource_type=ResourceType.SERVICE,
            resolution_strategy=ResolutionStrategy.AUTO_BLOCK,
        )
        await ProposalStore.create_conflict(conflict)

        resolved = await ConflictResolver.resolve(conflict)
        assert resolved.resolved is True

        blocked = await ProposalStore.get_proposal("res-d")
        assert blocked.status in (ProposalStatus.BLOCKED, ProposalStatus.SUPERSEDED)

    @pytest.mark.asyncio
    async def test_rank_and_pick_resolution(self):
        await _seed_proposal(id="res-e", target_id="svc-3", confidence=0.95, evidence_count=10)
        await _seed_proposal(id="res-f", target_id="svc-3", confidence=0.3, evidence_count=1)

        conflict = ConflictRecord(
            proposal_a_id="res-e",
            proposal_b_id="res-f",
            conflict_type=ConflictType.DIRECT,
            affected_resource="svc-3",
            resource_type=ResourceType.SERVICE,
            resolution_strategy=ResolutionStrategy.RANK_AND_PICK,
        )
        await ProposalStore.create_conflict(conflict)

        resolved = await ConflictResolver.resolve(conflict)
        assert resolved.resolved is True

        winner = await ProposalStore.get_proposal("res-e")
        assert winner.status == ProposalStatus.APPROVED

        loser = await ProposalStore.get_proposal("res-f")
        assert loser.status == ProposalStatus.SUPERSEDED


# ---------------------------------------------------------------------------
# Coordinator Integration Tests
# ---------------------------------------------------------------------------

class TestConflictCoordinator:
    @pytest.mark.asyncio
    async def test_submit_proposal_no_conflicts(self):
        request = SubmitProposalRequest(
            agent="test-agent",
            title="Test",
            description="A test",
            action="rollback",
            target_id="svc-new",
            target_type="Service",
            resource_type=ResourceType.SERVICE,
            rationale="Testing",
            evidence_count=5,
            confidence=0.85,
        )
        response = await ConflictCoordinator.submit_proposal(request)
        assert response.status == ProposalStatus.APPROVED
        assert len(response.conflicts) == 0
        assert response.message == "Proposal approved. No conflicts detected."

    @pytest.mark.asyncio
    async def test_submit_proposal_with_conflict(self):
        await _seed_proposal(
            id="coord-existing",
            action="rollback",
            target_id="svc-conflict",
            agent="existing-agent",
        )

        request = SubmitProposalRequest(
            agent="new-agent",
            title="Conflicting proposal",
            description="This should conflict",
            action="scale",
            target_id="svc-conflict",
            target_type="Service",
            resource_type=ResourceType.SERVICE,
            rationale="Testing conflict detection",
            evidence_count=3,
            confidence=0.7,
        )
        response = await ConflictCoordinator.submit_proposal(request)
        assert len(response.conflicts) >= 1
        assert any(c.conflict_type == ConflictType.DIRECT for c in response.conflicts)

    @pytest.mark.asyncio
    async def test_lock_acquire_and_release(self):
        await _seed_proposal(id="lock-prop", target_id="svc-lock-target")

        acquired = await ConflictCoordinator.acquire_proposal_lock(
            resource_id="svc-lock-target",
            resource_type=ResourceType.SERVICE,
            proposal_id="lock-prop",
            agent="test-agent",
            ttl_minutes=10,
        )
        assert acquired is True

        second = await ConflictCoordinator.acquire_proposal_lock(
            resource_id="svc-lock-target",
            resource_type=ResourceType.SERVICE,
            proposal_id="lock-prop-2",
            agent="test-agent-2",
            ttl_minutes=10,
        )
        assert second is False

        released = await ConflictCoordinator.release_proposal_lock(
            resource_id="svc-lock-target",
            proposal_id="lock-prop",
        )
        assert released is True

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        health = await ConflictCoordinator.health()
        assert "active_proposals" in health
        assert "unresolved_conflicts" in health
        assert "status" in health


# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_submit_proposal(client: AsyncClient, auth_headers: dict):
    response = await client.post(
        "/coordinator/proposals",
        json={
            "agent": "test-agent",
            "title": "API test proposal",
            "description": "Testing via API",
            "action": "rollback",
            "target_id": "svc-api-test",
            "target_type": "Service",
            "resource_type": "Service",
            "rationale": "Testing",
            "evidence_count": 3,
            "confidence": 0.8,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"
    assert data["proposal"]["action"] == "rollback"
    assert data["proposal"]["target_id"] == "svc-api-test"


@pytest.mark.asyncio
async def test_api_submit_proposal_missing_target(client: AsyncClient, auth_headers: dict):
    response = await client.post(
        "/coordinator/proposals",
        json={"agent": "test", "action": "rollback"},
        headers=auth_headers,
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_api_get_proposal(client: AsyncClient, auth_headers: dict):
    await _seed_proposal(id="api-get-1")

    response = await client.get("/coordinator/proposals/api-get-1", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "api-get-1"
    assert data["action"] == "rollback"


@pytest.mark.asyncio
async def test_api_get_proposal_not_found(client: AsyncClient, auth_headers: dict):
    response = await client.get("/coordinator/proposals/nonexistent", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_list_proposals(client: AsyncClient, auth_headers: dict):
    await _seed_proposal(id="api-list-1", target_id="svc-list")
    await _seed_proposal(id="api-list-2", target_id="svc-list")

    response = await client.get("/coordinator/proposals?resource_id=svc-list", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2


@pytest.mark.asyncio
async def test_api_list_conflicts(client: AsyncClient, auth_headers: dict):
    await _seed_proposal(id="api-conf-a")
    await _seed_proposal(id="api-conf-b")
    c = ConflictRecord(
        proposal_a_id="api-conf-a",
        proposal_b_id="api-conf-b",
        affected_resource="svc-api",
        resource_type=ResourceType.SERVICE,
    )
    await ProposalStore.create_conflict(c)

    response = await client.get("/coordinator/conflicts", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 1


@pytest.mark.asyncio
async def test_api_resource_summary(client: AsyncClient, auth_headers: dict):
    await _seed_proposal(id="api-sum-a", target_id="svc-summary")
    await _seed_proposal(id="api-sum-b", target_id="svc-summary")

    response = await client.get("/coordinator/resources/svc-summary/summary", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["active_proposals"] >= 2


@pytest.mark.asyncio
async def test_api_acquire_lock(client: AsyncClient, auth_headers: dict):
    await _seed_proposal(id="api-lock-prop", target_id="svc-lock")

    response = await client.post(
        "/coordinator/locks/acquire",
        params={
            "resource_id": "svc-lock",
            "resource_type": "Service",
            "proposal_id": "api-lock-prop",
            "agent": "test-agent",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["acquired"] is True


@pytest.mark.asyncio
async def test_api_release_lock(client: AsyncClient, auth_headers: dict):
    await _seed_proposal(id="api-rel-prop", target_id="svc-rel")
    await ConflictCoordinator.acquire_proposal_lock(
        "svc-rel", ResourceType.SERVICE, "api-rel-prop", "test-agent",
    )

    response = await client.post(
        "/coordinator/locks/release",
        params={"resource_id": "svc-rel", "proposal_id": "api-rel-prop"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["released"] is True


@pytest.mark.asyncio
async def test_api_coordinator_health(client: AsyncClient, auth_headers: dict):
    response = await client.get("/coordinator/health", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_proposal_with_zero_evidence(self):
        p = make_proposal(id="edge-no-evidence", evidence_count=0)
        score = ConflictAnalyzer.score_proposal(p)
        assert score >= 0

    @pytest.mark.asyncio
    async def test_proposal_with_max_confidence(self):
        p = make_proposal(confidence=1.0)
        score = ConflictAnalyzer.score_proposal(p)
        assert score <= 1.0

    @pytest.mark.asyncio
    async def test_detect_no_active_proposals(self):
        p = make_proposal(id="edge-no-active", target_id="svc-nonexistent")
        conflicts = await ConflictDetector.detect_for_proposal(p)
        assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_different_resource_types_no_conflict(self):
        await _seed_proposal(id="edge-type-a", action="rollback", target_id="svc-1", resource_type=ResourceType.SERVICE)
        p2 = make_proposal(id="edge-type-b", action="scale", target_id="dep-1", resource_type=ResourceType.DEPLOYMENT)

        conflicts = await ConflictDetector.detect_for_proposal(p2)
        assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_proposal(self):
        c = ConflictRecord(
            proposal_a_id="nonexistent-a",
            proposal_b_id="nonexistent-b",
            affected_resource="svc-x",
            resource_type=ResourceType.SERVICE,
        )
        result = await ConflictResolver.resolve(c)
        # When proposals not found, resolver logs warning and returns without resolution
        assert result.resolved is False

    @pytest.mark.asyncio
    async def test_release_nonexistent_lock(self):
        released = await ProposalStore.release_lock("svc-no-lock", "prop-no-lock")
        assert released is False

    @pytest.mark.asyncio
    async def test_is_locked(self):
        await _seed_proposal(id="lock-check-prop", target_id="svc-lock-check")
        await ConflictCoordinator.acquire_proposal_lock(
            "svc-lock-check", ResourceType.SERVICE, "lock-check-prop", "agent",
        )

        locked = await ProposalStore.is_locked("svc-lock-check")
        assert locked is True

        await ConflictCoordinator.release_proposal_lock("svc-lock-check", "lock-check-prop")

        locked = await ProposalStore.is_locked("svc-lock-check")
        assert locked is False
