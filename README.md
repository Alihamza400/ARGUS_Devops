# ARGUS

[![CI](https://github.com/Alihamza400/ARGUS_Devops/actions/workflows/ci.yml/badge.svg)](https://github.com/Alihamza400/ARGUS_Devops/actions/workflows/ci.yml)
[![Docker](https://github.com/Alihamza400/ARGUS_Devops/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Alihamza400/ARGUS_Devops/actions/workflows/docker-publish.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![Tests](https://img.shields.io/badge/tests-221%20passing-brightgreen.svg)](#)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](#)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-blue?logo=docker)](https://github.com/Alihamza400/ARGUS_Devops/pkgs/container/argus_devops)

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
# 1. Start ARGUS + Neo4j (Docker images auto-pulled from GHCR)
docker compose -f deployments/docker-compose.yml up -d

# 2. Run schema migrations
curl -X POST http://localhost:8000/graph/schema/migrate

# 3. Ingest your repo
docker exec argus-server python scripts/run_adapters.py git /path/to/repo --name my-app

# 4. Analyze a failing pod
docker exec argus-server python scripts/run_agent.py analyze --pod-id pod-crash-xyz --proposal

# 5. Review and execute
docker exec argus-server python scripts/run_gate.py approve <proposal-id> --reviewer alice
docker exec argus-server python scripts/run_enforcer.py execute <proposal-id>
```

---

## What You Can Do

### Ingest data
```bash
# Git repos
docker exec argus-server python scripts/run_adapters.py git /path/to/repo --name my-app

# Kubernetes clusters
docker exec argus-server python scripts/run_adapters.py k8s --cluster-name prod

# GitHub Actions
docker exec argus-server python scripts/run_adapters.py github --owner my-org --repo my-app --token ghp_...
```

### Analyze incidents
```bash
# Analyze a pod by ID
docker exec argus-server python scripts/run_agent.py analyze --pod-id pod-crash-xyz

# Analyze and generate a rollback proposal
docker exec argus-server python scripts/run_agent.py analyze --pod-id pod-crash-xyz --proposal

# Find all unhealthy pods
docker exec argus-server python scripts/run_agent.py unhealthy
```

### Review and approve
```bash
# List pending proposals
docker exec argus-server python scripts/run_gate.py list

# View proposal details with evidence
docker exec argus-server python scripts/run_gate.py view <proposal-id>

# Approve or reject
docker exec argus-server python scripts/run_gate.py approve <proposal-id> --reviewer alice
docker exec argus-server python scripts/run_gate.py reject <proposal-id> --reviewer bob --comment "Need more evidence"
```

### Execute approved changes
```bash
# Preview without executing
docker exec argus-server python scripts/run_enforcer.py execute <proposal-id> --dry-run

# Execute
docker exec argus-server python scripts/run_enforcer.py execute <proposal-id>
```

---

## Example: From Crash to Fix in 5 Commands

A pod is in `CrashLoopBackOff`. Here's the full workflow:

```bash
# 1. Find unhealthy pods
docker exec argus-server python scripts/run_agent.py unhealthy

# 2. Analyze and generate rollback proposal
docker exec argus-server python scripts/run_agent.py analyze --pod-name api-gateway --proposal

# Output:
# ARGUS ANALYSIS [CRITICAL] (100% confidence)
#   Pod 'api-gateway-xyz' in CrashLoopBackOff
#   Service: api-gateway → Repository: api-service
#   Latest commit: 'Fix OOM in handler' by Alice (22 min ago)
#   Suggestion: Rollback commit abc123
#
# PROPOSAL: [Argus] Rollback api-service — Fix OOM in handler

# 3. View the proposal with evidence
docker exec argus-server python scripts/run_gate.py view <proposal-id>

# 4. Approve it
docker exec argus-server python scripts/run_gate.py approve <proposal-id> --reviewer alice

# 5. Execute the rollback
docker exec argus-server python scripts/run_enforcer.py execute <proposal-id>
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

## Deployment

### One-command start

```bash
docker compose -f deployments/docker-compose.yml up -d
```

Images are pulled from `ghcr.io/alihamza400/argus_devops`. No local build needed.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `NEO4J_PASSWORD` | `argus_devops_2026` | Neo4j database password |
| `ARGUS_ENV` | `production` | Runtime environment |
| `ARGUS_GITHUB_WEBHOOK_SECRET` | (empty) | HMAC secret for GitHub webhook verification |
| `ARGUS_K8S_WATCHER_ENABLED` | `false` | Enable automatic K8s pod crash detection |
| `ARGUS_K8S_WATCHER_NAMESPACE` | (all) | Restrict K8s watcher to a specific namespace |

### Run without Docker

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## Tech Stack

Python 3.12+ · FastAPI · Neo4j 5.x · Pydantic v2 · Docker · GitHub Container Registry

---

## Status

All 8 core phases are complete — 221 tests passing on every commit via CI/CD pipeline. Docker images published to GHCR on every push.

---

## License

MIT
