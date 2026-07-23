from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.graph.connection import Neo4jConnection
from app.main import app
from app.monitoring.logging import get_logger, set_request_id


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
async def test_health_endpoint(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "neo4j" in data


@pytest.mark.asyncio
async def test_metrics_endpoint(client: AsyncClient):
    response = await client.get("/metrics")
    assert response.status_code == 200
    text = response.text
    assert "# HELP" in text
    assert "argus_requests_total" in text
    assert "argus_neo4j_up" in text
    assert "argus_graph_nodes_total" in text
    assert "argus_watcher_events_total" in text
    assert "argus_webhooks_received_total" in text
    assert "argus_incidents_total" in text


@pytest.mark.asyncio
async def test_metrics_records_requests(client: AsyncClient):
    await client.get("/health")
    response = await client.get("/metrics")
    text = response.text
    assert 'argus_requests_total{endpoint="/health",method="GET",status="2xx"}' in text


@pytest.mark.asyncio
async def test_request_id_header(client: AsyncClient):
    response = await client.get("/health", headers={"X-Request-ID": "my-custom-id"})
    assert response.headers.get("X-Request-ID") == "my-custom-id"


@pytest.mark.asyncio
async def test_request_id_auto_generated(client: AsyncClient):
    response = await client.get("/health")
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None
    assert len(request_id) > 0


@pytest.mark.asyncio
async def test_logger_output():
    import io
    import logging

    from app.monitoring.logging import JSONFormatter

    logger = logging.getLogger("test-json")
    logger.setLevel(logging.INFO)

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())
    logger.handlers.clear()
    logger.addHandler(handler)

    set_request_id("req-999")
    logger.info("hello world", extra={"extra_field": 42})

    output = buf.getvalue()
    parsed = json.loads(output)
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test-json"
    assert parsed["request_id"] == "req-999"
    assert "timestamp" in parsed


@pytest.mark.asyncio
async def test_neo4j_metric_updates(client: AsyncClient):
    await Neo4jConnection.run_query(
        "CREATE (n:Pod {id: 'metric-pod-1', name: 'pod-1', phase: 'Running', namespace: 'ns'}) RETURN n",
    )

    response = await client.get("/metrics")
    text = response.text
    assert 'argus_graph_nodes_total{type="Pod"}' in text
