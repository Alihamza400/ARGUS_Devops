import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.graph.connection import Neo4jConnection


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


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "neo4j" in data


@pytest.mark.asyncio
async def test_create_repository_node(client: AsyncClient):
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
    )
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == "repo-1"
    assert data["type"] == "Repository"
    assert data["properties"]["name"] == "argus"


@pytest.mark.asyncio
async def test_create_duplicate_node_returns_409(client: AsyncClient):
    payload = {
        "type": "Repository",
        "id": "repo-dup",
        "properties": {"name": "dup", "url": "https://example.com/dup", "default_branch": "main", "provider": "github"},
    }
    await client.post("/graph/nodes", json=payload)
    response = await client.post("/graph/nodes", json=payload)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_service_and_link_to_repo(client: AsyncClient):
    await client.post(
        "/graph/nodes",
        json={
            "type": "Repository",
            "id": "repo-1",
            "properties": {"name": "argus", "url": "https://github.com/org/argus", "default_branch": "main", "provider": "github"},
        },
    )
    await client.post(
        "/graph/nodes",
        json={
            "type": "Service",
            "id": "svc-1",
            "properties": {"name": "api-gateway", "namespace": "default", "image": "org/api:latest", "replicas": 3},
        },
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
    )
    assert response.status_code == 201
    data = response.json()
    assert data["type"] == "DEPLOYED_FROM"
    assert data["source_id"] == "svc-1"
    assert data["target_id"] == "repo-1"


@pytest.mark.asyncio
async def test_traverse_service_to_repo_path(client: AsyncClient):
    await client.post(
        "/graph/nodes",
        json={"type": "Repository", "id": "repo-1", "properties": {"name": "argus", "url": "https://github.com/org/argus", "default_branch": "main", "provider": "github"}},
    )
    await client.post(
        "/graph/nodes",
        json={"type": "Service", "id": "svc-1", "properties": {"name": "api-gateway", "namespace": "default", "image": "org/api:latest", "replicas": 3}},
    )
    await client.post(
        "/graph/edges",
        json={"source_type": "Service", "source_id": "svc-1", "target_type": "Repository", "target_id": "repo-1", "type": "DEPLOYED_FROM"},
    )
    response = await client.get("/graph/nodes/svc-1/subgraph?depth=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) >= 2
    node_ids = {n["id"] for n in data["nodes"]}
    assert "svc-1" in node_ids
    assert "repo-1" in node_ids


@pytest.mark.asyncio
async def test_get_node_not_found(client: AsyncClient):
    response = await client.get("/graph/nodes/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_edge_to_nonexistent_node(client: AsyncClient):
    response = await client.post(
        "/graph/edges",
        json={
            "source_type": "Service",
            "source_id": "does-not-exist",
            "target_type": "Repository",
            "target_id": "also-does-not-exist",
            "type": "DEPLOYED_FROM",
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_schema_endpoint(client: AsyncClient):
    response = await client.get("/graph/schema")
    assert response.status_code == 200
    data = response.json()
    assert "node_types" in data
    assert "Repository" in data["node_types"]
    assert "Service" in data["node_types"]
    assert "edge_types" in data
    assert "DEPLOYED_FROM" in data["edge_types"]


@pytest.mark.asyncio
async def test_list_nodes_by_type(client: AsyncClient):
    await client.post(
        "/graph/nodes",
        json={"type": "Repository", "id": "repo-1", "properties": {"name": "argus", "url": "https://github.com/org/argus", "default_branch": "main", "provider": "github"}},
    )
    await client.post(
        "/graph/nodes",
        json={"type": "Service", "id": "svc-1", "properties": {"name": "api", "namespace": "default", "image": "org/api:latest", "replicas": 3}},
    )
    response = await client.get("/graph/nodes?type=Repository")
    assert response.status_code == 200
    data = response.json()
    assert all(n["type"] == "Repository" for n in data)
