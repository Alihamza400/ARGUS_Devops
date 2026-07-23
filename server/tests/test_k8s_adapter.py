from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from app.adapters.kubernetes import K8sAdapter, K8sAdapterConfig
from app.graph.connection import Neo4jConnection


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


def _make_mock_v1():
    v1 = MagicMock()

    v1.get_code.return_value.git_version = "v1.28.0"

    mock_ns = MagicMock()
    mock_ns.metadata.name = "default"
    mock_ns.metadata.labels = {"env": "prod"}
    v1.list_namespace.return_value.items = [mock_ns]

    mock_svc = MagicMock()
    mock_svc.metadata.name = "nginx"
    mock_svc.metadata.namespace = "default"
    mock_svc.spec.selector = {"app": "nginx"}
    v1.list_service_for_all_namespaces.return_value.items = [mock_svc]

    mock_pod = MagicMock()
    mock_pod.metadata.name = "nginx-7d4b8f9f6c-abc12"
    mock_pod.metadata.namespace = "default"
    owner_ref = MagicMock()
    owner_ref.kind = "ReplicaSet"
    owner_ref.name = "nginx-7d4b8f9f6c"
    mock_pod.metadata.owner_references = [owner_ref]
    mock_pod.status.phase = "Running"
    mock_pod.spec.node_name = "worker-1"

    c = MagicMock()
    c.resources.requests = {"cpu": "100m", "memory": "128Mi"}
    c.resources.limits = {"cpu": "200m", "memory": "256Mi"}
    mock_pod.spec.containers = [c]

    v1.list_pod_for_all_namespaces.return_value.items = [mock_pod]

    return v1


def _make_mock_apps_v1():
    apps_v1 = MagicMock()

    mock_dep = MagicMock()
    mock_dep.metadata.name = "nginx"
    mock_dep.metadata.namespace = "default"
    mock_dep.spec.strategy.type = "RollingUpdate"
    mock_dep.spec.selector.match_labels = {"app": "nginx"}
    apps_v1.list_deployment_for_all_namespaces.return_value.items = [mock_dep]

    return apps_v1


def _make_mock_config():
    return MagicMock()


@patch("kubernetes.config")
@patch("kubernetes.client")
@pytest.mark.asyncio
async def test_k8s_adapter_syncs_cluster(mock_client, mock_config):
    mock_v1 = _make_mock_v1()
    mock_client.CoreV1Api.return_value = mock_v1

    config = K8sAdapterConfig(
        cluster_name="test-cluster",
        cluster_id="cluster-test-001",
        sync_namespaces=False,
        sync_services=False,
        sync_deployments=False,
        sync_pods=False,
    )
    adapter = K8sAdapter(config)
    result = await adapter.sync()

    assert result["cluster_id"] == "cluster-test-001"
    assert result["nodes_created"] == 1
    assert result["edges_created"] == 0

    nodes = await Neo4jConnection.run_query(
        "MATCH (n:Cluster {id: $id}) RETURN n",
        {"id": "cluster-test-001"},
    )
    assert len(nodes) == 1
    assert nodes[0]["n"]["name"] == "test-cluster"
    assert nodes[0]["n"]["provider"] == "kubernetes"


@patch("kubernetes.config")
@patch("kubernetes.client")
@pytest.mark.asyncio
async def test_k8s_adapter_syncs_namespace(mock_client, mock_config):
    mock_v1 = _make_mock_v1()
    mock_client.CoreV1Api.return_value = mock_v1

    config = K8sAdapterConfig(
        cluster_name="test-cluster",
        cluster_id="cluster-test-002",
        sync_services=False,
        sync_deployments=False,
        sync_pods=False,
    )
    adapter = K8sAdapter(config)
    result = await adapter.sync()

    assert result["nodes_created"] >= 1
    assert result["edges_created"] >= 1

    ns_nodes = await Neo4jConnection.run_query(
        "MATCH (n:Namespace {id: $id}) RETURN n",
        {"id": "ns-default"},
    )
    assert len(ns_nodes) == 1
    assert ns_nodes[0]["n"]["name"] == "default"

    edge = await Neo4jConnection.run_query(
        """
        MATCH (ns:Namespace {id: 'ns-default'})-[r:IN]->(c:Cluster {id: 'cluster-test-002'})
        RETURN r
        """,
    )
    assert len(edge) == 1


@patch("kubernetes.config")
@patch("kubernetes.client")
@pytest.mark.asyncio
async def test_k8s_adapter_syncs_service(mock_client, mock_config):
    mock_v1 = _make_mock_v1()
    mock_client.CoreV1Api.return_value = mock_v1

    config = K8sAdapterConfig(
        cluster_name="test-cluster",
        cluster_id="cluster-test-003",
        sync_namespaces=False,
        sync_deployments=False,
        sync_pods=False,
    )
    adapter = K8sAdapter(config)
    result = await adapter.sync()

    assert result["nodes_created"] >= 1
    assert result["edges_created"] >= 1

    svc_nodes = await Neo4jConnection.run_query(
        "MATCH (n:Service {id: $id}) RETURN n",
        {"id": "svc-default-nginx"},
    )
    assert len(svc_nodes) == 1
    assert svc_nodes[0]["n"]["name"] == "nginx"
    assert svc_nodes[0]["n"]["namespace"] == "default"


@patch("kubernetes.config")
@patch("kubernetes.client")
@pytest.mark.asyncio
async def test_k8s_adapter_syncs_deployment(mock_client, mock_config):
    mock_v1 = _make_mock_v1()
    mock_apps_v1 = _make_mock_apps_v1()
    mock_client.CoreV1Api.return_value = mock_v1
    mock_client.AppsV1Api.return_value = mock_apps_v1

    config = K8sAdapterConfig(
        cluster_name="test-cluster",
        cluster_id="cluster-test-004",
        sync_namespaces=False,
        sync_services=False,
        sync_pods=False,
    )
    adapter = K8sAdapter(config)
    result = await adapter.sync()

    assert result["nodes_created"] >= 1

    dep_nodes = await Neo4jConnection.run_query(
        "MATCH (n:Deployment {id: $id}) RETURN n",
        {"id": "dep-default-nginx"},
    )
    assert len(dep_nodes) == 1
    assert dep_nodes[0]["n"]["name"] == "nginx"
    assert dep_nodes[0]["n"]["strategy"] == "RollingUpdate"


@patch("kubernetes.config")
@patch("kubernetes.client")
@pytest.mark.asyncio
async def test_k8s_adapter_syncs_pod(mock_client, mock_config):
    mock_v1 = _make_mock_v1()
    mock_client.CoreV1Api.return_value = mock_v1

    config = K8sAdapterConfig(
        cluster_name="test-cluster",
        cluster_id="cluster-test-005",
        sync_namespaces=False,
        sync_services=False,
        sync_deployments=False,
        sync_pods=True,
    )
    adapter = K8sAdapter(config)
    result = await adapter.sync()

    assert result["nodes_created"] >= 1
    assert result["edges_created"] >= 1

    pod_nodes = await Neo4jConnection.run_query(
        "MATCH (n:Pod {id: $id}) RETURN n",
        {"id": "pod-default-nginx-7d4b8f9f6c-abc12"},
    )
    assert len(pod_nodes) == 1
    assert pod_nodes[0]["n"]["name"] == "nginx-7d4b8f9f6c-abc12"
    assert pod_nodes[0]["n"]["phase"] == "Running"
    assert pod_nodes[0]["n"]["cpu_request"] == "100m"
    assert pod_nodes[0]["n"]["memory_limit"] == "256Mi"


@patch("kubernetes.config")
@patch("kubernetes.client")
@pytest.mark.asyncio
async def test_k8s_adapter_full_sync(mock_client, mock_config):
    mock_v1 = _make_mock_v1()
    mock_apps_v1 = _make_mock_apps_v1()
    mock_client.CoreV1Api.return_value = mock_v1
    mock_client.AppsV1Api.return_value = mock_apps_v1

    config = K8sAdapterConfig(
        cluster_name="full-test",
        cluster_id="cluster-test-006",
    )
    adapter = K8sAdapter(config)
    result = await adapter.sync()

    assert result["nodes_created"] >= 4
    assert result["edges_created"] >= 3

    node_count = await Neo4jConnection.run_query(
        "MATCH (n) RETURN count(n) AS cnt",
    )
    assert node_count[0]["cnt"] >= 4

    edge_count = await Neo4jConnection.run_query(
        "MATCH ()-[r]->() RETURN count(r) AS cnt",
    )
    assert edge_count[0]["cnt"] >= 3


@patch("kubernetes.config")
@patch("kubernetes.client")
@pytest.mark.asyncio
async def test_k8s_adapter_idempotent(mock_client, mock_config):
    mock_v1 = _make_mock_v1()
    mock_apps_v1 = _make_mock_apps_v1()
    mock_client.CoreV1Api.return_value = mock_v1
    mock_client.AppsV1Api.return_value = mock_apps_v1

    config = K8sAdapterConfig(
        cluster_name="idempotent-test",
        cluster_id="cluster-test-007",
    )
    adapter = K8sAdapter(config)
    result1 = await adapter.sync()
    result2 = await adapter.sync()

    assert result1["nodes_created"] >= 4
    assert result2["nodes_created"] == 0
    assert result2["edges_created"] == 0


@pytest.mark.asyncio
async def test_k8s_adapter_error_no_kubernetes_package():
    config = K8sAdapterConfig(
        cluster_name="no-k8s",
        cluster_id="cluster-test-008",
    )
    adapter = K8sAdapter(config)

    with patch.dict("sys.modules", {"kubernetes": None}):
        result = await adapter.sync()

    assert len(result["errors"]) >= 1
    assert "kubernetes package not installed" in result["errors"][0]
