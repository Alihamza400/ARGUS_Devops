// Argus Knowledge Graph - Initial Schema Migration
// Run against Neo4j 5.x

// === CONSTRAINTS ===
// Ensure unique IDs per node type

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

// === INDEXES ===
// Speed up common query patterns

CREATE INDEX repository_name IF NOT EXISTS FOR (n:Repository) ON (n.name);
CREATE INDEX service_name IF NOT EXISTS FOR (n:Service) ON (n.name);
CREATE INDEX service_namespace IF NOT EXISTS FOR (n:Service) ON (n.namespace);
CREATE INDEX pod_phase IF NOT EXISTS FOR (n:Pod) ON (n.phase);
CREATE INDEX pod_namespace IF NOT EXISTS FOR (n:Pod) ON (n.namespace);
CREATE INDEX commit_sha IF NOT EXISTS FOR (n:Commit) ON (n.sha);
CREATE INDEX commit_branch IF NOT EXISTS FOR (n:Commit) ON (n.branch);
CREATE INDEX namespace_name IF NOT EXISTS FOR (n:Namespace) ON (n.name);
CREATE INDEX deployment_namespace IF NOT EXISTS FOR (n:Deployment) ON (n.namespace);

// === USAGE STATISTICS (optional, improves query planning) ===
CALL db.stats.retrieve('GRAPH COUNTS');
