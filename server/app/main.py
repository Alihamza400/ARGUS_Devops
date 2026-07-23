from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.router import router as agent_router
from app.api.graph import router as graph_router
from app.api.webhooks import router as webhooks_router
from app.auth.router import router as auth_router
from app.auth.store import AuthStore
from app.config import settings
from app.coordinator.router import router as coordinator_router
from app.enforcer.router import router as enforcer_router
from app.gate.router import router as gate_router
from app.graph.connection import Neo4jConnection


@asynccontextmanager
async def lifespan(app: FastAPI):
    connected = await Neo4jConnection.verify_connectivity()
    if not connected:
        print("WARNING: Neo4j not reachable at", settings.neo4j_uri)
    else:
        print("Neo4j connected successfully")

    await AuthStore.ensure_schema()

    admin = await AuthStore.get_user_by_username("admin")
    if not admin:
        await AuthStore.create_user(
            username="admin",
            password="admin123",
            role="admin",
            email="admin@argus.local",
        )
        print("Default admin user created (admin / admin123)")

    if settings.k8s_watcher_enabled:
        from app.adapters.watchers.kubernetes import k8s_watcher

        await k8s_watcher.start()

    yield

    if settings.k8s_watcher_enabled:
        from app.adapters.watchers.kubernetes import k8s_watcher

        await k8s_watcher.stop()

    await Neo4jConnection.close()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(graph_router)
app.include_router(agent_router)
app.include_router(coordinator_router)
app.include_router(gate_router)
app.include_router(enforcer_router)
app.include_router(webhooks_router)
app.include_router(auth_router)


@app.get("/health")
async def health():
    neo4j_ok = await Neo4jConnection.verify_connectivity()
    return {
        "status": "ok" if neo4j_ok else "degraded",
        "neo4j": "connected" if neo4j_ok else "disconnected",
        "version": settings.app_version,
        "environment": settings.argus_env,
    }
