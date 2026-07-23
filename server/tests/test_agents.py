from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.agents.coordinator import AgentCoordinator
from app.agents.incident import IncidentAgent
from app.agents.models import AgentQuery, ProposalAction, Severity
from app.agents.proposal import ProposalAgent
from app.agents.queries import GraphQueries
from app.graph.connection import Neo4jConnection
from app.main import app


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


@pytest_asyncio.fixture
async def populated_graph():
    """Seed the graph with a realistic multi-cluster environment."""
    # Cluster
    await Neo4jConnection.run_query(
        "CREATE (c:Cluster {id: 'cluster-prod', name: 'production', version: 'v1.29.0', provider: 'kubernetes'})"
    )
    # Namespaces
    await Neo4jConnection.run_query(
        "CREATE (ns:Namespace {id: 'ns-api', name: 'api', labels: \"{'env': 'prod', 'team': 'backend'}\"})"
    )
    await Neo4jConnection.run_query(
        "CREATE (ns2:Namespace {id: 'ns-web', name: 'web', labels: \"{'env': 'prod', 'team': 'frontend'}\"})"
    )
    # Namespace -> Cluster edges
    await Neo4jConnection.run_query(
        "MATCH (ns:Namespace {id: 'ns-api'}), (c:Cluster {id: 'cluster-prod'}) CREATE (ns)-[:IN]->(c)"
    )
    await Neo4jConnection.run_query(
        "MATCH (ns:Namespace {id: 'ns-web'}), (c:Cluster {id: 'cluster-prod'}) CREATE (ns)-[:IN]->(c)"
    )

    # Repository
    await Neo4jConnection.run_query(
        "CREATE (r:Repository {id: 'repo-api-1', name: 'api-service', url: 'https://github.com/org/api-service', default_branch: 'main', provider: 'github'})"
    )

    # Commits
    commits = [
        ("commit-abc1", "a1b2c3d4e5f6", "Initial API scaffold", "Alice", "alice@org.com", "2026-07-20T10:00:00", "main"),
        ("commit-abc2", "b2c3d4e5f6a7", "Add request handler", "Alice", "alice@org.com", "2026-07-20T11:00:00", "main"),
        ("commit-abc3", "c3d4e5f6a7b8", "Containerize service", "Bob", "bob@org.com", "2026-07-21T09:00:00", "main"),
        ("commit-abc4", "d4e5f6a7b8c9", "Fix memory leak in handler", "Alice", "alice@org.com", "2026-07-21T14:00:00", "fix/memory-leak"),
        ("commit-abc5", "e5f6a7b8c9d0", "Bump dependencies", "Charlie", "charlie@org.com", "2026-07-22T08:00:00", "main"),
    ]
    for cid, sha, msg, author, email, ts, branch in commits:
        await Neo4jConnection.run_query(
            "CREATE (c:Commit {id: $id, sha: $sha, message: $msg, author: $author, email: $email, timestamp: $ts, branch: $branch})",
            {"id": cid, "sha": sha, "msg": msg, "author": author, "email": email, "ts": ts, "branch": branch},
        )
        await Neo4jConnection.run_query(
            "MATCH (c:Commit {id: $cid}), (r:Repository {id: 'repo-api-1'}) CREATE (c)-[:IS_IN]->(r)",
            {"cid": cid},
        )

    # Service
    await Neo4jConnection.run_query(
        "CREATE (s:Service {id: 'svc-api-gateway', name: 'api-gateway', namespace: 'api', image: 'org/api-gateway:v2.1.0', replicas: 3})"
    )
    # Service -> Namespace
    await Neo4jConnection.run_query(
        "MATCH (s:Service {id: 'svc-api-gateway'}), (ns:Namespace {id: 'ns-api'}) CREATE (s)-[:IN]->(ns)"
    )
    # Service -> Repository (DEPLOYED_FROM)
    await Neo4jConnection.run_query(
        "MATCH (s:Service {id: 'svc-api-gateway'}), (r:Repository {id: 'repo-api-1'}) CREATE (s)-[:DEPLOYED_FROM {since: '2026-07-22T08:30:00', version: 'v2.1.0'}]->(r)"
    )

    # Healthy pod
    await Neo4jConnection.run_query(
        "CREATE (p:Pod {id: 'pod-api-healthy', name: 'api-gateway-7d4b8f9f6c-abc01', phase: 'Running', node: 'worker-1', namespace: 'api', cpu_request: '250m', cpu_limit: '500m', memory_request: '256Mi', memory_limit: '512Mi'})"
    )
    await Neo4jConnection.run_query(
        "MATCH (p:Pod {id: 'pod-api-healthy'}), (ns:Namespace {id: 'ns-api'}) CREATE (p)-[:IN]->(ns)"
    )
    await Neo4jConnection.run_query(
        "MATCH (p:Pod {id: 'pod-api-healthy'}), (c:Cluster {id: 'cluster-prod'}) CREATE (p)-[:RUNS_ON]->(c)"
    )
    await Neo4jConnection.run_query(
        "MATCH (p:Pod {id: 'pod-api-healthy'}), (s:Service {id: 'svc-api-gateway'}) CREATE (p)-[:BELONGS_TO]->(s)"
    )

    # CrashLoopBackOff pod
    await Neo4jConnection.run_query(
        "CREATE (p2:Pod {id: 'pod-api-crash', name: 'api-gateway-7d4b8f9f6c-xyz99', phase: 'CrashLoopBackOff', node: 'worker-2', namespace: 'api', cpu_request: '500m', cpu_limit: '1000m', memory_request: '512Mi', memory_limit: '1Gi'})"
    )
    await Neo4jConnection.run_query(
        "MATCH (p:Pod {id: 'pod-api-crash'}), (ns:Namespace {id: 'ns-api'}) CREATE (p)-[:IN]->(ns)"
    )
    await Neo4jConnection.run_query(
        "MATCH (p:Pod {id: 'pod-api-crash'}), (c:Cluster {id: 'cluster-prod'}) CREATE (p)-[:RUNS_ON]->(c)"
    )
    await Neo4jConnection.run_query(
        "MATCH (p:Pod {id: 'pod-api-crash'}), (s:Service {id: 'svc-api-gateway'}) CREATE (p)-[:BELONGS_TO]->(s)"
    )

    # OOMKilled pod (different service)
    await Neo4jConnection.run_query(
        "CREATE (s2:Service {id: 'svc-web-frontend', name: 'web-frontend', namespace: 'web', image: 'org/web-frontend:v3.0.0', replicas: 2})"
    )
    await Neo4jConnection.run_query(
        "MATCH (s:Service {id: 'svc-web-frontend'}), (ns:Namespace {id: 'ns-web'}) CREATE (s)-[:IN]->(ns)"
    )
    await Neo4jConnection.run_query(
        "CREATE (p3:Pod {id: 'pod-web-oom', name: 'web-frontend-9a8b7c6d-ooo00', phase: 'OOMKilling', node: 'worker-3', namespace: 'web', cpu_request: '100m', cpu_limit: '200m', memory_request: '128Mi', memory_limit: '256Mi'})"
    )
    await Neo4jConnection.run_query(
        "MATCH (p:Pod {id: 'pod-web-oom'}), (ns:Namespace {id: 'ns-web'}) CREATE (p)-[:IN]->(ns)"
    )
    await Neo4jConnection.run_query(
        "MATCH (p:Pod {id: 'pod-web-oom'}), (c:Cluster {id: 'cluster-prod'}) CREATE (p)-[:RUNS_ON]->(c)"
    )
    await Neo4jConnection.run_query(
        "MATCH (p:Pod {id: 'pod-web-oom'}), (s:Service {id: 'svc-web-frontend'}) CREATE (p)-[:BELONGS_TO]->(s)"
    )

    yield


# =========================================================================
# Query Layer Tests
# =========================================================================


@pytest.mark.asyncio
async def test_queries_find_pod_by_id(populated_graph):
    pod = await GraphQueries.find_pod_by_id("pod-api-crash")
    assert pod is not None
    assert pod.name == "api-gateway-7d4b8f9f6c-xyz99"
    assert pod.phase == "CrashLoopBackOff"
    assert pod.namespace == "api"


@pytest.mark.asyncio
async def test_queries_find_pod_by_name(populated_graph):
    pods = await GraphQueries.find_pods_by_name("api-gateway")
    assert len(pods) >= 2


@pytest.mark.asyncio
async def test_queries_find_pods_by_phase(populated_graph):
    pods = await GraphQueries.find_pods_by_phase("CrashLoopBackOff")
    assert len(pods) >= 1
    assert pods[0].phase == "CrashLoopBackOff"


@pytest.mark.asyncio
async def test_queries_get_service_for_pod(populated_graph):
    svc = await GraphQueries.get_service_for_pod("pod-api-crash")
    assert svc is not None
    assert svc.name == "api-gateway"


@pytest.mark.asyncio
async def test_queries_get_repo_for_service(populated_graph):
    repo = await GraphQueries.get_repo_for_service("svc-api-gateway")
    assert repo is not None
    assert repo.name == "api-service"


@pytest.mark.asyncio
async def test_queries_get_commits_for_repo(populated_graph):
    commits = await GraphQueries.get_commits_for_repo("repo-api-1", max_count=10)
    assert len(commits) == 5
    assert commits[0].author == "Charlie"


@pytest.mark.asyncio
async def test_queries_get_provenance_chain(populated_graph):
    chain = await GraphQueries.get_provenance_chain("pod-api-crash")
    assert chain["pod"] is not None
    assert chain["pod"].phase == "CrashLoopBackOff"
    assert chain["service"] is not None
    assert chain["service"].name == "api-gateway"
    assert chain["repository"] is not None
    assert chain["repository"].name == "api-service"
    assert len(chain["commits"]) == 5


@pytest.mark.asyncio
async def test_queries_get_unhealthy_pods(populated_graph):
    pods = await GraphQueries.get_unhealthy_pods()
    assert len(pods) >= 2
    phases = {p.phase for p in pods}
    assert "CrashLoopBackOff" in phases
    assert "OOMKilling" in phases


@pytest.mark.asyncio
async def test_queries_search_pods(populated_graph):
    pods = await GraphQueries.search_pods("crash")
    assert len(pods) >= 1
    assert any("CrashLoopBackOff" in p.phase for p in pods)


@pytest.mark.asyncio
async def test_queries_pod_not_found(populated_graph):
    pod = await GraphQueries.find_pod_by_id("nonexistent")
    assert pod is None


# =========================================================================
# IncidentAgent Tests
# =========================================================================


@pytest.mark.asyncio
async def test_incident_agent_analyzes_crashloopbackoff(populated_graph):
    agent = IncidentAgent()
    query = AgentQuery(
        query="Analyze crashloop pod",
        pod_id="pod-api-crash",
        include_timeline=True,
        max_commits=20,
    )
    result = await agent.analyze(query)

    assert result.severity == Severity.CRITICAL
    assert result.confidence >= 0.8
    assert len(result.evidence) >= 5
    assert any("CrashLoopBackOff" in str(e.detail) for e in result.evidence)
    assert len(result.timeline) >= 1
    assert len(result.suggestions) >= 1


@pytest.mark.asyncio
async def test_incident_agent_healthy_pod(populated_graph):
    agent = IncidentAgent()
    query = AgentQuery(
        query="Check healthy pod",
        pod_id="pod-api-healthy",
        include_timeline=True,
    )
    result = await agent.analyze(query)

    assert result.severity == Severity.MEDIUM
    assert result.confidence >= 0.8


@pytest.mark.asyncio
async def test_incident_agent_oom_pod(populated_graph):
    agent = IncidentAgent()
    query = AgentQuery(
        query="Check OOM pod",
        pod_id="pod-web-oom",
        include_timeline=True,
    )
    result = await agent.analyze(query)

    assert result.severity == Severity.CRITICAL
    assert result.confidence >= 0.5
    oom_evidence = [e for e in result.evidence if "OOMKilling" in str(e.detail)]
    assert len(oom_evidence) >= 1


@pytest.mark.asyncio
async def test_incident_agent_pod_not_found(populated_graph):
    agent = IncidentAgent()
    query = AgentQuery(
        query="Check nonexistent pod",
        pod_id="pod-does-not-exist",
    )
    result = await agent.analyze(query)

    assert result.severity == Severity.INFO
    assert result.confidence == 1.0
    assert "not found" in result.summary.lower()


@pytest.mark.asyncio
async def test_incident_agent_search_by_name(populated_graph):
    agent = IncidentAgent()
    query = AgentQuery(
        query="Find api pods",
        pod_name="api-gateway",
        include_timeline=True,
    )
    result = await agent.analyze(query)

    assert result.severity is not None
    assert len(result.evidence) >= 3
    assert any("api-gateway" in str(e.detail) for e in result.evidence)


# =========================================================================
# ProposalAgent Tests
# =========================================================================


@pytest.mark.asyncio
async def test_proposal_agent_generates_rollback(populated_graph):
    incident = IncidentAgent()
    query = AgentQuery(
        query="Generate rollback for crashloop",
        pod_id="pod-api-crash",
        max_commits=10,
    )
    analysis = await incident.analyze(query)

    proposal_agent = ProposalAgent()
    proposal = await proposal_agent.propose(analysis)

    assert proposal is not None
    assert proposal.action == ProposalAction.ROLLBACK
    assert len(proposal.evidence) >= 1
    assert proposal.pr_body is not None
    assert "Rollback" in proposal.title or "rollback" in proposal.title.lower()
    assert "Argus" in proposal.title


@pytest.mark.asyncio
async def test_proposal_agent_generates_pr_body(populated_graph):
    incident = IncidentAgent()
    analysis = await incident.analyze(AgentQuery(query="test", pod_id="pod-api-crash"))

    proposal_agent = ProposalAgent()
    proposal = await proposal_agent.propose(analysis)

    assert proposal is not None
    assert "## Automated Rollback Proposal" in proposal.pr_body
    assert "### Evidence" in proposal.pr_body
    assert "### Proposed Action" in proposal.pr_body
    assert "### Risk Assessment" in proposal.pr_body


@pytest.mark.asyncio
async def test_proposal_agent_no_commits_fallback(populated_graph):
    analysis = await IncidentAgent().analyze(
        AgentQuery(query="test empty", pod_id="pod-api-crash", max_commits=0)
    )
    analysis.evidence = [e for e in analysis.evidence if e.category.value != "commit_change"]
    analysis.evidence = analysis.evidence[:2]

    proposal_agent = ProposalAgent()
    proposal = await proposal_agent.propose(analysis)

    assert proposal is not None
    assert proposal.action == ProposalAction.MANUAL_INTERVENTION
    assert "Manual" in proposal.title


# =========================================================================
# Coordinator Tests
# =========================================================================


@pytest.mark.asyncio
async def test_coordinator_list_agents():
    agents = AgentCoordinator.list_agents()
    assert len(agents) >= 2
    types = {a["type"] for a in agents}
    assert "incident" in types
    assert "proposal" in types


@pytest.mark.asyncio
async def test_coordinator_analyze_without_proposal(populated_graph):
    query = AgentQuery(
        query="test",
        pod_id="pod-api-crash",
        generate_proposal=False,
    )
    response = await AgentCoordinator.analyze(query)

    assert response.analysis is not None
    assert response.proposal is None
    assert len(response.errors) == 0


@pytest.mark.asyncio
async def test_coordinator_analyze_with_proposal(populated_graph):
    query = AgentQuery(
        query="test with proposal",
        pod_id="pod-api-crash",
        generate_proposal=True,
    )
    response = await AgentCoordinator.analyze(query)

    assert response.analysis is not None
    assert response.proposal is not None
    assert response.proposal.pr_body is not None


# =========================================================================
# API Endpoint Tests
# =========================================================================

@pytest.mark.asyncio
async def test_api_list_agents(client: AsyncClient, auth_headers: dict):
    response = await client.get("/agent/agents", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["agents"]) >= 2


@pytest.mark.asyncio
async def test_api_analyze_pod(client: AsyncClient, auth_headers: dict, populated_graph):
    response = await client.get("/agent/analyze/pod-api-crash", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["analysis"]["severity"] in ("critical", "high", "medium", "low", "info")
    assert len(data["analysis"]["evidence"]) >= 3


@pytest.mark.asyncio
async def test_api_analyze_pod_with_proposal(client: AsyncClient, auth_headers: dict, populated_graph):
    response = await client.get("/agent/analyze/pod-api-crash?generate_proposal=true", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["proposal"] is not None
    assert data["proposal"]["pr_body"] is not None


@pytest.mark.asyncio
async def test_api_analyze_post(client: AsyncClient, auth_headers: dict, populated_graph):
    response = await client.post(
        "/agent/analyze",
        headers=auth_headers,
        json={
            "query": "Analyze crashloop",
            "pod_id": "pod-api-crash",
            "generate_proposal": True,
            "max_commits": 10,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["analysis"] is not None
    assert data["proposal"] is not None


@pytest.mark.asyncio
async def test_api_analyze_no_pod_id(client: AsyncClient, auth_headers: dict):
    response = await client.post(
        "/agent/analyze",
        headers=auth_headers,
        json={"query": "empty"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_api_list_unhealthy(client: AsyncClient, auth_headers: dict, populated_graph):
    response = await client.get("/agent/unhealthy", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 2
    assert any(p["phase"] == "CrashLoopBackOff" for p in data["pods"])
    assert any(p["phase"] == "OOMKilling" for p in data["pods"])


# =========================================================================
# Edge Cases
# =========================================================================


@pytest.mark.asyncio
async def test_queries_pods_for_service(populated_graph):
    pods = await GraphQueries.get_pods_for_service("svc-api-gateway")
    assert len(pods) == 2
    phases = {p.phase for p in pods}
    assert "Running" in phases
    assert "CrashLoopBackOff" in phases


@pytest.mark.asyncio
async def test_queries_deployments_for_service(populated_graph):
    deps = await GraphQueries.get_deployments_for_service("svc-api-gateway")
    assert len(deps) == 0


@pytest.mark.asyncio
async def test_incident_agent_suggestions(populated_graph):
    result = await IncidentAgent().analyze(
        AgentQuery(query="test", pod_id="pod-api-crash")
    )
    assert len(result.suggestions) >= 1
    assert any("commit" in s.lower() for s in result.suggestions)
