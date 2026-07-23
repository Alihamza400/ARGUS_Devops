# ARGUS

**A shared-context coordination layer for DevOps agents.**

Ingests code, CI/CD, and infrastructure into a single graph with automated incident analysis, conflict resolution, human review, and closed-loop enforcement.

---

## Why This Exists

Devops has four disconnected worlds:

| World | You have | But you can't answer |
|---|---|---|
| Code | Git | What commit caused this? |
| Build | GitHub Actions | Did the pipeline even pass? |
| Runtime | Kubernetes | Why is this pod crashing? |
| Ops | PagerDuty | What should we do? |

When something breaks, engineers waste hours in War Rooms connecting dots manually. **ARGUS connects these dots automatically** — from a crashing pod back to the exact commit, author, and pipeline run that produced it.

---

## How It Works

```
Git repo ──► ARGUS ──► Neo4j Graph ──► Analysis ──► Proposal ──► Review ──► Execute
               │                                                        │
               └──────────── Conflicts checked ─── Policies enforced ────┘
                                                           Auto-rollback if fails
```

Three steps:

1. **Ingest** — Point ARGUS at your Git repos, K8s clusters, and GitHub Actions. It builds a graph connecting everything.
2. **Analyze** — When a pod crashes, run one command. ARGUS traces the full chain: pod → service → repo → commit → pipeline.
3. **Act** — ARGUS proposes a fix, checks for conflicts, routes it for human approval, executes the change, and verifies it worked.

---

## Quick Start

```bash
# 1. Start Neo4j
docker compose -f deployments/docker-compose.yml up -d

# 2. Install and migrate
cd server && pip install -r requirements.txt
curl -X POST http://localhost:8000/graph/schema/migrate

# 3. Ingest your repo
python scripts/run_adapters.py git /path/to/repo --name my-app

# 4. Analyze a failing pod
python scripts/run_agent.py analyze --pod-id pod-crash-xyz --proposal

# 5. Review and execute
python scripts/run_gate.py approve <proposal-id> --reviewer alice
python scripts/run_enforcer.py execute <proposal-id>
```

---

## What You Can Do

### Ingest data
```bash
# Git repos
python scripts/run_adapters.py git ./my-repo --name my-app

# Kubernetes clusters
python scripts/run_adapters.py k8s --cluster-name prod

# GitHub Actions
python scripts/run_adapters.py github --owner my-org --repo my-app --token ghp_...
```

### Analyze incidents
```bash
# Analyze a pod by ID
python scripts/run_agent.py analyze --pod-id pod-crash-xyz

# Analyze and generate a rollback proposal
python scripts/run_agent.py analyze --pod-id pod-crash-xyz --proposal

# Find all unhealthy pods
python scripts/run_agent.py unhealthy
```

### Review and approve
```bash
# List pending proposals
python scripts/run_gate.py list

# View proposal details with evidence
python scripts/run_gate.py view <proposal-id>

# Approve or reject
python scripts/run_gate.py approve <proposal-id> --reviewer alice
python scripts/run_gate.py reject <proposal-id> --reviewer bob --comment "Need more evidence"
```

### Execute approved changes
```bash
# Preview without executing
python scripts/run_enforcer.py execute <proposal-id> --dry-run

# Execute
python scripts/run_enforcer.py execute <proposal-id>
```

---

## Example: From Crash to Fix in 5 Commands

A pod is in `CrashLoopBackOff`. Here's the full workflow:

```bash
# 1. Find the pod
python scripts/run_agent.py unhealthy

# 2. Analyze it (traces through services, repos, commits, pipelines)
python scripts/run_agent.py analyze --pod-name api-gateway --proposal

# Output:
# ARGUS ANALYSIS [CRITICAL] (100% confidence)
#   Pod 'api-gateway-xyz' in CrashLoopBackOff
#   Service: api-gateway → Repository: api-service
#   Latest commit: 'Fix OOM in handler' by Alice (22 min ago)
#   Suggestion: Rollback commit abc123
#
# PROPOSAL: [Argus] Rollback api-service — Fix OOM in handler

# 3. View the proposal with evidence
python scripts/run_gate.py view <proposal-id>

# 4. Approve it
python scripts/run_gate.py approve <proposal-id> --reviewer alice

# 5. Execute the rollback
python scripts/run_enforcer.py execute <proposal-id>
```

---

## Key Concepts

| Concept | What it means |
|---|---|
| **Graph** | All your DevOps data (code, builds, pods, services) connected in Neo4j |
| **Adapter** | Ingests data from external systems (Git, K8s, GitHub Actions) |
| **Agent** | Analyzes the graph and proposes actions (incident analysis, rollbacks) |
| **Proposal** | A suggested change with evidence attached |
| **Conflict** | When two proposals target the same resource (auto-detected and scored) |
| **Review** | Human approval step with configurable policies |
| **Enforcement** | Executing the approved change with pre-checks and verification |

---

## Project Structure

```
server/
├── app/
│   ├── adapters/        # Data ingestion (Git, K8s, GitHub Actions)
│   ├── agents/          # Analysis and proposal agents
│   ├── coordinator/     # Conflict detection and resolution
│   ├── gate/            # Human approval workflow
│   ├── enforcer/        # Change execution and verification
│   ├── graph/           # Neo4j connection and schema
│   ├── api/             # REST API endpoints
│   └── main.py          # Server entry point
├── scripts/             # CLI commands
├── tests/               # 208 tests
└── requirements.txt
```

---

## Tech Stack

Python 3.12+ · FastAPI · Neo4j 5.x · Pydantic v2 · Docker

---

## Status

All 8 core phases are complete (208 tests passing). The remaining work is production infrastructure: authentication, HA setup, CI/CD pipeline, and security hardening.

---

## License

MIT
