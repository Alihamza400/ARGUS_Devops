from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.adapters.github_actions import GitHubActionsAdapter, GitHubActionsConfig
from app.adapters.github_actions.client import GitHubClient, GitHubClientError, GitHubRateLimitError
from app.adapters.github_actions.models import GitHubWorkflowRun
from app.graph.connection import Neo4jConnection
from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def ensure_neo4j():
    for attempt in range(3):
        import asyncio
        connected = await Neo4jConnection.verify_connectivity()
        if connected:
            break
        await asyncio.sleep(1)
    else:
        pytest.skip("Neo4j not available")
    yield
    await Neo4jConnection.run_query("MATCH (n) DETACH DELETE n")


SAMPLE_RUNS_RESPONSE = {
    "total_count": 3,
    "workflow_runs": [
        {
            "id": 1001,
            "name": "CI",
            "head_branch": "main",
            "head_sha": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/org/repo/actions/runs/1001",
            "run_number": 42,
            "event": "push",
            "display_title": "Add feature X",
            "created_at": "2026-07-22T10:00:00Z",
            "updated_at": "2026-07-22T10:05:00Z",
            "run_started_at": "2026-07-22T10:00:30Z",
            "actor": {"login": "alice", "id": 101},
            "run_attempt": 1,
        },
        {
            "id": 1002,
            "name": "CI",
            "head_branch": "fix/mem-leak",
            "head_sha": "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/org/repo/actions/runs/1002",
            "run_number": 43,
            "event": "pull_request",
            "display_title": "Fix memory leak",
            "created_at": "2026-07-22T11:00:00Z",
            "updated_at": "2026-07-22T11:03:00Z",
            "run_started_at": "2026-07-22T11:00:15Z",
            "actor": {"login": "bob", "id": 102},
            "run_attempt": 2,
        },
        {
            "id": 1003,
            "name": "Deploy",
            "head_branch": "main",
            "head_sha": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
            "status": "in_progress",
            "conclusion": None,
            "html_url": "https://github.com/org/repo/actions/runs/1003",
            "run_number": 44,
            "event": "push",
            "display_title": "Deploy to production",
            "created_at": "2026-07-22T12:00:00Z",
            "updated_at": "2026-07-22T12:01:00Z",
            "run_started_at": "2026-07-22T12:00:10Z",
            "actor": {"login": "alice", "id": 101},
            "run_attempt": 1,
        },
    ],
}

SAMPLE_WORKFLOWS_RESPONSE = {
    "total_count": 2,
    "workflows": [
        {"id": 201, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active", "html_url": "https://github.com/org/repo/actions/workflows/ci.yml"},
        {"id": 202, "name": "Deploy", "path": ".github/workflows/deploy.yml", "state": "active", "html_url": "https://github.com/org/repo/actions/workflows/deploy.yml"},
    ],
}


def _mock_httpx_response(status: int = 200, json_data: dict | None = None, headers: dict | None = None):
    mock = MagicMock(spec=AsyncMock)
    mock.status_code = status
    mock.json = MagicMock(return_value=json_data or {})
    mock.headers = headers or {}
    return mock


@pytest_asyncio.fixture
async def graph_with_commits():
    """Seed the graph with commits that match our workflow run SHAs."""
    sha1 = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
    sha2 = "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1"

    await Neo4jConnection.run_query(
        "CREATE (r:Repository {id: 'repo-ci-test', name: 'test-repo', url: 'https://github.com/org/repo', default_branch: 'main', provider: 'github'})"
    )
    for sha in [sha1, sha2]:
        await Neo4jConnection.run_query(
            "CREATE (c:Commit {id: $cid, sha: $sha, message: 'test commit', author: 'dev', email: 'dev@org.com', timestamp: datetime(), branch: 'main'})",
            {"cid": f"commit-{sha[:12]}", "sha": sha},
        )
        await Neo4jConnection.run_query(
            "MATCH (c:Commit {sha: $sha}), (r:Repository {id: 'repo-ci-test'}) CREATE (c)-[:IS_IN]->(r)",
            {"sha": sha},
        )

    await Neo4jConnection.run_query(
        "CREATE (d:Deployment {id: 'dep-main', name: 'main-deploy', namespace: 'main', strategy: 'RollingUpdate', revision: 1})"
    )

    yield


# ---------------------------------------------------------------------------
# Model Tests
# ---------------------------------------------------------------------------

class TestGitHubWorkflowRunModel:
    def test_from_api_full_data(self):
        data = SAMPLE_RUNS_RESPONSE["workflow_runs"][0]
        run = GitHubWorkflowRun.from_api(data)
        assert run.id == 1001
        assert run.name == "CI"
        assert run.head_branch == "main"
        assert run.head_sha == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
        assert run.status == "completed"
        assert run.conclusion == "success"
        assert run.run_number == 42
        assert run.event == "push"
        assert run.duration_seconds == 270  # 10:00:30 to 10:05:00 = 270s
        assert run.is_completed is True
        assert run.is_success is True
        assert run.is_failure is False

    def test_from_api_in_progress(self):
        data = SAMPLE_RUNS_RESPONSE["workflow_runs"][2]
        run = GitHubWorkflowRun.from_api(data)
        assert run.status == "in_progress"
        assert run.conclusion is None
        assert run.is_completed is False
        assert run.is_success is False
        assert run.is_failure is False

    def test_from_api_failure(self):
        data = SAMPLE_RUNS_RESPONSE["workflow_runs"][1]
        run = GitHubWorkflowRun.from_api(data)
        assert run.conclusion == "failure"
        assert run.is_failure is True
        assert run.run_attempt == 2

    def test_from_api_minimal_data(self):
        run = GitHubWorkflowRun.from_api({"id": 1})
        assert run.id == 1
        assert run.name == "unknown"
        assert run.status == "unknown"
        assert run.conclusion is None
        assert run.duration_seconds == 0

    def test_parse_dt_none(self):
        from app.adapters.github_actions.models import _parse_dt
        assert _parse_dt(None) is None
        assert _parse_dt("") is None

    def test_parse_dt_valid(self):
        from app.adapters.github_actions.models import _parse_dt
        dt = _parse_dt("2026-07-22T10:00:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 7


# ---------------------------------------------------------------------------
# Config Tests
# ---------------------------------------------------------------------------

class TestGitHubActionsConfig:
    def test_defaults(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo")
        assert config.repo_owner == "org"
        assert config.repo_name == "repo"
        assert config.repo_full_name == "org/repo"
        assert config.max_runs == 50
        assert config.per_page == 100
        assert config.max_retries == 3
        assert config.api_url == "https://api.github.com"

    def test_auto_generates_repo_id(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo")
        assert config.repo_id.startswith("repo-gh-")
        assert len(config.repo_id) > 8

    def test_uses_provided_repo_id(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo", repo_id="my-id")
        assert config.repo_id == "my-id"


# ---------------------------------------------------------------------------
# Client Tests
# ---------------------------------------------------------------------------

class TestGitHubClient:
    @pytest.mark.asyncio
    async def test_list_workflows(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo", token="test-token")
        client = GitHubClient(config)

        mock_resp = _mock_httpx_response(200, SAMPLE_WORKFLOWS_RESPONSE)
        with patch.object(client, "_request", AsyncMock(return_value=mock_resp)):
            workflows = await client.list_workflows()
            assert len(workflows) == 2
            assert workflows[0].name == "CI"
            assert workflows[1].name == "Deploy"

    @pytest.mark.asyncio
    async def test_get_workflow_by_name_found(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo", token="test-token")
        client = GitHubClient(config)

        mock_resp = _mock_httpx_response(200, SAMPLE_WORKFLOWS_RESPONSE)
        with patch.object(client, "_request", AsyncMock(return_value=mock_resp)):
            wf = await client.get_workflow_by_name("CI")
            assert wf is not None
            assert wf.id == 201

    @pytest.mark.asyncio
    async def test_get_workflow_by_name_not_found(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo", token="test-token")
        client = GitHubClient(config)

        mock_resp = _mock_httpx_response(200, SAMPLE_WORKFLOWS_RESPONSE)
        with patch.object(client, "_request", AsyncMock(return_value=mock_resp)):
            wf = await client.get_workflow_by_name("Nonexistent")
            assert wf is None

    @pytest.mark.asyncio
    async def test_list_workflow_runs(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo", token="test-token")
        client = GitHubClient(config)

        mock_resp = _mock_httpx_response(200, SAMPLE_RUNS_RESPONSE)
        with patch.object(client, "_request", AsyncMock(return_value=mock_resp)):
            runs = await client.list_workflow_runs()
            assert len(runs) == 3
            assert runs[0].id == 1001
            assert runs[1].id == 1002
            assert runs[2].id == 1003

    @pytest.mark.asyncio
    async def test_list_workflow_runs_by_workflow_id(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo", token="test-token")
        client = GitHubClient(config)

        mock_resp = _mock_httpx_response(200, SAMPLE_RUNS_RESPONSE)
        with patch.object(client, "_request", AsyncMock(return_value=mock_resp)):
            runs = await client.list_workflow_runs(workflow_id=201)
            assert len(runs) == 3

    @pytest.mark.asyncio
    async def test_list_runs_for_commit(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo", token="test-token")
        client = GitHubClient(config)

        mock_resp = _mock_httpx_response(200, SAMPLE_RUNS_RESPONSE)
        with patch.object(client, "_request", AsyncMock(return_value=mock_resp)):
            runs = await client.list_runs_for_commit("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0")
            assert len(runs) == 3

    @pytest.mark.asyncio
    async def test_rate_limit_error(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo", token="test-token",
                                     max_retries=0)
        client = GitHubClient(config)

        mock_resp = _mock_httpx_response(429, {}, {"X-RateLimit-Reset": "1728000000"})
        mock_http = MagicMock()
        mock_http.request = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False

        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            with pytest.raises(GitHubRateLimitError):
                await client.list_workflow_runs()

    @pytest.mark.asyncio
    async def test_authentication_error(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo", token="bad-token",
                                     max_retries=0)
        client = GitHubClient(config)

        mock_resp = _mock_httpx_response(401, {"message": "Bad credentials"})
        mock_http = MagicMock()
        mock_http.request = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False

        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            with pytest.raises(GitHubClientError) as exc:
                await client.list_workflow_runs()
            assert "Authentication" in str(exc.value)

    @pytest.mark.asyncio
    async def test_not_found_error(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo", token="test-token",
                                     max_retries=0)
        client = GitHubClient(config)

        mock_resp = _mock_httpx_response(404, {"message": "Not Found"})
        mock_http = MagicMock()
        mock_http.request = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False

        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            with pytest.raises(GitHubClientError) as exc:
                await client.list_workflow_runs()
            assert "Resource not found" in str(exc.value)

    @pytest.mark.asyncio
    async def test_timeout_retries(self):
        config = GitHubActionsConfig(repo_owner="org", repo_name="repo", token="test-token",
                                     max_retries=1, retry_backoff=0.01)
        client = GitHubClient(config)
        client._pages_consumed = 0

        resp_ok = _mock_httpx_response(200, SAMPLE_RUNS_RESPONSE)
        resp_ok.raise_for_status = MagicMock()

        mock_http = MagicMock()
        mock_http.request = AsyncMock(side_effect=[
            __import__('httpx').TimeoutException("timeout"),
            resp_ok,
        ])
        mock_http.is_closed = False

        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http)):
            runs = await client.list_workflow_runs()
            assert len(runs) == 3
            assert mock_http.request.call_count == 2


# ---------------------------------------------------------------------------
# Adapter Tests
# ---------------------------------------------------------------------------

class TestGitHubActionsAdapter:
    @pytest.mark.asyncio
    async def test_sync_creates_pipeline_runs(self, graph_with_commits):
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="test-token", max_runs=50,
        )
        adapter = GitHubActionsAdapter(config)

        with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(
            return_value=[GitHubWorkflowRun.from_api(r) for r in SAMPLE_RUNS_RESPONSE["workflow_runs"]]
        )):
            result = await adapter.sync()

        assert result["runs_fetched"] == 3
        assert result["pipeline_runs_created"] == 3
        assert result["commit_links_created"] >= 2
        assert result["errors"] == []

        # Verify nodes in graph
        pr_nodes = await Neo4jConnection.run_query(
            "MATCH (n:PipelineRun) RETURN n.id AS id, n.workflow AS wf, n.status AS status ORDER BY n.id"
        )
        assert len(pr_nodes) == 3

        ids = {n["id"] for n in pr_nodes}
        assert "gh-run-1001" in ids
        assert "gh-run-1002" in ids
        assert "gh-run-1003" in ids

        workflows = {n["wf"] for n in pr_nodes}
        assert "CI" in workflows
        assert "Deploy" in workflows

    @pytest.mark.asyncio
    async def test_sync_creates_triggered_edges(self, graph_with_commits):
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="test-token",
        )
        adapter = GitHubActionsAdapter(config)

        with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(
            return_value=[GitHubWorkflowRun.from_api(r) for r in SAMPLE_RUNS_RESPONSE["workflow_runs"]]
        )):
            await adapter.sync()

        # Verify TRIGGERED edges exist
        edges = await Neo4jConnection.run_query(
            "MATCH (c:Commit)-[r:TRIGGERED]->(pr:PipelineRun) RETURN c.sha AS sha, pr.id AS run_id"
        )
        assert len(edges) >= 2

    @pytest.mark.asyncio
    async def test_sync_creates_produces_edge_for_successful_run(self, graph_with_commits):
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="test-token",
        )
        adapter = GitHubActionsAdapter(config)

        with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(
            return_value=[GitHubWorkflowRun.from_api(SAMPLE_RUNS_RESPONSE["workflow_runs"][0])]
        )):
            result = await adapter.sync()

        assert result["deployment_links_created"] >= 1

        edges = await Neo4jConnection.run_query(
            "MATCH (pr:PipelineRun)-[r:PRODUCES]->(d:Deployment) RETURN pr.id AS pr_id, d.id AS dep_id"
        )
        assert len(edges) >= 1

    @pytest.mark.asyncio
    async def test_idempotent(self, graph_with_commits):
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="test-token",
        )
        adapter = GitHubActionsAdapter(config)

        runs = [GitHubWorkflowRun.from_api(r) for r in SAMPLE_RUNS_RESPONSE["workflow_runs"]]

        with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(return_value=runs)):
            r1 = await adapter.sync()
            r2 = await adapter.sync()

        assert r1["pipeline_runs_created"] == 3
        assert r2["pipeline_runs_created"] == 0
        assert r2["commit_links_created"] == 0
        assert r2["deployment_links_created"] == 0

    @pytest.mark.asyncio
    async def test_sync_with_filter_by_workflow(self, graph_with_commits):
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="test-token",
            workflow_name="CI",
        )
        adapter = GitHubActionsAdapter(config)

        with patch.object(GitHubClient, "get_workflow_by_name", AsyncMock(
            return_value=MagicMock(id=201)
        )):
            with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(
                return_value=[GitHubWorkflowRun.from_api(SAMPLE_RUNS_RESPONSE["workflow_runs"][0])]
            )):
                result = await adapter.sync()

        assert result["pipeline_runs_created"] >= 1

    @pytest.mark.asyncio
    async def test_sync_handles_rate_limit(self, graph_with_commits):
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="test-token",
        )
        adapter = GitHubActionsAdapter(config)

        with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(
            side_effect=GitHubRateLimitError(reset_at=1728000000)
        )):
            result = await adapter.sync()

        assert len(result["errors"]) == 1
        assert "rate limited" in result["errors"][0].lower()

    @pytest.mark.asyncio
    async def test_sync_handles_authentication_error(self, graph_with_commits):
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="bad-token",
        )
        adapter = GitHubActionsAdapter(config)

        with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(
            side_effect=GitHubClientError("Authentication failed", status=401)
        )):
            result = await adapter.sync()

        assert len(result["errors"]) == 1

    @pytest.mark.asyncio
    async def test_sync_no_commits_in_graph(self):
        """If no commits match the run SHAs, edges should not be created."""
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="test-token",
        )
        adapter = GitHubActionsAdapter(config)

        with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(
            return_value=[GitHubWorkflowRun.from_api(SAMPLE_RUNS_RESPONSE["workflow_runs"][0])]
        )):
            result = await adapter.sync()

        assert result["pipeline_runs_created"] == 1
        assert result["commit_links_created"] == 0

    @pytest.mark.asyncio
    async def test_sync_empty_response(self, graph_with_commits):
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="test-token",
        )
        adapter = GitHubActionsAdapter(config)

        with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(return_value=[])):
            result = await adapter.sync()

        assert result["runs_fetched"] == 0
        assert result["pipeline_runs_created"] == 0
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_sync_stores_run_metadata(self, graph_with_commits):
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="test-token",
        )
        adapter = GitHubActionsAdapter(config)

        with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(
            return_value=[GitHubWorkflowRun.from_api(SAMPLE_RUNS_RESPONSE["workflow_runs"][0])]
        )):
            await adapter.sync()

        nodes = await Neo4jConnection.run_query(
            "MATCH (n:PipelineRun {id: 'gh-run-1001'}) RETURN n.workflow AS wf, n.status AS status, n.trigger AS trigger, n.duration_seconds AS dur"
        )
        assert len(nodes) == 1
        assert nodes[0]["wf"] == "CI"
        assert nodes[0]["status"] == "success"
        assert nodes[0]["trigger"] == "push"
        assert nodes[0]["dur"] == 270


# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_api_sync_github_endpoint(client: AsyncClient, graph_with_commits):
    with patch("app.adapters.github_actions.adapter.GitHubClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.list_workflow_runs = AsyncMock(
            return_value=[GitHubWorkflowRun.from_api(SAMPLE_RUNS_RESPONSE["workflow_runs"][0])]
        )
        MockClient.return_value = mock_instance

        response = await client.post(
            "/graph/sync/github",
            params={
                "repo_owner": "org",
                "repo_name": "repo",
                "token": "test-token",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["adapter"] == "github_actions"
        assert data["pipeline_runs_created"] >= 1


@pytest.mark.asyncio
async def test_api_sync_github_endpoint_no_token_still_succeeds(client: AsyncClient):
    response = await client.post(
        "/graph/sync/github",
        params={"repo_owner": "org", "repo_name": "repo"},
    )
    assert response.status_code == 200
    data = response.json()
    # Without token, the adapter may return errors but should not crash
    assert "adapter" in data


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_run_with_partial_data(self, graph_with_commits):
        """A run with minimal fields should still create a PipelineRun node."""
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="test-token",
        )
        adapter = GitHubActionsAdapter(config)

        minimal_run = GitHubWorkflowRun.from_api({
            "id": 9999,
            "name": "Minimal",
            "head_branch": "main",
            "head_sha": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
            "status": "completed",
            "conclusion": "success",
            "html_url": "",
            "run_number": 1,
            "event": "push",
            "display_title": "",
            "created_at": "2026-07-22T10:00:00Z",
            "updated_at": "2026-07-22T10:01:00Z",
        })

        with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(return_value=[minimal_run])):
            result = await adapter.sync()

        assert result["pipeline_runs_created"] == 1

    @pytest.mark.asyncio
    async def test_run_no_sha_skips_commit_link(self, graph_with_commits):
        """A run without a head_sha should not attempt to link to a commit."""
        config = GitHubActionsConfig(
            repo_owner="org", repo_name="repo", token="test-token",
        )
        adapter = GitHubActionsAdapter(config)

        no_sha_run = GitHubWorkflowRun.from_api({
            "id": 8888,
            "name": "NoSha",
            "head_branch": "main",
            "head_sha": "",
            "status": "completed",
            "conclusion": "success",
            "html_url": "",
            "run_number": 1,
            "event": "push",
            "display_title": "",
            "created_at": "2026-07-22T10:00:00Z",
            "updated_at": "2026-07-22T10:01:00Z",
        })

        with patch.object(GitHubClient, "list_workflow_runs", AsyncMock(return_value=[no_sha_run])):
            result = await adapter.sync()

        assert result["pipeline_runs_created"] == 1
        assert result["commit_links_created"] == 0
