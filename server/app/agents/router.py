from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.agents.coordinator import AgentCoordinator
from app.agents.models import AgentQuery, AgentResponse
from app.auth.dependencies import require_engineer, require_viewer

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/agents")
async def list_agents(current_user: dict = Depends(require_viewer)):
    return {"agents": AgentCoordinator.list_agents()}


@router.post("/analyze", response_model=AgentResponse)
async def analyze(query: AgentQuery, current_user: dict = Depends(require_engineer)):
    if not query.pod_id and not query.pod_name and not query.service_name:
        raise HTTPException(
            status_code=400,
            detail="Provide pod_id, pod_name, or service_name",
        )
    return await AgentCoordinator.analyze(query)


@router.get("/analyze/{pod_id}", response_model=AgentResponse)
async def analyze_pod(
    pod_id: str,
    generate_proposal: bool = False,
    max_commits: int = 20,
    current_user: dict = Depends(require_engineer),
):
    query = AgentQuery(
        query=f"Analyze pod {pod_id}",
        pod_id=pod_id,
        generate_proposal=generate_proposal,
        max_commits=max_commits,
    )
    return await AgentCoordinator.analyze(query)


@router.get("/unhealthy")
async def list_unhealthy(limit: int = 20, current_user: dict = Depends(require_viewer)):
    from app.agents.queries import GraphQueries

    pods = await GraphQueries.get_unhealthy_pods(limit=limit)
    return {
        "count": len(pods),
        "pods": [
            {
                "id": p.id,
                "name": p.name,
                "phase": p.phase,
                "namespace": p.namespace,
                "node": p.node,
            }
            for p in pods
        ],
    }
