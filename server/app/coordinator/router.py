from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import require_admin, require_engineer, require_viewer
from app.coordinator.coordinator import ConflictCoordinator
from app.coordinator.models import (
    ConflictQuery,
    ConflictResolutionRequest,
    ProposalStatus,
    ResourceType,
    SubmitProposalRequest,
    SubmitProposalResponse,
)

router = APIRouter(prefix="/coordinator", tags=["coordinator"])


@router.post("/schema")
async def ensure_coordinator_schema(current_user: dict = Depends(require_admin)):
    results = await ConflictCoordinator.ensure_schema()
    return {"migrations": results}


@router.post("/proposals", response_model=SubmitProposalResponse)
async def submit_proposal(
    request: SubmitProposalRequest,
    current_user: dict = Depends(require_engineer),
):
    if not request.target_id:
        raise HTTPException(status_code=400, detail="target_id is required")
    if not request.action:
        raise HTTPException(status_code=400, detail="action is required")
    return await ConflictCoordinator.submit_proposal(request)


@router.get("/proposals/{proposal_id}")
async def get_proposal(
    proposal_id: str,
    current_user: dict = Depends(require_viewer),
):
    proposal = await ConflictCoordinator.get_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")
    return proposal.model_dump()


@router.get("/proposals")
async def list_proposals(
    resource_id: str | None = Query(None),
    resource_type: ResourceType | None = Query(None),
    status: ProposalStatus | None = Query(None),
    agent: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(require_viewer),
):
    proposals = await ConflictCoordinator.list_proposals(
        resource_id=resource_id,
        resource_type=resource_type,
        status=status,
        agent=agent,
        limit=limit,
        offset=offset,
    )
    return {"count": len(proposals), "proposals": [p.model_dump() for p in proposals]}


@router.get("/conflicts")
async def list_conflicts(
    resolved: bool | None = None,
    current_user: dict = Depends(require_viewer),
):
    conflicts = await ConflictCoordinator.list_conflicts(resolved=resolved)
    return {"count": len(conflicts), "conflicts": [c.model_dump() for c in conflicts]}


@router.post("/conflicts/resolve")
async def resolve_conflict(
    request: ConflictResolutionRequest,
    current_user: dict = Depends(require_admin),
):
    if not request.conflict_id:
        raise HTTPException(status_code=400, detail="conflict_id is required")
    result = await ConflictCoordinator.resolve_conflict_manually(request)
    if not result:
        raise HTTPException(status_code=404, detail=f"Conflict '{request.conflict_id}' not found")
    return result.model_dump()


@router.get("/resources/{resource_id}/summary")
async def resource_summary(
    resource_id: str,
    current_user: dict = Depends(require_viewer),
):
    return await ConflictCoordinator.get_resource_summary(resource_id)


@router.post("/locks/acquire")
async def acquire_lock(
    resource_id: str,
    resource_type: ResourceType,
    proposal_id: str,
    agent: str,
    ttl_minutes: int = 30,
    current_user: dict = Depends(require_engineer),
):
    acquired = await ConflictCoordinator.acquire_proposal_lock(
        resource_id=resource_id,
        resource_type=resource_type,
        proposal_id=proposal_id,
        agent=agent,
        ttl_minutes=ttl_minutes,
    )
    return {"acquired": acquired, "resource_id": resource_id}


@router.post("/locks/release")
async def release_lock(
    resource_id: str,
    proposal_id: str,
    current_user: dict = Depends(require_engineer),
):
    released = await ConflictCoordinator.release_proposal_lock(resource_id, proposal_id)
    return {"released": released}


@router.get("/health")
async def coordinator_health(current_user: dict = Depends(require_viewer)):
    return await ConflictCoordinator.health()


@router.post("/locks/release-expired")
async def release_expired_locks(current_user: dict = Depends(require_admin)):
    count = await ConflictCoordinator.release_expired_locks()
    return {"released": count}
