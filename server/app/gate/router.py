from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import require_admin, require_engineer, require_viewer
from app.gate.engine import ReviewEngine
from app.gate.models import ApprovalPolicyConfig, ReviewResponse, ReviewSubmission
from app.gate.store import ReviewStore

router = APIRouter(prefix="/gate", tags=["gate"])


@router.post("/schema")
async def ensure_schema(current_user: dict = Depends(require_admin)):
    return {"migrations": await ReviewStore.ensure_schema()}


@router.post("/review", response_model=ReviewResponse)
async def submit_review(
    submission: ReviewSubmission,
    current_user: dict = Depends(require_engineer),
):
    if not submission.proposal_id:
        raise HTTPException(400, "proposal_id required")
    if not submission.reviewer:
        raise HTTPException(400, "reviewer required")
    return await ReviewEngine.submit_review(submission)


@router.get("/proposals/{proposal_id}/status")
async def get_review_status(
    proposal_id: str,
    current_user: dict = Depends(require_viewer),
):
    status = await ReviewEngine.get_review_status(proposal_id)
    if status.get("proposal_status") == "unknown":
        raise HTTPException(404, "Proposal not found")
    return status


@router.get("/pending")
async def list_pending(
    limit: int = Query(50, le=200),
    current_user: dict = Depends(require_viewer),
):
    items = await ReviewEngine.list_pending_reviews(limit=limit)
    return {"count": len(items), "items": items}


@router.get("/policy")
async def get_policy(current_user: dict = Depends(require_viewer)):
    return (await ReviewStore.get_policy_config()).model_dump()


@router.put("/policy")
async def update_policy(
    config: ApprovalPolicyConfig,
    current_user: dict = Depends(require_admin),
):
    await ReviewStore.save_policy_config(config)
    return {"status": "updated", "config": config.model_dump()}
