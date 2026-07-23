from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient):
    from app.auth.store import AuthStore
    from app.graph.connection import Neo4jConnection

    await AuthStore.ensure_schema()
    existing = await AuthStore.get_user_by_username("admin")
    if not existing:
        await AuthStore.create_user("admin", "admin123", role="admin")

    response = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
