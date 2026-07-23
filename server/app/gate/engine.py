from __future__ import annotations
import logging
from typing import Any
from app.coordinator.models import ProposalStatus
from app.coordinator.store import ProposalStore
from app.gate.models import ReviewDecision, ReviewRecord, ReviewResponse, ReviewSubmission
from app.gate.policy import ApprovalPolicyEngine
from app.gate.store import ReviewStore
logger = logging.getLogger("argus.gate.engine")

class ReviewEngine:
    @staticmethod
    async def submit_review(submission: ReviewSubmission) -> ReviewResponse:
        proposal = await ProposalStore.get_proposal(submission.proposal_id)
        if not proposal:
            return ReviewResponse(review=ReviewRecord(proposal_id=submission.proposal_id, reviewer=submission.reviewer), message="Proposal not found", proposal_status="not_found")
        if await ReviewStore.has_reviewer_acted(submission.proposal_id, submission.reviewer):
            return ReviewResponse(review=ReviewRecord(proposal_id=submission.proposal_id, reviewer=submission.reviewer), message=f"Reviewer already reviewed this proposal", proposal_status=proposal.status.value, policy_violations=["Duplicate review"])
        if proposal.status != ProposalStatus.PENDING and submission.decision != ReviewDecision.REJECTED:
            return ReviewResponse(review=ReviewRecord(proposal_id=submission.proposal_id, reviewer=submission.reviewer), message=f"Proposal is '{proposal.status.value}', not pending", proposal_status=proposal.status.value)

        policy = await ApprovalPolicyEngine.check(proposal=proposal.model_dump(), reviewer=submission.reviewer, reviewer_role=submission.reviewer_role)
        review = ReviewRecord(proposal_id=submission.proposal_id, reviewer=submission.reviewer, reviewer_role=submission.reviewer_role, decision=submission.decision, comment=submission.comment, evidence_checked=submission.evidence_checked)
        await ReviewStore.create_review(review)

        if not policy.passed and submission.decision == ReviewDecision.APPROVED:
            await ProposalStore.update_proposal_status(submission.proposal_id, ProposalStatus.PENDING, resolution="Policy violations", resolved_by="ReviewEngine", resolver_notes="; ".join(policy.violations))
            return ReviewResponse(review=review, proposal=proposal.model_dump(), policy_violations=policy.violations, proposal_status="pending", message="Review recorded but policy violations exist")

        if submission.decision == ReviewDecision.APPROVED:
            if await ApprovalPolicyEngine.is_approval_complete(submission.proposal_id):
                await ProposalStore.update_proposal_status(submission.proposal_id, ProposalStatus.APPROVED, resolution=f"Approved by {submission.reviewer}", resolved_by="ReviewEngine", resolver_notes=submission.comment)
                return ReviewResponse(review=review, proposal=proposal.model_dump(), proposal_status="approved", message="Proposal approved")
            config = await ReviewStore.get_policy_config()
            min_req = int(config.rules.get("min_reviewers", 1))
            apps = await ReviewStore.get_approval_count(submission.proposal_id)
            return ReviewResponse(review=review, proposal=proposal.model_dump(), proposal_status="pending", message=f"{apps}/{min_req} approvals, awaiting more")

        if submission.decision == ReviewDecision.REJECTED:
            await ProposalStore.update_proposal_status(submission.proposal_id, ProposalStatus.REJECTED, resolution=f"Rejected by {submission.reviewer}", resolved_by="ReviewEngine", resolver_notes=submission.comment)
            return ReviewResponse(review=review, proposal=proposal.model_dump(), proposal_status="rejected", message=f"Rejected: {submission.comment}")

        if submission.decision == ReviewDecision.CHANGES_REQUESTED:
            await ProposalStore.update_proposal_status(submission.proposal_id, ProposalStatus.PENDING, resolution=f"Changes requested by {submission.reviewer}", resolved_by="ReviewEngine", resolver_notes=submission.comment)
            return ReviewResponse(review=review, proposal=proposal.model_dump(), proposal_status="pending", message=f"Changes requested: {submission.comment}")

        return ReviewResponse(review=review, proposal=proposal.model_dump(), proposal_status=proposal.status.value, message=f"Review: {submission.decision.value}")

    @staticmethod
    async def get_review_status(proposal_id: str) -> dict[str, Any]:
        proposal = await ProposalStore.get_proposal(proposal_id)
        reviews = await ReviewStore.get_reviews_for_proposal(proposal_id)
        apps = await ReviewStore.get_approval_count(proposal_id)
        rejs = await ReviewStore.get_rejection_count(proposal_id)
        config = await ReviewStore.get_policy_config()
        min_req = int(config.rules.get("min_reviewers", 1))
        return {"proposal_id": proposal_id, "proposal_status": proposal.status.value if proposal else "unknown", "total_reviews": len(reviews), "approvals": apps, "rejections": rejs, "min_required": min_req, "approval_met": apps >= min_req, "reviews": [r.model_dump() for r in reviews]}

    @staticmethod
    async def list_pending_reviews(limit: int = 50) -> list[dict[str, Any]]:
        result = []
        for p in await ProposalStore.list_proposals(status=ProposalStatus.PENDING, limit=limit):
            result.append(await ReviewEngine.get_review_status(p.id))
        return result
