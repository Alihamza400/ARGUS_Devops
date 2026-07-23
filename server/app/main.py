import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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
from app.monitoring.logging import get_logger, set_request_id, setup_logging
from app.monitoring.metrics import metrics_data, request_count, request_duration

setup_logging(settings.argus_log_level)
logger = get_logger("argus")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        set_request_id(request_id)

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        route = request.url.path
        method = request.method
        status_group = f"{response.status_code // 100}xx"

        request_count.labels(method=method, endpoint=route, status=status_group).inc()
        request_duration.labels(method=method, endpoint=route).observe(duration)

        response.headers["X-Request-ID"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    connected = await Neo4jConnection.verify_connectivity()
    if not connected:
        logger.warning("Neo4j not reachable", extra={"uri": settings.neo4j_uri})
    else:
        logger.info("Neo4j connected successfully")

    await AuthStore.ensure_schema()

    admin = await AuthStore.get_user_by_username("admin")
    if not admin:
        await AuthStore.create_user(
            username="admin",
            password="admin123",
            role="admin",
            email="admin@argus.local",
        )
        logger.info("Default admin user created", extra={"username": "admin"})

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
app.add_middleware(RequestIDMiddleware)

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
    status = "ok" if neo4j_ok else "degraded"
    logger.info("Health check", extra={"status": status, "neo4j": neo4j_ok})
    return {
        "status": status,
        "neo4j": "connected" if neo4j_ok else "disconnected",
        "version": settings.app_version,
        "environment": settings.argus_env,
    }


@app.get("/metrics")
async def metrics():
    return Response(
        content=await metrics_data(),
        media_type="text/plain; charset=utf-8",
    )
