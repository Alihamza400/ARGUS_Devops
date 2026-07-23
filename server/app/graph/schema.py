from enum import Enum


class NodeType(str, Enum):
    REPOSITORY = "Repository"
    COMMIT = "Commit"
    PULL_REQUEST = "PullRequest"
    SERVICE = "Service"
    DEPLOYMENT = "Deployment"
    POD = "Pod"
    NAMESPACE = "Namespace"
    CLUSTER = "Cluster"
    PIPELINE_RUN = "PipelineRun"
    SECURITY_FINDING = "SecurityFinding"
    COST_REPORT = "CostReport"


class EdgeType(str, Enum):
    DEPLOYED_FROM = "DEPLOYED_FROM"
    RUNS_ON = "RUNS_ON"
    BELONGS_TO = "BELONGS_TO"
    IN = "IN"
    TRIGGERED = "TRIGGERED"
    IS_IN = "IS_IN"
    MERGED_INTO = "MERGED_INTO"
    DEPLOYS = "DEPLOYS"
    AFFECTS = "AFFECTS"
    CHARGES = "CHARGES"
    PRODUCES = "PRODUCES"


NODE_PROPERTIES: dict[NodeType, dict[str, str]] = {
    NodeType.REPOSITORY: {
        "id": "string",
        "url": "string",
        "name": "string",
        "default_branch": "string",
        "provider": "string",
    },
    NodeType.COMMIT: {
        "id": "string",
        "sha": "string",
        "message": "string",
        "author": "string",
        "email": "string",
        "timestamp": "datetime",
        "branch": "string",
    },
    NodeType.PULL_REQUEST: {
        "id": "string",
        "number": "integer",
        "title": "string",
        "state": "string",
        "author": "string",
    },
    NodeType.SERVICE: {
        "id": "string",
        "name": "string",
        "namespace": "string",
        "image": "string",
        "replicas": "integer",
    },
    NodeType.DEPLOYMENT: {
        "id": "string",
        "name": "string",
        "strategy": "string",
        "revision": "integer",
        "namespace": "string",
    },
    NodeType.POD: {
        "id": "string",
        "name": "string",
        "phase": "string",
        "node": "string",
        "namespace": "string",
        "cpu_request": "string",
        "cpu_limit": "string",
        "memory_request": "string",
        "memory_limit": "string",
    },
    NodeType.NAMESPACE: {
        "id": "string",
        "name": "string",
        "labels": "string",
    },
    NodeType.CLUSTER: {
        "id": "string",
        "name": "string",
        "version": "string",
        "provider": "string",
    },
    NodeType.PIPELINE_RUN: {
        "id": "string",
        "workflow": "string",
        "status": "string",
        "trigger": "string",
        "started_at": "datetime",
        "duration_seconds": "integer",
    },
    NodeType.SECURITY_FINDING: {
        "id": "string",
        "type": "string",
        "severity": "string",
        "description": "string",
        "resource": "string",
    },
    NodeType.COST_REPORT: {
        "id": "string",
        "resource": "string",
        "amount": "float",
        "currency": "string",
        "period_start": "datetime",
        "period_end": "datetime",
    },
}

EDGE_PROPERTIES: dict[EdgeType, dict[str, str]] = {
    EdgeType.DEPLOYED_FROM: {"since": "datetime", "version": "string"},
    EdgeType.RUNS_ON: {"since": "datetime"},
    EdgeType.BELONGS_TO: {},
    EdgeType.IN: {},
    EdgeType.TRIGGERED: {"started_at": "datetime"},
    EdgeType.IS_IN: {},
    EdgeType.MERGED_INTO: {"merged_at": "datetime"},
    EdgeType.DEPLOYS: {"strategy": "string", "revision": "integer"},
    EdgeType.AFFECTS: {"detected_at": "datetime", "status": "string"},
    EdgeType.CHARGES: {"amount": "float", "period": "string"},
    EdgeType.PRODUCES: {"at": "datetime"},
}


VALID_EDGE_PAIRS: dict[EdgeType, list[tuple[NodeType, NodeType]]] = {
    EdgeType.DEPLOYED_FROM: [(NodeType.SERVICE, NodeType.REPOSITORY)],
    EdgeType.RUNS_ON: [(NodeType.POD, NodeType.CLUSTER)],
    EdgeType.BELONGS_TO: [
        (NodeType.POD, NodeType.SERVICE),
        (NodeType.DEPLOYMENT, NodeType.SERVICE),
    ],
    EdgeType.IN: [
        (NodeType.SERVICE, NodeType.NAMESPACE),
        (NodeType.POD, NodeType.NAMESPACE),
        (NodeType.DEPLOYMENT, NodeType.NAMESPACE),
        (NodeType.NAMESPACE, NodeType.CLUSTER),
    ],
    EdgeType.TRIGGERED: [(NodeType.COMMIT, NodeType.PIPELINE_RUN)],
    EdgeType.IS_IN: [(NodeType.COMMIT, NodeType.REPOSITORY)],
    EdgeType.MERGED_INTO: [(NodeType.PULL_REQUEST, NodeType.REPOSITORY)],
    EdgeType.DEPLOYS: [(NodeType.DEPLOYMENT, NodeType.SERVICE)],
    EdgeType.AFFECTS: [
        (NodeType.SECURITY_FINDING, NodeType.SERVICE),
        (NodeType.SECURITY_FINDING, NodeType.POD),
    ],
    EdgeType.CHARGES: [
        (NodeType.COST_REPORT, NodeType.SERVICE),
        (NodeType.COST_REPORT, NodeType.NAMESPACE),
    ],
    EdgeType.PRODUCES: [(NodeType.PIPELINE_RUN, NodeType.DEPLOYMENT)],
}


SCHEMA_MIGRATIONS_CYPHER = """
// Constraints (unique node property enforcement)
CREATE CONSTRAINT repository_id IF NOT EXISTS FOR (n:Repository) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT commit_id IF NOT EXISTS FOR (n:Commit) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT pull_request_id IF NOT EXISTS FOR (n:PullRequest) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT service_id IF NOT EXISTS FOR (n:Service) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT deployment_id IF NOT EXISTS FOR (n:Deployment) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT pod_id IF NOT EXISTS FOR (n:Pod) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT namespace_id IF NOT EXISTS FOR (n:Namespace) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT cluster_id IF NOT EXISTS FOR (n:Cluster) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT pipeline_run_id IF NOT EXISTS FOR (n:PipelineRun) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT security_finding_id IF NOT EXISTS FOR (n:SecurityFinding) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT cost_report_id IF NOT EXISTS FOR (n:CostReport) REQUIRE n.id IS UNIQUE;

// Indexes for common query patterns
CREATE INDEX repository_name IF NOT EXISTS FOR (n:Repository) ON (n.name);
CREATE INDEX service_name IF NOT EXISTS FOR (n:Service) ON (n.name);
CREATE INDEX pod_phase IF NOT EXISTS FOR (n:Pod) ON (n.phase);
CREATE INDEX commit_sha IF NOT EXISTS FOR (n:Commit) ON (n.sha);
CREATE INDEX namespace_name IF NOT EXISTS FOR (n:Namespace) ON (n.name);
"""
