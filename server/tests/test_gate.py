from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.coordinator.coordinator import ConflictCoordinator
from app.coordinator.models import ProposalRecord, ProposalStatus, ResourceType
from app.coordinator.store import ProposalStore
from app.gate.engine import ReviewEngine
from app.gate.models import (
    ApprovalPolicyConfig,
    ReviewDecision,
    ReviewRecord,
    ReviewSubmission,
    ReviewerRole,
)
from app.gate.policy import ApprovalPolicyEngine
from app.gate.store import ReviewStore
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
        await ReviewStore.ensure_schema()
    except Exception:
        pass
    yield
    await Neo4jConnection.run_query("MATCH (n) DETACH DELETE n")


async def _seed_proposal(**kwargs) -> ProposalRecord:
    defaults = dict(
        id="gate-test-prop",
        agent="test-agent",
        agent_version="1.0.0",
        title="Test proposal for gate",
        description="A test proposal",
        action="rollback",
        target_id="svc-gate-test",
        target_type="Service",
        resource_type=ResourceType.SERVICE,
        rationale="Testing the approval gate",
        evidence_count=5,
        evidence_summary="5 evidence items",
        confidence=0.85,
        risk_level="medium",
        severity="high",
        status=ProposalStatus.PENDING,
        tags=["test"],
    )
    defaults.update(kwargs)
    p = ProposalRecord(**defaults)
    await ProposalStore.create_proposal(p)
    return p


# ---------------------------------------------------------------------------
# ReviewStore Tests
# ---------------------------------------------------------------------------

class TestReviewStore:
    @pytest.mark.asyncio
    async def test_create_and_get_review(self):
        await _seed_proposal(id="rs-prop-1")
        review = ReviewRecord(proposal_id="rs-prop-1", reviewer="alice", decision=ReviewDecision.APPROVED)
        await ReviewStore.create_review(review)

        fetched = await ReviewStore.get_review(review.id)
        assert fetched is not None
        assert fetched.proposal_id == "rs-prop-1"
        assert fetched.reviewer == "alice"
        assert fetched.decision == ReviewDecision.APPROVED

    @pytest.mark.asyncio
    async def test_list_reviews_by_proposal(self):
        await _seed_proposal(id="rs-prop-2")
        for r in ["bob", "carol"]:
            rev = ReviewRecord(proposal_id="rs-prop-2", reviewer=r, decision=ReviewDecision.APPROVED)
            await ReviewStore.create_review(rev)

        reviews = await ReviewStore.get_reviews_for_proposal("rs-prop-2")
        assert len(reviews) == 2

    @pytest.mark.asyncio
    async def test_list_reviews_by_reviewer(self):
        await _seed_proposal(id="rs-prop-3")
        rev = ReviewRecord(proposal_id="rs-prop-3", reviewer="dave", decision=ReviewDecision.APPROVED)
        await ReviewStore.create_review(rev)

        reviews = await ReviewStore.list_reviews(reviewer="dave")
        assert len(reviews) >= 1
        assert reviews[0].reviewer == "dave"

    @pytest.mark.asyncio
    async def test_approval_count(self):
        await _seed_proposal(id="rs-prop-4")
        for r in ["eve", "frank"]:
            rev = ReviewRecord(proposal_id="rs-prop-4", reviewer=r, decision=ReviewDecision.APPROVED)
            await ReviewStore.create_review(rev)

        count = await ReviewStore.get_approval_count("rs-prop-4")
        assert count == 2

    @pytest.mark.asyncio
    async def test_rejection_count(self):
        await _seed_proposal(id="rs-prop-5")
        rev = ReviewRecord(proposal_id="rs-prop-5", reviewer="grace", decision=ReviewDecision.REJECTED)
        await ReviewStore.create_review(rev)

        count = await ReviewStore.get_rejection_count("rs-prop-5")
        assert count == 1

    @pytest.mark.asyncio
    async def test_has_reviewer_acted(self):
        await _seed_proposal(id="rs-prop-6")
        rev = ReviewRecord(proposal_id="rs-prop-6", reviewer="heidi", decision=ReviewDecision.APPROVED)
        await ReviewStore.create_review(rev)

        assert await ReviewStore.has_reviewer_acted("rs-prop-6", "heidi") is True
        assert await ReviewStore.has_reviewer_acted("rs-prop-6", "ivan") is False

    @pytest.mark.asyncio
    async def test_policy_config_defaults(self):
        config = await ReviewStore.get_policy_config()
        assert config.rules["min_reviewers"] == 1
        assert config.rules["no_self_approval"] is True
        assert config.rules["senior_for_critical"] is True

    @pytest.mark.asyncio
    async def test_save_and_load_policy_config(self):
        config = ApprovalPolicyConfig(rules={"min_reviewers": 3})
        await ReviewStore.save_policy_config(config)

        loaded = await ReviewStore.get_policy_config()
        assert loaded.rules["min_reviewers"] == 3


# ---------------------------------------------------------------------------
# PolicyEngine Tests
# ---------------------------------------------------------------------------

class TestApprovalPolicyEngine:
    @pytest.mark.asyncio
    async def test_policy_passes_with_min_approvals(self):
        await _seed_proposal(id="pe-prop-1")
        rev = ReviewRecord(proposal_id="pe-prop-1", reviewer="alice", decision=ReviewDecision.APPROVED)
        await ReviewStore.create_review(rev)

        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 1
        await ReviewStore.save_policy_config(config)

        proposal = (await ProposalStore.get_proposal("pe-prop-1")).model_dump()
        result = await ApprovalPolicyEngine.check(proposal=proposal, reviewer="bob")

        assert result.passed is True
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_policy_passes_with_min_approvals_check(self):
        await _seed_proposal(id="pe-prop-2")
        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 3
        await ReviewStore.save_policy_config(config)

        complete = await ApprovalPolicyEngine.is_approval_complete("pe-prop-2")
        assert complete is False

        appr = ReviewRecord(proposal_id="pe-prop-2", reviewer="a", decision=ReviewDecision.APPROVED)
        appr2 = ReviewRecord(proposal_id="pe-prop-2", reviewer="b", decision=ReviewDecision.APPROVED)
        appr3 = ReviewRecord(proposal_id="pe-prop-2", reviewer="c", decision=ReviewDecision.APPROVED)
        await ReviewStore.create_review(appr)
        await ReviewStore.create_review(appr2)
        await ReviewStore.create_review(appr3)

        complete = await ApprovalPolicyEngine.is_approval_complete("pe-prop-2")
        assert complete is True

    @pytest.mark.asyncio
    async def test_no_self_approval(self):
        await _seed_proposal(id="pe-prop-3", agent="alice")
        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 0
        await ReviewStore.save_policy_config(config)

        proposal = (await ProposalStore.get_proposal("pe-prop-3")).model_dump()
        result = await ApprovalPolicyEngine.check(proposal=proposal, reviewer="alice")

        assert result.passed is False
        assert any("Self-approval" in v for v in result.violations)

    @pytest.mark.asyncio
    async def test_senior_required_for_critical(self):
        await _seed_proposal(id="pe-prop-4", severity="critical")
        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 0
        await ReviewStore.save_policy_config(config)

        proposal = (await ProposalStore.get_proposal("pe-prop-4")).model_dump()
        result = await ApprovalPolicyEngine.check(
            proposal=proposal, reviewer="bob", reviewer_role=ReviewerRole.PEER
        )

        assert result.passed is False
        assert any("senior" in v.lower() for v in result.violations)

    @pytest.mark.asyncio
    async def test_min_evidence_check(self):
        await _seed_proposal(id="pe-prop-5", evidence_count=0)
        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 0
        config.rules["min_evidence_count"] = 3
        await ReviewStore.save_policy_config(config)

        proposal = (await ProposalStore.get_proposal("pe-prop-5")).model_dump()
        result = await ApprovalPolicyEngine.check(proposal=proposal, reviewer="bob")

        assert result.passed is False
        assert any("evidence" in v.lower() for v in result.violations)

    @pytest.mark.asyncio
    async def test_min_confidence_check(self):
        await _seed_proposal(id="pe-prop-6", confidence=0.1)
        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 0
        config.rules["min_confidence"] = 0.5
        await ReviewStore.save_policy_config(config)

        proposal = (await ProposalStore.get_proposal("pe-prop-6")).model_dump()
        result = await ApprovalPolicyEngine.check(proposal=proposal, reviewer="bob")

        assert result.passed is False
        assert any("confidence" in v.lower() for v in result.violations)

    @pytest.mark.asyncio
    async def test_is_approval_complete(self):
        await _seed_proposal(id="pe-prop-7")
        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 2
        await ReviewStore.save_policy_config(config)

        assert await ApprovalPolicyEngine.is_approval_complete("pe-prop-7") is False

        ReviewRecord(proposal_id="pe-prop-7", reviewer="a", decision=ReviewDecision.APPROVED)
        ReviewRecord(proposal_id="pe-prop-7", reviewer="b", decision=ReviewDecision.APPROVED)

        assert await ApprovalPolicyEngine.is_approval_complete("pe-prop-7") is False

    @pytest.mark.asyncio
    async def test_update_policy(self):
        config = ApprovalPolicyConfig(rules={"min_reviewers": 5})
        await ApprovalPolicyEngine.update_policy(config)

        loaded = await ReviewStore.get_policy_config()
        assert loaded.rules["min_reviewers"] == 5


# ---------------------------------------------------------------------------
# ReviewEngine Tests
# ---------------------------------------------------------------------------

class TestReviewEngine:
    @pytest.mark.asyncio
    async def test_submit_approval(self):
        await _seed_proposal(id="re-prop-1")
        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 1
        await ReviewStore.save_policy_config(config)

        submission = ReviewSubmission(
            proposal_id="re-prop-1",
            reviewer="alice",
            reviewer_role=ReviewerRole.PEER,
            decision=ReviewDecision.APPROVED,
            comment="Looks good",
            evidence_checked=True,
        )
        response = await ReviewEngine.submit_review(submission)

        assert response.proposal_status == "approved"
        assert "approved" in response.message.lower()

        proposal = await ProposalStore.get_proposal("re-prop-1")
        assert proposal.status == ProposalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_submit_rejection(self):
        await _seed_proposal(id="re-prop-2")

        submission = ReviewSubmission(
            proposal_id="re-prop-2",
            reviewer="bob",
            decision=ReviewDecision.REJECTED,
            comment="Not ready",
            evidence_checked=True,
        )
        response = await ReviewEngine.submit_review(submission)

        assert response.proposal_status == "rejected"

        proposal = await ProposalStore.get_proposal("re-prop-2")
        assert proposal.status == ProposalStatus.REJECTED

    @pytest.mark.asyncio
    async def test_submit_changes_requested(self):
        await _seed_proposal(id="re-prop-3")

        submission = ReviewSubmission(
            proposal_id="re-prop-3",
            reviewer="carol",
            decision=ReviewDecision.CHANGES_REQUESTED,
            comment="Add more evidence",
            evidence_checked=True,
        )
        response = await ReviewEngine.submit_review(submission)

        assert response.proposal_status == "pending"

        proposal = await ProposalStore.get_proposal("re-prop-3")
        assert proposal.status == ProposalStatus.PENDING

    @pytest.mark.asyncio
    async def test_duplicate_review_rejected(self):
        await _seed_proposal(id="re-prop-4")

        sub1 = ReviewSubmission(
            proposal_id="re-prop-4", reviewer="dave", decision=ReviewDecision.APPROVED
        )
        await ReviewEngine.submit_review(sub1)

        sub2 = ReviewSubmission(
            proposal_id="re-prop-4", reviewer="dave", decision=ReviewDecision.APPROVED
        )
        response = await ReviewEngine.submit_review(sub2)

        assert "already reviewed" in response.message.lower()

    @pytest.mark.asyncio
    async def test_non_pending_proposal_rejected(self):
        await _seed_proposal(id="re-prop-5", status=ProposalStatus.APPROVED)

        submission = ReviewSubmission(
            proposal_id="re-prop-5", reviewer="eve", decision=ReviewDecision.APPROVED
        )
        response = await ReviewEngine.submit_review(submission)

        assert "not pending" in response.message.lower()

    @pytest.mark.asyncio
    async def test_get_review_status(self):
        await _seed_proposal(id="re-prop-6")
        rev = ReviewRecord(proposal_id="re-prop-6", reviewer="frank", decision=ReviewDecision.APPROVED)
        await ReviewStore.create_review(rev)

        status = await ReviewEngine.get_review_status("re-prop-6")
        assert status["total_reviews"] == 1
        assert status["approvals"] == 1

    @pytest.mark.asyncio
    async def test_list_pending_reviews(self):
        await _seed_proposal(id="re-prop-7", status=ProposalStatus.PENDING)
        await _seed_proposal(id="re-prop-8", status=ProposalStatus.PENDING)

        pending = await ReviewEngine.list_pending_reviews()
        assert len(pending) >= 2

    @pytest.mark.asyncio
    async def test_approval_requires_policy_compliance(self):
        await _seed_proposal(id="re-prop-9", agent="alice")
        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 0
        config.rules["senior_for_critical"] = True
        await ReviewStore.save_policy_config(config)

        proposal = await ProposalStore.get_proposal("re-prop-9")
        proposal_dict = proposal.model_dump()
        proposal_dict["severity"] = "critical"

        submission = ReviewSubmission(
            proposal_id="re-prop-9",
            reviewer="alice",
            reviewer_role=ReviewerRole.PEER,
            decision=ReviewDecision.APPROVED,
        )
        response = await ReviewEngine.submit_review(submission)

        assert len(response.policy_violations) >= 1


# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_api_submit_review(client: AsyncClient):
    await _seed_proposal(id="api-gate-1")
    config = await ReviewStore.get_policy_config()
    config.rules["min_reviewers"] = 1
    await ReviewStore.save_policy_config(config)

    response = await client.post(
        "/gate/review",
        json={
            "proposal_id": "api-gate-1",
            "reviewer": "alice",
            "decision": "approved",
            "comment": "Approved via API",
            "evidence_checked": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["proposal_status"] == "approved"
    assert data["review"]["reviewer"] == "alice"


@pytest.mark.asyncio
async def test_api_submit_review_missing_fields(client: AsyncClient):
    response = await client.post("/gate/review", json={})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_api_get_review_status(client: AsyncClient):
    await _seed_proposal(id="api-gate-2")

    response = await client.get("/gate/proposals/api-gate-2/status")
    assert response.status_code == 200
    data = response.json()
    assert data["proposal_id"] == "api-gate-2"
    assert data["proposal_status"] == "pending"


@pytest.mark.asyncio
async def test_api_get_review_status_not_found(client: AsyncClient):
    response = await client.get("/gate/proposals/nonexistent/status")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_list_pending(client: AsyncClient):
    await _seed_proposal(id="api-gate-3", status=ProposalStatus.PENDING)

    response = await client.get("/gate/pending")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 1


@pytest.mark.asyncio
async def test_api_get_policy(client: AsyncClient):
    response = await client.get("/gate/policy")
    assert response.status_code == 200
    data = response.json()
    assert "rules" in data
    assert "min_reviewers" in data["rules"]


@pytest.mark.asyncio
async def test_api_update_policy(client: AsyncClient):
    response = await client.put(
        "/gate/policy",
        json={"rules": {"min_reviewers": 3, "no_self_approval": True}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "updated"

    config = await ReviewStore.get_policy_config()
    assert config.rules["min_reviewers"] == 3


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_review_with_empty_comment(self):
        await _seed_proposal(id="edge-1")
        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 1
        await ReviewStore.save_policy_config(config)

        submission = ReviewSubmission(
            proposal_id="edge-1", reviewer="alice", decision=ReviewDecision.APPROVED
        )
        response = await ReviewEngine.submit_review(submission)
        assert response.proposal_status == "approved"

    @pytest.mark.asyncio
    async def test_multiple_reviewers_until_quorum(self):
        await _seed_proposal(id="edge-2")
        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 2
        await ReviewStore.save_policy_config(config)

        sub1 = ReviewSubmission(
            proposal_id="edge-2", reviewer="bob", decision=ReviewDecision.APPROVED
        )
        r1 = await ReviewEngine.submit_review(sub1)
        assert r1.proposal_status == "pending"

        sub2 = ReviewSubmission(
            proposal_id="edge-2", reviewer="carol", decision=ReviewDecision.APPROVED
        )
        r2 = await ReviewEngine.submit_review(sub2)
        assert r2.proposal_status == "approved"

    @pytest.mark.asyncio
    async def test_reject_overrides_approval(self):
        await _seed_proposal(id="edge-3")
        config = await ReviewStore.get_policy_config()
        config.rules["min_reviewers"] = 1
        await ReviewStore.save_policy_config(config)

        sub1 = ReviewSubmission(
            proposal_id="edge-3", reviewer="dave", decision=ReviewDecision.APPROVED
        )
        await ReviewEngine.submit_review(sub1)

        sub2 = ReviewSubmission(
            proposal_id="edge-3", reviewer="eve", decision=ReviewDecision.REJECTED
        )
        r2 = await ReviewEngine.submit_review(sub2)
        assert r2.proposal_status == "rejected"

    @pytest.mark.asyncio
    async def test_proposal_not_found(self):
        submission = ReviewSubmission(
            proposal_id="nonexistent", reviewer="frank", decision=ReviewDecision.APPROVED
        )
        response = await ReviewEngine.submit_review(submission)
        assert "not found" in response.message.lower()

    @pytest.mark.asyncio
    async def test_policy_config_persistence(self):
        config1 = ApprovalPolicyConfig(rules={"min_reviewers": 7})
        await ReviewStore.save_policy_config(config1)

        config2 = await ReviewStore.get_policy_config()
        assert config2.rules["min_reviewers"] == 7

    @pytest.mark.asyncio
    async def test_review_creates_edge_to_proposal(self):
        await _seed_proposal(id="edge-4")
        rev = ReviewRecord(proposal_id="edge-4", reviewer="grace", decision=ReviewDecision.APPROVED)
        await ReviewStore.create_review(rev)

        result = await Neo4jConnection.run_query(
            "MATCH (r:Review {id: $rid})-[rel:REVIEWS]->(p:Proposal {id: $pid}) RETURN rel",
            {"rid": rev.id, "pid": "edge-4"},
        )
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_empty_pending_list(self):
        pending = await ReviewEngine.list_pending_reviews()
        assert isinstance(pending, list)
