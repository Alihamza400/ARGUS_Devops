from __future__ import annotations

import time

from prometheus_client import Counter, Gauge, Histogram, generate_latest

from app.graph.connection import Neo4jConnection

# Request metrics
request_count = Counter(
    "argus_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
request_duration = Histogram(
    "argus_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# Neo4j metrics
neo4j_up = Gauge("argus_neo4j_up", "Neo4j connection status (1 = connected, 0 = disconnected)")
neo4j_query_duration = Histogram(
    "argus_neo4j_query_duration_seconds",
    "Neo4j query duration in seconds",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)

# Graph metrics
graph_node_count = Gauge("argus_graph_nodes_total", "Total number of nodes in the graph", ["type"])
graph_edge_count = Gauge("argus_graph_edges_total", "Total number of edges in the graph", ["type"])

# Business metrics
incident_count = Gauge("argus_incidents_total", "Total number of incidents", ["status"])
proposal_count = Gauge("argus_proposals_total", "Total number of proposals", ["status"])
enforcement_count = Gauge("argus_enforcements_total", "Total number of enforcements", ["status"])
pipeline_run_count = Gauge("argus_pipeline_runs_total", "Total number of pipeline runs", ["status"])

# Webhook metrics
webhook_received = Counter(
    "argus_webhooks_received_total",
    "Total GitHub webhooks received",
    ["event_type", "status"],
)

# Watcher metrics
watcher_events = Counter(
    "argus_watcher_events_total",
    "Total K8s watcher events processed",
    ["type", "status"],
)


async def collect_graph_metrics() -> None:
    try:
        for node_type in ["Repository", "Commit", "Service", "Pod", "Namespace", "Cluster", "PipelineRun", "Incident", "PullRequest", "Release"]:
            result = await Neo4jConnection.run_query(
                f"MATCH (n:{node_type}) RETURN count(n) AS cnt",
            )
            graph_node_count.labels(type=node_type).set(result[0]["cnt"] if result else 0)

        for edge_type in ["IS_IN", "BELONGS_TO", "IN", "TRIGGERED", "PRODUCES", "DEPLOYS", "DEPLOYED_FROM", "DETECTED_IN", "MERGED_INTO"]:
            result = await Neo4jConnection.run_query(
                f"MATCH ()-[r:{edge_type}]->() RETURN count(r) AS cnt",
            )
            graph_edge_count.labels(type=edge_type).set(result[0]["cnt"] if result else 0)

        for status in ["open", "resolved"]:
            result = await Neo4jConnection.run_query(
                "MATCH (n:Incident {status: $s}) RETURN count(n) AS cnt",
                {"s": status},
            )
            incident_count.labels(status=status).set(result[0]["cnt"] if result else 0)

        for status in ["pending", "approved", "rejected"]:
            from app.coordinator.store import ProposalStore

            await ProposalStore.ensure_schema()
            result = await Neo4jConnection.run_query(
                "MATCH (n:Proposal {status: $s}) RETURN count(n) AS cnt",
                {"s": status},
            )
            proposal_count.labels(status=status).set(result[0]["cnt"] if result else 0)

    except Exception:
        pass


async def metrics_data() -> bytes:
    neo4j_ok = await Neo4jConnection.verify_connectivity()
    neo4j_up.set(1 if neo4j_ok else 0)

    try:
        await collect_graph_metrics()
    except Exception:
        pass

    return generate_latest()
