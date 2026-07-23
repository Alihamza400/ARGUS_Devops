import pytest
import pytest_asyncio
from httpx import AsyncClient
from app.main import app






@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "neo4j" in data


@pytest.mark.asyncio
async def test_create_repository_node(client: AsyncClient, auth_headers: dict):
    response = await client.post(
        "/graph/nodes",
        json={
            "type": "Repository",
            "id": "repo-1",
            "properties": {
                "url": "https://github.com/org/argus",
                "name": "argus",
                "default_branch": "main",
                "provider": "github",
            },
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == "repo-1"
    assert data["type"] == "Repository"
    assert data["properties"]["name"] == "argus"


@pytest.mark.asyncio
async def test_create_duplicate_node_returns_409(client: AsyncClient, auth_headers: dict):
    payload = {
        "type": "Repository",
        "id": "repo-dup",
        "properties": {"name": "dup", "url": "https://example.com/dup", "default_branch": "main", "provider": "github"},
    }
    await client.post("/graph/nodes", json=payload, headers=auth_headers)
    response = await client.post("/graph/nodes", json=payload, headers=auth_headers)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_service_and_link_to_repo(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/graph/nodes",
        json={
            "type": "Repository",
            "id": "repo-1",
            "properties": {"name": "argus", "url": "https://github.com/org/argus", "default_branch": "main", "provider": "github"},
        },
        headers=auth_headers,
    )
    await client.post(
        "/graph/nodes",
        json={
            "type": "Service",
            "id": "svc-1",
            "properties": {"name": "api-gateway", "namespace": "default", "image": "org/api:latest", "replicas": 3},
        },
        headers=auth_headers,
    )
    response = await client.post(
        "/graph/edges",
        json={
            "source_type": "Service",
            "source_id": "svc-1",
            "target_type": "Repository",
            "target_id": "repo-1",
            "type": "DEPLOYED_FROM",
            "properties": {"version": "v1.0.0"},
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["type"] == "DEPLOYED_FROM"
    assert data["source_id"] == "svc-1"
    assert data["target_id"] == "repo-1"


@pytest.mark.asyncio
async def test_traverse_service_to_repo_path(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/graph/nodes",
        json={"type": "Repository", "id": "repo-1", "properties": {"name": "argus", "url": "https://github.com/org/argus", "default_branch": "main", "provider": "github"}},
        headers=auth_headers,
    )
    await client.post(
        "/graph/nodes",
        json={"type": "Service", "id": "svc-1", "properties": {"name": "api-gateway", "namespace": "default", "image": "org/api:latest", "replicas": 3}},
        headers=auth_headers,
    )
    await client.post(
        "/graph/edges",
        json={"source_type": "Service", "source_id": "svc-1", "target_type": "Repository", "target_id": "repo-1", "type": "DEPLOYED_FROM"},
        headers=auth_headers,
    )
    response = await client.get("/graph/nodes/svc-1/subgraph?depth=2", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) >= 2
    node_ids = {n["id"] for n in data["nodes"]}
    assert "svc-1" in node_ids
    assert "repo-1" in node_ids


@pytest.mark.asyncio
async def test_get_node_not_found(client: AsyncClient, auth_headers: dict):
    response = await client.get("/graph/nodes/nonexistent", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_edge_to_nonexistent_node(client: AsyncClient, auth_headers: dict):
    response = await client.post(
        "/graph/edges",
        json={
            "source_type": "Service",
            "source_id": "does-not-exist",
            "target_type": "Repository",
            "target_id": "also-does-not-exist",
            "type": "DEPLOYED_FROM",
        },
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_schema_endpoint(client: AsyncClient, auth_headers: dict):
    response = await client.get("/graph/schema", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "node_types" in data
    assert "Repository" in data["node_types"]
    assert "Service" in data["node_types"]
    assert "edge_types" in data
    assert "DEPLOYED_FROM" in data["edge_types"]


@pytest.mark.asyncio
async def test_list_nodes_by_type(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/graph/nodes",
        json={"type": "Repository", "id": "repo-1", "properties": {"name": "argus", "url": "https://github.com/org/argus", "default_branch": "main", "provider": "github"}},
        headers=auth_headers,
    )
    await client.post(
        "/graph/nodes",
        json={"type": "Service", "id": "svc-1", "properties": {"name": "api", "namespace": "default", "image": "org/api:latest", "replicas": 3}},
        headers=auth_headers,
    )
    response = await client.get("/graph/nodes?type=Repository", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert all(n["type"] == "Repository" for n in data)
