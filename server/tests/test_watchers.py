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
async def test_incident_node_creation():
    from app.graph.schema import NodeType

    incident_id = "test-incident-001"
    await Neo4jConnection.run_query(
        """
        CREATE (n:Incident {
            id: $id, type: $type, severity: $severity,
            status: $status, message: $message,
            source: $source, detected_at: $detected_at
        })
        RETURN n
        """,
        {
            "id": incident_id,
            "type": "pod_crash",
            "severity": "critical",
            "status": "open",
            "message": "Pod api-gateway in CrashLoopBackOff",
            "source": "k8s-watcher",
            "detected_at": "2026-07-23T12:00:00Z",
        },
    )

    result = await Neo4jConnection.run_query(
        "MATCH (n:Incident {id: $id}) RETURN n",
        {"id": incident_id},
    )
    assert len(result) == 1
    assert result[0]["n"]["type"] == "pod_crash"
    assert result[0]["n"]["severity"] == "critical"
    assert result[0]["n"]["status"] == "open"


@pytest.mark.asyncio
async def test_incident_to_pod_edge():
    pod_id = "pod-test-ns-nginx-xyz"
    incident_id = "test-incident-002"

    await Neo4jConnection.run_query(
        "CREATE (n:Pod {id: $id, name: $name, phase: $phase, namespace: $namespace}) RETURN n",
        {"id": pod_id, "name": "nginx-xyz", "phase": "Running", "namespace": "test-ns"},
    )
    await Neo4jConnection.run_query(
        """
        CREATE (n:Incident {
            id: $id, type: $type, severity: $severity,
            status: $status, message: $message,
            source: $source, detected_at: $detected_at
        })
        RETURN n
        """,
        {
            "id": incident_id,
            "type": "pod_crash",
            "severity": "critical",
            "status": "open",
            "message": "nginx-xyz in CrashLoopBackOff",
            "source": "k8s-watcher",
            "detected_at": "2026-07-23T12:00:00Z",
        },
    )

    await Neo4jConnection.run_query(
        """
        MATCH (source:Incident {id: $incident_id})
        MATCH (target:Pod {id: $pod_id})
        MERGE (source)-[:DETECTED_IN {detected_at: $detected_at}]->(target)
        """,
        {
            "incident_id": incident_id,
            "pod_id": pod_id,
            "detected_at": "2026-07-23T12:00:00Z",
        },
    )

    result = await Neo4jConnection.run_query(
        """
        MATCH (i:Incident {id: $id})-[:DETECTED_IN]->(p:Pod)
        RETURN i, p
        """,
        {"id": incident_id},
    )
    assert len(result) == 1
    assert result[0]["p"]["id"] == pod_id
    assert result[0]["i"]["id"] == incident_id


@pytest.mark.asyncio
async def test_incident_api_creates_incident(client: AsyncClient, auth_headers: dict):
    pod_id = "pod-api-ns-web-1"
    await Neo4jConnection.run_query(
            "CREATE (n:Pod {id: $id, name: $name, phase: $phase, namespace: $namespace}) RETURN n",
            {"id": pod_id, "name": "web-1", "phase": "CrashLoopBackOff", "namespace": "api-ns"},
        )

    incident_id = "inc-api-ns-web-1-99999"
    await Neo4jConnection.run_query(
        """
        CREATE (n:Incident {
            id: $id, type: $type, severity: $severity,
            status: $status, message: $message,
            source: $source, detected_at: $detected_at
        })
        RETURN n
        """,
        {
            "id": incident_id,
            "type": "pod_crash",
            "severity": "critical",
            "status": "open",
            "message": "web-1 in CrashLoopBackOff",
            "source": "k8s-watcher",
            "detected_at": "2026-07-23T12:00:00Z",
        },
    )

    response = await client.get(f"/graph/nodes/{incident_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == incident_id
    assert data["type"] == "Incident"


@pytest.mark.asyncio
async def test_unhealthy_pods_query():
    pods_data = [
        ("pod-ns1-ok", "Running", "ns1"),
        ("pod-ns1-crash", "CrashLoopBackOff", "ns1"),
        ("pod-ns2-error", "Error", "ns2"),
        ("pod-ns3-pending", "Pending", "ns3"),
    ]
    for pod_id, phase, ns in pods_data:
        await Neo4jConnection.run_query(
            "CREATE (n:Pod {id: $id, name: $name, phase: $phase, namespace: $namespace}) RETURN n",
            {"id": pod_id, "name": pod_id.split("-", 2)[-1], "phase": phase, "namespace": ns},
        )

    from app.agents.queries import GraphQueries

    unhealthy = await GraphQueries.get_unhealthy_pods(limit=20)
    assert len(unhealthy) >= 3
    phases = {p.phase for p in unhealthy}
    assert "CrashLoopBackOff" in phases
    assert "Error" in phases
    assert "Running" not in phases
