<div align="center">
  <h1>ARGUS</h1>
  <p><strong>A shared-context coordination layer for DevOps agents.</strong></p>
  <p>Ingests code, CI/CD, and infrastructure into a single graph with automated incident analysis,<br>conflict resolution, human review, and closed-loop enforcement.</p>
  <br>
  <p>
    <a href="https://github.com/Alihamza400/ARGUS_Devops/actions/workflows/ci.yml"><img src="https://github.com/Alihamza400/ARGUS_Devops/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="https://github.com/Alihamza400/ARGUS_Devops/actions/workflows/docker-publish.yml"><img src="https://github.com/Alihamza400/ARGUS_Devops/actions/workflows/docker-publish.yml/badge.svg" alt="Docker"></a>
    <a href="https://github.com/Alihamza400/ARGUS_Devops/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
    <a href="https://github.com/Alihamza400/ARGUS_Devops/pkgs/container/argus_devops"><img src="https://img.shields.io/badge/docker-ghcr.io-blue?logo=docker" alt="Docker"></a>
    <a href="#"><img src="https://img.shields.io/badge/tests-244%20passing-brightgreen.svg" alt="Tests"></a>
    <a href="https://www.python.org/downloads/release/python-3120/"><img src="https://img.shields.io/badge/python-3.12-blue.svg" alt="Python 3.12"></a>
  </p>
</div>

---

- [What is ARGUS?](#what-is-argus)
- [Quick Start](#quick-start)
- [Walkthrough: From Crash to Fix](#walkthrough-from-crash-to-fix)
- [What You Can Do](#what-you-can-do)
- [Deployment](#deployment)
- [API & Auth](#api--auth)
- [Monitoring](#monitoring)
- [Architecture](#architecture)
- [Contributing](#contributing)
- [License](#license)

---

## What is ARGUS?

DevOps has four disconnected worlds:

| World | You have | But you can't answer |
|---|---|---|
| Code | Git | What commit caused this? |
| Build | GitHub Actions | Did the pipeline even pass? |
| Runtime | Kubernetes | Why is this pod crashing? |
| Ops | PagerDuty | What should we do? |

When something breaks, engineers waste hours connecting dots manually. **ARGUS connects these dots automatically** — from a crashing pod back to the exact commit, author, and pipeline run that produced it.

### How it works

```
Git repo ──► ARGUS ──► Neo4j Graph ──► Analysis ──► Proposal ──► Review ──► Execute
               │                                                        │
               └──────────── Conflicts checked ─── Policies enforced ────┘
                                                           Auto-rollback if fails
```

---

## Quick Start

```bash
# 1. Start ARGUS + Neo4j
docker compose -f deployments/docker-compose.yml up -d

# 2. Run schema migrations
curl -X POST http://localhost:8000/graph/schema/migrate

# 3. Ingest your repo
docker exec argus-server python scripts/run_adapters.py git /path/to/repo --name my-app

# 4. Analyze a failing pod (if you have K8s data)
docker exec argus-server python scripts/run_agent.py analyze --pod-id pod-crash-xyz --proposal

# 5. Review and execute the proposal
docker exec argus-server python scripts/run_gate.py approve <proposal-id> --reviewer alice
docker exec argus-server python scripts/run_enforcer.py execute <proposal-id>
```

---

## Walkthrough: From Crash to Fix

A pod enters `CrashLoopBackOff`. Here's the exact workflow:

```bash
# 1. Find unhealthy pods
docker exec argus-server python scripts/run_agent.py unhealthy

# Output:
# Pod 'api-gateway-xyz' - CrashLoopBackOff - namespace: production

# 2. Analyze the incident (traces pod → service → repo → commits → pipeline)
docker exec argus-server python scripts/run_agent.py analyze --pod-name api-gateway --proposal

# Output:
# ARGUS ANALYSIS [CRITICAL] (100% confidence)
#   Pod 'api-gateway-xyz' in CrashLoopBackOff
#   Service: api-gateway → Repository: api-service
#   Latest commit: 'Fix OOM in handler' by Alice (22 min ago)
#   Pipeline: CI #847 (failed)
#   Suggestion: Rollback commit abc123
#
# PROPOSAL: [Argus] Rollback api-service — Fix OOM in handler

# 3. View evidence attached to the proposal
docker exec argus-server python scripts/run_gate.py view <proposal-id>

# 4. Approve the rollback
docker exec argus-server python scripts/run_gate.py approve <proposal-id> --reviewer alice

# 5. Execute it (pre-checks → rollback → health verification)
docker exec argus-server python scripts/run_enforcer.py execute <proposal-id>
```

---

## What You Can Do

<details>
<summary><b>📥 Ingest data</b></summary>

```bash
# Git repositories
docker exec argus-server python scripts/run_adapters.py git /path/to/repo --name my-app

# Kubernetes clusters
docker exec argus-server python scripts/run_adapters.py k8s --cluster-name prod

# GitHub Actions pipelines
docker exec argus-server python scripts/run_adapters.py github --owner my-org --repo my-app --token ghp_...
```
</details>

<details>
<summary><b>🔍 Analyze incidents</b></summary>

```bash
# Analyze a specific pod
docker exec argus-server python scripts/run_agent.py analyze --pod-id pod-crash-xyz

# Analyze and generate a rollback proposal
docker exec argus-server python scripts/run_agent.py analyze --pod-id pod-crash-xyz --proposal

# List all unhealthy pods across the cluster
docker exec argus-server python scripts/run_agent.py unhealthy
```
</details>

<details>
<summary><b>✅ Review and approve</b></summary>

```bash
# List proposals waiting for review
docker exec argus-server python scripts/run_gate.py list

# View proposal details with evidence
docker exec argus-server python scripts/run_gate.py view <proposal-id>

# Approve or reject
docker exec argus-server python scripts/run_gate.py approve <proposal-id> --reviewer alice
docker exec argus-server python scripts/run_gate.py reject <proposal-id> --reviewer bob --comment "Need more evidence"

# Check review status
docker exec argus-server python scripts/run_gate.py status <proposal-id>
```
</details>

<details>
<summary><b>⚡ Execute changes</b></summary>

```bash
# Preview without making changes
docker exec argus-server python scripts/run_enforcer.py execute <proposal-id> --dry-run

# Execute the approved change
docker exec argus-server python scripts/run_enforcer.py execute <proposal-id>

# List past enforcements
docker exec argus-server python scripts/run_enforcer.py list

# View enforcer configuration
docker exec argus-server python scripts/run_enforcer.py config --show
```
</details>

<details>
<summary><b>📡 Set up webhooks (auto-ingestion)</b></summary>

```bash
# Configure in GitHub
# Settings → Webhooks → Add webhook
# Payload URL: https://your-server.com/webhooks/github
# Content type: application/json
# Secret: (your ARGUS_GITHUB_WEBHOOK_SECRET)
# Events: Push, Pull requests, Releases

# Enable K8s watcher (auto-detect pod crashes)
export ARGUS_K8S_WATCHER_ENABLED=true
docker compose -f deployments/docker-compose.yml up -d
```
</details>

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
| `ARGUS_ENV` | `production` | Runtime environment (development/production) |
| `ARGUS_LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |
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

## API & Auth

All API endpoints require authentication. Default credentials:

```bash
# Login
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin123"}'

# Use the token
curl http://localhost:8000/graph/schema \
  -H "Authorization: Bearer eyJ..."

# Or create an API key for CI/CD
curl -X POST http://localhost:8000/auth/api-keys \
  -H "Authorization: Bearer eyJ..." \
  -H "Content-Type: application/json" \
  -d '{"name": "ci-token"}'
```

### Roles

| Role | Permissions |
|---|---|
| **admin** | Everything — schema migrations, manage users, delete nodes, resolve conflicts, update policies |
| **engineer** | Ingest data, analyze incidents, create proposals, review and execute changes, manage their API keys |
| **viewer** | Read-only — browse graph, list proposals and configs, view agent output |

### Key endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/login` | Login, get JWT token |
| `POST` | `/auth/users` | Create user (admin only) |
| `GET` | `/auth/me` | Current user info |
| `POST` | `/auth/api-keys` | Create API key |
| `POST` | `/graph/nodes` | Create a graph node |
| `GET` | `/graph/nodes` | List all nodes |
| `POST` | `/agent/analyze` | Run incident analysis |
| `POST` | `/coordinator/proposals` | Submit a proposal |
| `POST` | `/gate/review` | Submit a review decision |
| `POST` | `/enforce/execute` | Execute an approved proposal |

Full API reference is available at `/docs` (Swagger UI) when the server is running.

---

## Monitoring

ARGUS exposes structured JSON logs and Prometheus metrics.

### Logs

Every log entry is JSON. Every request gets a correlation ID.

```json
{"timestamp": "2026-07-23T11:09:52Z", "level": "INFO", "logger": "argus",
 "message": "Neo4j connected successfully", "request_id": "abc123"}
```

View logs:
```bash
docker logs -f argus-server | jq .
```

### Metrics

`GET /metrics` returns Prometheus-formatted metrics:

```bash
# Scrape from Prometheus
curl http://localhost:8000/metrics
```

Metrics available:

| Metric | Type | Labels |
|---|---|---|
| `argus_requests_total` | Counter | `method`, `endpoint`, `status` |
| `argus_request_duration_seconds` | Histogram | `method`, `endpoint` |
| `argus_neo4j_up` | Gauge | — |
| `argus_graph_nodes_total` | Gauge | `type` |
| `argus_incidents_total` | Gauge | `status` |
| `argus_proposals_total` | Gauge | `status` |
| `argus_webhooks_received_total` | Counter | `event_type`, `status` |
| `argus_watcher_events_total` | Counter | `type`, `status` |

---

## Architecture

```
                          ┌──────────────────────────────────┐
                          │         FASTAPI SERVER            │
                          │    REST API + CLI + Metrics       │
                          └────────────┬─────────────────────┘
                                       │
        ┌──────────┬──────────┬────────┼─────────┬──────────┬──────────┐
        │          │          │        │         │          │          │
   ┌────▼────┐ ┌──▼───┐ ┌───▼────┐ ┌─▼──────┐ ┌─▼──────┐ ┌─▼───────┐  │
   │   Git   │ │ K8s  │ │GitHub  │ │Incident│ │Conflict│ │Approval │  │
   │ Adapter │ │Adapter│ │Actions │ │ Agent  │ │Coord.  │ │ Gate    │  │
   └────┬────┘ └──┬───┘ └───┬────┘ └─┬──────┘ └─┬──────┘ └─┬───────┘  │
        │         │         │        │         │          │          │
        └─────────┴─────────┴────────┴─────────┴──────────┴──────────┘
                                       │
                          ┌────────────▼────────────┐
                          │       NEO4J GRAPH        │
                          │    Single source of      │
                          │    truth for DevOps      │
                          └─────────────────────────┘
                                       │
                          ┌────────────▼────────────┐
                          │    ENFORCER PIPELINE     │
                          │  Precheck → Execute →    │
                          │  Verify → Auto-rollback  │
                          └─────────────────────────┘
```

### Project structure

```
server/
├── app/
│   ├── adapters/        # Data ingestion (Git, K8s, GitHub Actions, webhooks)
│   ├── agents/          # Incident analysis and proposal agents
│   ├── auth/            # JWT auth, RBAC, API keys
│   ├── coordinator/     # Conflict detection and resolution
│   ├── gate/            # Human approval workflow
│   ├── enforcer/        # Change execution and verification
│   ├── graph/           # Neo4j connection and schema
│   ├── monitoring/      # Structured logging and Prometheus metrics
│   ├── api/             # REST API routes
│   └── main.py          # Server entry point
├── scripts/             # CLI commands (adapters, agents, gate, enforcer)
└── tests/               # 244 tests
```

---

## Contributing

Contributions are welcome. To get started:

```bash
# Clone and set up
git clone https://github.com/Alihamza400/ARGUS_Devops.git
cd ARGUS_Devops/server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start Neo4j
docker compose -f ../deployments/docker-compose.yml up -d neo4j

# Run tests
pytest
```

---

## License

MIT — see [LICENSE](LICENSE).
