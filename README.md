<![CDATA[<div align="center">
  <h1>ARGUS</h1>
  <p><strong>A shared-context coordination layer for DevOps agents.</strong></p>
  <p>Ingests code, CI/CD, and infrastructure into a provenance graph with automated analysis, conflict resolution, human approval gates, and closed-loop enforcement.</p>
  <br>
</div>

---

## Table of Contents

- [Rationale](#rationale)
- [Architecture](#architecture)
- [Core Capabilities](#core-capabilities)
- [The Provenance Chain](#the-provenance-chain)
- [Getting Started](#getting-started)
- [CLI Reference](#cli-reference)
- [API Reference](#api-reference)
- [Project Structure](#project-structure)
- [Technology Stack](#technology-stack)
- [Development](#development)
- [Roadmap](#roadmap)
- [License](#license)

---

## Rationale

### The Problem

DevOps teams operate across four fundamentally disconnected domains:

| Domain | Tooling | Question |
|---|---|---|
| **Code** | GitHub, GitLab | Who wrote this? When? Why? |
| **CI/CD** | GitHub Actions, Jenkins | Did it build? Did it deploy? |
| **Runtime** | Kubernetes | Why is it crashing? What changed? |
| **Operations** | PagerDuty, Slack | What do we do? Who decides? |

When an incident occurs, connecting these dots is a manual process involving Slack threads, War Rooms, and tribal knowledge. No single system tracks the complete chain: **commit → build → deploy → runtime → incident → fix → verification**.

The result: **longer MTTR, repeated incidents, and no audit trail** for why decisions were made.

### The Solution

ARGUS creates a **shared knowledge graph** that connects these domains into a single source of truth. On top of this graph, we layer:

1. **Automated analysis** — Trace a crashing pod back to the exact commit and pipeline run that produced it
2. **Conflict-aware proposals** — Before any change is suggested, check if it conflicts with other in-flight proposals
3. **Evidence-based scoring** — Rank competing proposals by confidence, evidence quality, risk, and recency
4. **Human approval gates** — Enforce policy: minimum reviewers, no self-approval, senior required for critical changes
5. **Closed-loop enforcement** — Pre-checks → execute → verify → auto-rollback on failure

### Design Principles

- **CLI-first, API-second** — Every operation is available via CLI, enabling automation and CI/CD integration
- **Graph-native storage** — DevOps data is inherently connected (trees, radial queries, paths). A graph database reflects this naturally
- **Idempotent ingestion** — Running adapters multiple times produces the same result. No duplicate data.
- **Agent-based architecture** — New capabilities are added as agents, not as modifications to existing code

---

## Architecture

```
                          ┌──────────────────────────────────┐
                          │         FASTAPI SERVER            │
                          │    REST API + CLI interfaces      │
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

---

## Core Capabilities

| Capability | Description |
|---|---|
| **Graph Storage** | 13 node types, 13 edge types in Neo4j with uniqueness constraints and query indexes |
| **Git Ingestion** | Clone repositories, walk all branches, ingest commits with full metadata and author attribution |
| **Kubernetes Ingestion** | Sync clusters, namespaces, pods, services, deployments with resource specifications |
| **CI/CD Ingestion** | Import GitHub Actions workflow runs with status, duration, trigger events; link to commits |
| **Incident Analysis** | Trace a failing pod through the full chain: pod → service → repository → commits → pipeline |
| **Conflict Detection** | 4 conflict types — direct (same resource), indirect (compatible actions), cascading (graph neighbors), complementary (different resources) |
| **Evidence Scoring** | 5-factor weighted scoring: confidence (30%), evidence count (25%), risk level (20%), severity (15%), recency (10%) |
| **Approval Policies** | Enforceable rules: minimum reviewers, no self-approval, senior required for critical changes, minimum evidence and confidence thresholds |
| **Closed-Loop Enforcement** | Pre-checks (change windows, blast radius, rate limits) → execute → verify health → auto-rollback on failure |

---

## The Provenance Chain

The graph connects everything into a single traceable path:

```
Pod (CrashLoopBackOff)
 │
 ├──[:BELONGS_TO]──► Service
 │                     │
 │                     ├──[:IN]──► Namespace ──[:IN]──► Cluster
 │                     │
 │                     └──[:DEPLOYED_FROM]──► Repository
 │                                              │
 │                                              └──[:IS_IN]◄── Commit
 │                                                              │
 │                                                              └──[:TRIGGERED]──► PipelineRun
 │                                                                                  │
 │                                                                                  └──[:PRODUCES]──► Deployment
 │                                                                                                    │
 │                                                                                                    └──[:DEPLOYS]──► Service
```

One query answers: *"This pod is crashing. The service was deployed from commit `abc123` by Alice, via pipeline run #42 which completed 10 minutes ago."*

---

## Getting Started

### Prerequisites

- Python 3.12+
- Docker & Docker Compose (for Neo4j)
- A GitHub personal access token (for CI/CD adapter)

### 1. Start Neo4j

```bash
docker compose -f deployments/docker-compose.yml up -d
```

### 2. Install dependencies

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Run schema migrations

```bash
curl -X POST http://localhost:8000/graph/schema/migrate
curl -X POST http://localhost:8000/coordinator/schema
curl -X POST http://localhost:8000/gate/schema
curl -X POST http://localhost:8000/enforce/schema
```

### 4. Ingest your first repository

```bash
python scripts/run_adapters.py git /path/to/repo --name my-service
```

### 5. Analyze an incident

```bash
python scripts/run_agent.py analyze --pod-id pod-crash-xyz --proposal
```

### 6. Review and approve

```bash
python scripts/run_gate.py approve <proposal-id> --reviewer alice
```

### 7. Execute the approved change

```bash
python scripts/run_enforcer.py execute <proposal-id>
```

---

## CLI Reference

### Adapters

| Command | Description |
|---|---|
| `run_adapters.py git <path> --name <name>` | Ingest a Git repository |
| `run_adapters.py k8s --cluster-name <name>` | Sync a Kubernetes cluster |
| `run_adapters.py github --owner <o> --repo <r>` | Sync GitHub Actions workflow runs |

### Agent

| Command | Description |
|---|---|
| `run_agent.py analyze --pod-id <id>` | Analyze a pod incident |
| `run_agent.py analyze --pod-id <id> --proposal` | Analyze and generate rollback proposal |
| `run_agent.py unhealthy` | List all unhealthy pods |
| `run_agent.py list` | List available agents |

### Approval Gate

| Command | Description |
|---|---|
| `run_gate.py list` | List pending proposals |
| `run_gate.py view <proposal-id>` | View proposal with evidence |
| `run_gate.py approve <id> --reviewer <name>` | Approve a proposal |
| `run_gate.py reject <id> --reviewer <name>` | Reject a proposal |
| `run_gate.py status <proposal-id>` | Check review status |
| `run_gate.py policy --show` | View approval policy |
| `run_gate.py policy --set <key> <value>` | Update policy rule |

### Enforcer

| Command | Description |
|---|---|
| `run_enforcer.py execute <proposal-id>` | Execute an approved proposal |
| `run_enforcer.py execute <id> --dry-run` | Preview without executing |
| `run_enforcer.py list` | List enforcements |
| `run_enforcer.py get <enforcement-id>` | Get enforcement details |
| `run_enforcer.py config --show` | View enforcer configuration |

---

## API Reference

The API server runs at `http://localhost:8000` by default.

### Graph API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/graph/nodes` | Create a node |
| `GET` | `/graph/nodes` | List nodes |
| `GET` | `/graph/nodes/{id}` | Get a node |
| `GET` | `/graph/nodes/{id}/subgraph` | Traverse subgraph |
| `DELETE` | `/graph/nodes/{id}` | Delete a node |
| `POST` | `/graph/edges` | Create an edge |
| `GET` | `/graph/edges` | List edges |
| `POST` | `/graph/query` | Execute Cypher query |
| `GET` | `/graph/schema` | Get graph schema |
| `POST` | `/graph/schema/migrate` | Run migrations |
| `POST` | `/graph/sync` | Run all adapters |

### Agent API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/agent/analyze` | Analyze an incident |
| `GET` | `/agent/analyze/{pod-id}` | Analyze a specific pod |
| `GET` | `/agent/agents` | List registered agents |
| `GET` | `/agent/unhealthy` | List unhealthy pods |

### Coordinator API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/coordinator/proposals` | Submit a proposal |
| `GET` | `/coordinator/proposals` | List proposals |
| `GET` | `/coordinator/proposals/{id}` | Get proposal |
| `GET` | `/coordinator/conflicts` | List conflicts |
| `POST` | `/coordinator/conflicts/resolve` | Resolve a conflict |
| `GET` | `/coordinator/resources/{id}/summary` | Resource conflict summary |
| `POST` | `/coordinator/locks/acquire` | Acquire a resource lock |
| `GET` | `/coordinator/health` | Coordinator health |

### Approval Gate API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/gate/review` | Submit a review decision |
| `GET` | `/gate/proposals/{id}/status` | Get review status |
| `GET` | `/gate/pending` | List pending proposals |
| `GET` | `/gate/policy` | Get approval policy |
| `PUT` | `/gate/policy` | Update approval policy |

### Enforcer API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/enforce/execute` | Execute an approved proposal |
| `GET` | `/enforce/enforcements` | List enforcements |
| `GET` | `/enforce/enforcements/{id}` | Get enforcement details |
| `GET` | `/enforce/config` | Get enforcer config |
| `PUT` | `/enforce/config` | Update enforcer config |
| `GET` | `/enforce/health` | Enforcer health |

### Health

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Server health including Neo4j connectivity |

---

## Project Structure

```
ARGUS_Devops/
├── server/
│   ├── app/
│   │   ├── adapters/           # Data ingestion adapters
│   │   │   ├── base.py         #   Abstract base adapter
│   │   │   ├── git.py          #   Git repository adapter
│   │   │   ├── kubernetes.py   #   Kubernetes adapter
│   │   │   └── github_actions/ #   GitHub Actions adapter
│   │   ├── agents/             # Analysis & proposal agents
│   │   │   ├── base.py         #   Abstract base agent
│   │   │   ├── incident.py     #   Incident analysis agent
│   │   │   ├── proposal.py     #   GitOps proposal agent
│   │   │   ├── queries.py      #   Graph query patterns
│   │   │   └── coordinator.py  #   Agent registry & routing
│   │   ├── coordinator/        # Conflict detection & resolution
│   │   │   ├── detector.py     #   Conflict type detection
│   │   │   ├── analyzer.py     #   Evidence-weighted scoring
│   │   │   ├── resolver.py     #   Resolution strategies
│   │   │   └── store.py        #   Neo4j persistence
│   │   ├── gate/               # Human approval workflow
│   │   │   ├── engine.py       #   Review workflow orchestrator
│   │   │   ├── policy.py       #   Approval policy engine
│   │   │   ├── store.py        #   Neo4j persistence
│   │   │   └── renderer.py     #   CLI evidence display
│   │   ├── enforcer/           # Closed-loop enforcement
│   │   │   ├── precheck.py     #   Safety gate checks
│   │   │   ├── executor.py     #   Action execution engine
│   │   │   ├── verifier.py     #   Post-enforcement verification
│   │   │   └── store.py        #   Neo4j persistence
│   │   ├── graph/              # Graph database layer
│   │   │   ├── schema.py       #   Node/edge type definitions
│   │   │   ├── connection.py   #   Neo4j async driver
│   │   │   └── queries.py      #   Reusable graph queries
│   │   ├── api/                # REST API routes
│   │   │   └── graph.py        #   Graph CRUD endpoints
│   │   ├── models/             # Pydantic data models
│   │   ├── config.py           # Environment-based config
│   │   └── main.py             # FastAPI application entry point
│   ├── scripts/                # CLI entry points
│   │   ├── run_adapters.py     #   Data ingestion CLI
│   │   ├── run_agent.py        #   Analysis agent CLI
│   │   ├── run_gate.py         #   Approval gate CLI
│   │   └── run_enforcer.py     #   Enforcement CLI
│   └── tests/                  # Test suite (208 tests)
│       ├── test_agents.py
│       ├── test_coordinator.py
│       ├── test_enforcer.py
│       ├── test_gate.py
│       ├── test_git_adapter.py
│       ├── test_github_actions_adapter.py
│       ├── test_graph_api.py
│       └── test_k8s_adapter.py
├── deployments/
│   ├── docker-compose.yml      # Stack: Neo4j + API server
│   └── migrations/             # Neo4j schema migrations
├── README.md
└── .gitignore
```

---

## Technology Stack

| Component | Technology |
|---|---|
| **Runtime** | Python 3.12+ |
| **API Framework** | FastAPI with async support |
| **Database** | Neo4j 5.x (graph) |
| **Database Driver** | neo4j (async Python driver) |
| **Validation** | Pydantic v2 |
| **Git** | gitpython |
| **Kubernetes** | kubernetes Python client |
| **HTTP Client** | httpx (async) |
| **Testing** | pytest, pytest-asyncio, httpx (ASGI transport) |
| **Containerization** | Docker, Docker Compose |
| **CLI** | argparse (stdlib) |

---

## Development

### Running tests

```bash
cd server
source .venv/bin/activate

# Run all tests
pytest

# Run specific test file
pytest tests/test_agents.py -v

# Run with coverage
pip install pytest-cov
pytest --cov=app
```

### Adding a new adapter

1. Create the adapter class in `server/app/adapters/` extending `BaseAdapter`
2. Define configuration as a dataclass
3. Implement `async def sync(self) -> dict`
4. Register in `server/app/adapters/__init__.py`
5. Add CLI command in `server/scripts/run_adapters.py`
6. Write tests in `server/tests/`

### Adding a new agent

1. Create the agent class in `server/app/agents/` extending `BaseAgent`
2. Implement `async def analyze(self, query) -> AnalysisResult`
3. Register in `AgentCoordinator` in `server/app/agents/coordinator.py`
4. Write tests in `server/tests/test_agents.py`

---

## Roadmap

| Phase | Status |
|---|---|
| **P1: Graph Schema & Storage** | ✅ Complete |
| **P2: Git Adapter** | ✅ Complete |
| **P3: Kubernetes Adapter** | ✅ Complete |
| **P4: Reference Agent** | ✅ Complete |
| **P5: CI/CD Adapter** | ✅ Complete |
| **P6: Conflict Coordinator** | ✅ Complete |
| **P7: Approval Gate** | ✅ Complete |
| **P8: Enforcement Bridge** | ✅ Complete |
| **P9: Production Infrastructure** | 🔜 Planned |
| **P10: CI/CD Pipeline** | 🔜 Planned |

---

## License

MIT

---

<div align="center">
  <p>Built with Python, Neo4j, and FastAPI</p>
</div>
]]>