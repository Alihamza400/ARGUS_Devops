from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from app.enforcer.enforcer import EnforcerCoordinator
from app.enforcer.models import EnforceRequest, EnforceResponse, EnforcementAction, EnforcementStatus, EnforcerConfig
from app.enforcer.store import EnforcementStore
router = APIRouter(prefix="/enforce", tags=["enforcer"])

@router.post("/schema")
async def ensure_schema():
    return {"migrations": await EnforcementStore.ensure_schema()}

@router.post("/execute", response_model=EnforceResponse)
async def execute(request: EnforceRequest):
    if not request.proposal_id: raise HTTPException(400, "proposal_id required")
    return await EnforcerCoordinator.enforce(request)

@router.get("/enforcements/{enforcement_id}")
async def get_enforcement(enforcement_id: str):
    enf = await EnforcerCoordinator.get_enforcement(enforcement_id)
    if not enf: raise HTTPException(404, "Enforcement not found")
    return enf.model_dump()

@router.get("/enforcements")
async def list_enforcements(proposal_id: str | None = Query(None), status: EnforcementStatus | None = Query(None), limit: int = Query(50, le=200), offset: int = Query(0, ge=0)):
    items = await EnforcerCoordinator.list_enforcements(proposal_id=proposal_id, status=status, limit=limit, offset=offset)
    return {"count": len(items), "enforcements": [e.model_dump() for e in items]}

@router.get("/config")
async def get_config():
    return (await EnforcerCoordinator.get_config()).model_dump()

@router.put("/config")
async def update_config(config: EnforcerConfig):
    await EnforcerCoordinator.update_config(config)
    return {"status": "updated", "config": config.model_dump()}

@router.get("/health")
async def health():
    return await EnforcerCoordinator.health()
