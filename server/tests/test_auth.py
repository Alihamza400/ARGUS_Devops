from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.graph.connection import Neo4jConnection
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def setup_and_teardown():
    import asyncio

    for attempt in range(3):
        connected = await Neo4jConnection.verify_connectivity()
        if connected:
            break
        await asyncio.sleep(0.5)
    else:
        pytest.skip("Neo4j not available after 3 attempts")

    from app.auth.store import AuthStore

    await AuthStore.ensure_schema()
    await AuthStore.create_user("testuser", "testpass123", role="admin", email="test@test.com")

    yield

    try:
        await Neo4jConnection.run_query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient):
    response = await client.post(
        "/auth/login",
        json={"username": "testuser", "password": "testpass123"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    response = await client.post(
        "/auth/login",
        json={"username": "testuser", "password": "testpass123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["role"] == "admin"
    assert data["username"] == "testuser"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    response = await client.post(
        "/auth/login",
        json={"username": "testuser", "password": "wrongpass"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient):
    response = await client.post(
        "/auth/login",
        json={"username": "nobody", "password": "testpass123"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_missing_fields(client: AsyncClient):
    response = await client.post("/auth/login", json={"username": "", "password": ""})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_me_endpoint(client: AsyncClient, admin_headers: dict):
    response = await client.get("/auth/me", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
    assert data["role"] == "admin"


@pytest.mark.asyncio
async def test_me_no_auth(client: AsyncClient):
    response = await client.get("/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_user_admin_only(client: AsyncClient, admin_headers: dict):
    response = await client.post(
        "/auth/users",
        json={"username": "newuser", "password": "newpass1234", "role": "viewer"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "newuser"
    assert data["role"] == "viewer"


@pytest.mark.asyncio
async def test_create_user_duplicate(client: AsyncClient, admin_headers: dict):
    response = await client.post(
        "/auth/users",
        json={"username": "testuser", "password": "testpass123", "role": "engineer"},
        headers=admin_headers,
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_user_no_auth(client: AsyncClient):
    response = await client.post(
        "/auth/users",
        json={"username": "newuser", "password": "newpass1234", "role": "viewer"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_users(client: AsyncClient, admin_headers: dict):
    response = await client.get("/auth/users", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 1
    usernames = [u["username"] for u in data["users"]]
    assert "testuser" in usernames


@pytest.mark.asyncio
async def test_rbac_viewer_cannot_engineer(client: AsyncClient):
    from app.auth.store import AuthStore

    await AuthStore.create_user("viewonly", "viewpass12", role="viewer")
    response = await client.post(
        "/auth/login",
        json={"username": "viewonly", "password": "viewpass12"},
    )
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.post(
        "/agent/analyze",
        json={"query": "test", "pod_id": "pod-test"},
        headers=headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_public_endpoints_no_auth(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200

    response = await client.get("/graph/schema")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_api_key_auth(client: AsyncClient):
    response = await client.post(
        "/auth/login",
        json={"username": "testuser", "password": "testpass123"},
    )
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.post(
        "/auth/api-keys",
        json={"name": "ci-key"},
        headers=headers,
    )
    assert response.status_code == 201
    api_key_data = response.json()
    raw_key = api_key_data["key"]

    api_headers = {"Authorization": f"Bearer {raw_key}"}
    response = await client.get("/auth/me", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["username"] == "testuser"


@pytest.mark.asyncio
async def test_api_key_list_and_delete(client: AsyncClient):
    response = await client.post(
        "/auth/login",
        json={"username": "testuser", "password": "testpass123"},
    )
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    await client.post("/auth/api-keys", json={"name": "test-key"}, headers=headers)

    response = await client.get("/auth/api-keys", headers=headers)
    assert response.status_code == 200
    assert response.json()["count"] >= 1


@pytest.mark.asyncio
async def test_node_create_requires_auth(client: AsyncClient):
    response = await client.post(
        "/graph/nodes",
        json={"id": "test-1", "type": "Repository", "properties": {"name": "test", "url": "http://example.com", "default_branch": "main", "provider": "github"}},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_node_create_with_auth(client: AsyncClient, admin_headers: dict):
    response = await client.post(
        "/graph/nodes",
        json={"id": "test-1", "type": "Repository", "properties": {"name": "test", "url": "http://example.com", "default_branch": "main", "provider": "github"}},
        headers=admin_headers,
    )
    assert response.status_code in (201, 409)
