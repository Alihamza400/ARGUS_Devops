# Argus

**A shared-context coordination layer for DevOps agents — connecting code, CI/CD, and infrastructure into a single provenance graph with automated analysis, conflict resolution, human approval gates, and closed-loop enforcement.**

---

## The Problem

DevOps teams operate in four disconnected worlds:

| World | Tooling | Question |
|---|---|---|
| Code | GitHub, GitLab | Who wrote this? |
| CI/CD | GitHub Actions, Jenkins | Did it deploy? |
| Runtime | Kubernetes | Why is it crashing? |
| Operations | PagerDuty, Slack | What do we do? |

When an incident happens, connecting these dots is manual, slow, and error-prone. No single system tracks the full chain: **commit → build → deploy → runtime → incident → fix → verification**.

## What Argus Does

Argus ingests data from across the DevOps lifecycle into a **Neo4j knowledge graph**, then provides automated agents that analyze, propose, coordinate, approve, and enforce changes with full provenance.

```
   Code ──► Repository ◄────── Service ◄──── Pod (crashing)
               ▲                     ▲
               │                     │
          Commit ──► PipelineRun ──► Deployment
               │                     │
               └─ TRIGGERED ──── PRODUCES ─┘
```

## Architecture

```
                    ┌─────────────────────────────┐
                    │        FastAPI Server         │
                    │  (REST + GraphQL queries)     │
                    └──────────┬──────────────────┘
                               │
     ┌──────────┬──────────┬───┴───┬──────────┬──────────┐
     │ Git       │ K8s      │ CI/CD  │ Agents   │ Enforcer │
     │ Adapter   │ Adapter  │Adapter │          │          │
     ├──────────┼──────────┼────────┼──────────┼──────────┤
     │ Repos,   │ Pods,    │Workflow│ Incident │ Precheck │
     │ Commits, │ Services,│ Runs,  │ Analysis │ Execute  │
     │ Branches │ Deploys  │ Status │ Proposal │ Verify   │
     └──────────┴──────────┴────────┴──────────┴──────────┘
                               │
                    ┌──────────┴──────────┐
                    │      Neo4j Graph     │
                    │  (single source of   │
                    │       truth)         │
                    └─────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │  Conflict Detection  │
                    │  + Human Approval    │
                    │  + Enforcement       │
                    └─────────────────────┘
```

## Features

| Feature | Description |
|---|---|
| **Graph Storage** | 13 node types, 13 edge types in Neo4j with constraints and indexes |
| **Git Ingestion** | Clone repos, walk branches, ingest commits with full metadata |
| **K8s Ingestion** | Sync clusters, namespaces, pods, services, deployments |
| **CI/CD Ingestion** | Import GitHub Actions workflow runs, link to commits |
| **Incident Analysis** | Trace a failing pod through service → repo → commits → pipeline |
| **Conflict Detection** | 4 conflict types: direct, indirect, cascading, complementary |
| **Evidence Scoring** | 5-factor weighted scoring (confidence, evidence, risk, severity, recency) |
| **Approval Gates** | Configurable policies: min reviewers, no self-approval, senior for critical |
| **Closed-Loop Enforcement** | Pre-checks → execute → verify → auto-rollback on failure |

## Quick Start

```bash
# Start Neo4j + API server
docker compose -f deployments/docker-compose.yml up -d

# Run schema migrations
curl -X POST http://localhost:8000/graph/schema/migrate

# Ingest a Git repo
python server/scripts/run_adapters.py git /path/to/repo --name my-repo

# Analyze an incident
python server/scripts/run_agent.py analyze --pod-id pod-crash-xyz --proposal

# Review and approve (after conflict checks)
python server/scripts/run_gate.py approve <proposal-id> --reviewer alice

# Execute the approved proposal
python server/scripts/run_enforcer.py execute <proposal-id>
```

## Architecture Decision Records

- **Why Neo4j?** — Graph database reflects the connected nature of DevOps data. Tree structures (commit → PR → deploy) and radial queries (what does this pod touch?) are naturally graph-shaped.
- **Why not a web UI first?** — CLI-first allows automation. Every CLI command is a potential API call from another system. UI can be added later without changing the architecture.
- **Why agents, not rules?** — Rules become unmanageable as systems grow. Agents use the graph as evidence to make context-aware decisions.

## Project Status

Argus is **pre-production** — the full platform is built and tested (208 tests passing). Production launch phases include Neo4j HA setup, authentication, CI/CD pipeline, and security audit.

## Built With

- **Python 3.12+** — FastAPI, Pydantic, httpx
- **Neo4j 5.x** — Graph database with async Python driver
- **gitpython** — Git repository ingestion
- **Docker** — Containerized deployment

## License

MIT
