from __future__ import annotations

import json
from datetime import datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.graph.connection import Neo4jConnection

PUSH_PAYLOAD = {
    "ref": "refs/heads/main",
    "repository": {
        "full_name": "test-org/test-repo",
        "clone_url": "https://github.com/test-org/test-repo.git",
        "default_branch": "main",
    },
    "commits": [
        {
            "id": "abc123def456",
            "message": "Fix bug in handler",
            "author": {"name": "Alice", "email": "alice@test.com"},
            "timestamp": "2026-07-23T10:00:00Z",
        },
        {
            "id": "789012ghi345",
            "message": "Add new feature",
            "author": {"name": "Bob", "email": "bob@test.com"},
            "timestamp": "2026-07-23T09:00:00Z",
        },
    ],
    "head_commit": {
        "id": "abc123def456",
    },
}

PR_PAYLOAD = {
    "action": "closed",
    "pull_request": {
        "number": 42,
        "title": "Fix production bug",
        "state": "merged",
        "user": {"login": "alice"},
        "merged_at": "2026-07-23T11:00:00Z",
    },
    "repository": {
        "full_name": "test-org/test-repo",
    },
}

RELEASE_PAYLOAD = {
    "action": "published",
    "release": {
        "tag_name": "v1.2.3",
        "name": "Release v1.2.3",
        "author": {"login": "alice"},
        "published_at": "2026-07-23T12:00:00Z",
    },
    "repository": {
        "full_name": "test-org/test-repo",
    },
}


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
async def test_github_push_creates_commits(client: AsyncClient):
    response = await client.post(
        "/webhooks/github",
        json=PUSH_PAYLOAD,
        headers={"X-GitHub-Event": "push"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["handled"] is True
    assert data["result"]["event"] == "push"
    assert data["result"]["commits_created"] == 2

    result = await Neo4jConnection.run_query(
        "MATCH (n:Repository {id: $id}) RETURN n",
        {"id": "github-test-org-test-repo"},
    )
    assert len(result) == 1
    assert result[0]["n"]["name"] == "test-org/test-repo"

    result = await Neo4jConnection.run_query(
        "MATCH (n:Commit) RETURN n ORDER BY n.timestamp",
    )
    assert len(result) == 2

    result = await Neo4jConnection.run_query(
        """
        MATCH (c:Commit)-[:IS_IN]->(r:Repository)
        RETURN count(c) AS cnt
        """,
    )
    assert result[0]["cnt"] == 2


@pytest.mark.asyncio
async def test_github_push_idempotent(client: AsyncClient):
    response = await client.post(
        "/webhooks/github",
        json=PUSH_PAYLOAD,
        headers={"X-GitHub-Event": "push"},
    )
    assert response.status_code == 200
    assert response.json()["result"]["commits_created"] == 2

    response = await client.post(
        "/webhooks/github",
        json=PUSH_PAYLOAD,
        headers={"X-GitHub-Event": "push"},
    )
    assert response.status_code == 200
    assert response.json()["result"]["commits_created"] == 0


@pytest.mark.asyncio
async def test_github_pull_request(client: AsyncClient):
    response = await client.post(
        "/webhooks/github",
        json=PR_PAYLOAD,
        headers={"X-GitHub-Event": "pull_request"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["handled"] is True
    assert data["result"]["event"] == "pull_request"

    result = await Neo4jConnection.run_query(
        "MATCH (n:PullRequest {id: $id}) RETURN n",
        {"id": "pr-test-org-test-repo-42"},
    )
    assert len(result) == 1
    assert result[0]["n"]["title"] == "Fix production bug"


@pytest.mark.asyncio
async def test_github_release(client: AsyncClient):
    response = await client.post(
        "/webhooks/github",
        json=RELEASE_PAYLOAD,
        headers={"X-GitHub-Event": "release"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["handled"] is True
    assert data["result"]["event"] == "release"

    result = await Neo4jConnection.run_query(
        "MATCH (n:Release {id: $id}) RETURN n",
        {"id": "release-test-org-test-repo-v1.2.3"},
    )
    assert len(result) == 1
    assert result[0]["n"]["tag"] == "v1.2.3"


@pytest.mark.asyncio
async def test_github_unknown_event(client: AsyncClient):
    response = await client.post(
        "/webhooks/github",
        json={},
        headers={"X-GitHub-Event": "issues"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["handled"] is True
    assert data["result"]["handled"] is False


@pytest.mark.asyncio
async def test_github_missing_event_header(client: AsyncClient):
    response = await client.post(
        "/webhooks/github",
        json={},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["handled"] is False


@pytest.mark.asyncio
async def test_webhook_verify_endpoint(client: AsyncClient):
    response = await client.get("/webhooks/github/verify")
    assert response.status_code == 200
    data = response.json()
    assert "secret_configured" in data


@pytest.mark.asyncio
async def test_signature_verification():
    from app.adapters.webhooks.github import GitHubWebhookHandler

    handler = GitHubWebhookHandler()
    handler.secret = "my-secret"

    payload = b'{"test": "data"}'
    sig = "sha256=5df195fb0a5cec963051c2f7b85ea031ea460d81db222fb33e7af3b8f462bf4c"

    assert handler.verify_signature(payload, sig) is True
    assert handler.verify_signature(b"tampered", sig) is False
    assert handler.verify_signature(payload, None) is False


@pytest.mark.asyncio
async def test_signature_no_secret():
    from app.adapters.webhooks.github import GitHubWebhookHandler

    handler = GitHubWebhookHandler()
    handler.secret = ""

    assert handler.verify_signature(b"test", None) is True
    assert handler.verify_signature(b"test", "sha256=abc") is True
