from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import get_current_user, require_admin, require_engineer, require_viewer
from app.graph.connection import Neo4jConnection
from app.graph.schema import (
    NodeType,
    EdgeType,
    VALID_EDGE_PAIRS,
    NODE_PROPERTIES,
    EDGE_PROPERTIES,
    SCHEMA_MIGRATIONS_CYPHER,
)
from app.models.graph import (
    NodeCreate,
    NodeResponse,
    EdgeCreate,
    EdgeResponse,
    CypherQuery,
    NodeQueryParams,
)

router = APIRouter(prefix="/graph", tags=["graph"])


@router.post("/schema/migrate")
async def run_migrations(current_user: dict = Depends(require_admin)):
    lines = [l.strip() for l in SCHEMA_MIGRATIONS_CYPHER.split(";") if l.strip()]
    results = []
    for line in lines:
        if line:
            try:
                await Neo4jConnection.run_query(line)
                results.append({"statement": line[:80], "status": "ok"})
            except Exception as e:
                results.append({"statement": line[:80], "status": "error", "error": str(e)})
    return {"migrations": results}


@router.get("/schema")
async def get_schema(current_user: dict = Depends(require_viewer)):
    return {
        "node_types": {nt.value: NODE_PROPERTIES.get(nt, {}) for nt in NodeType},
        "edge_types": {et.value: EDGE_PROPERTIES.get(et, {}) for et in EdgeType},
        "valid_edge_pairs": {
            et.value: [(s.value, t.value) for s, t in pairs]
            for et, pairs in VALID_EDGE_PAIRS.items()
        },
    }


@router.post("/nodes", response_model=NodeResponse, status_code=201)
async def create_node(data: NodeCreate, current_user: dict = Depends(require_engineer)):
    existing = await Neo4jConnection.run_query(
        "MATCH (n {id: $id}) RETURN n", {"id": data.id}
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Node with id '{data.id}' already exists")

    validate_properties(data.type.value, data.properties)

    props_str = ", ".join(f"{k}: ${k}" for k in data.properties)
    query = f"CREATE (n:{data.type.value} {{id: $id, {props_str}}}) RETURN n"
    params = {"id": data.id, **data.properties}
    result = await Neo4jConnection.run_query(query, params)
    node = result[0]["n"]
    return NodeResponse(
        id=node["id"],
        type=data.type.value,
        properties=dict(node.items()),
    )


def _node_props(record: dict) -> dict:
    return dict(record["n"].items())


@router.get("/nodes", response_model=list[NodeResponse])
async def list_nodes(type: str | None = None, limit: int = 100, skip: int = 0, current_user: dict = Depends(require_viewer)):
    if type:
        type_upper = type.upper()
        if type_upper not in NodeType.__members__:
            raise HTTPException(status_code=400, detail=f"Invalid node type: {type}")
        node_type = NodeType[type_upper].value
        query = f"MATCH (n:{node_type}) RETURN n, labels(n) AS type SKIP $skip LIMIT $limit"
    else:
        query = "MATCH (n) RETURN n, labels(n) AS type SKIP $skip LIMIT $limit"
    result = await Neo4jConnection.run_query(query, {"skip": skip, "limit": limit})
    return [
        NodeResponse(
            id=record["n"]["id"],
            type=record["type"][0],
            properties=_node_props(record),
        )
        for record in result
    ]


@router.get("/nodes/{node_id}", response_model=NodeResponse)
async def get_node(node_id: str, current_user: dict = Depends(require_viewer)):
    result = await Neo4jConnection.run_query(
        "MATCH (n {id: $id}) RETURN n, labels(n) AS type", {"id": node_id}
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    record = result[0]
    return NodeResponse(
        id=record["n"]["id"],
        type=record["type"][0],
        properties=_node_props(record),
    )


@router.get("/nodes/{node_id}/subgraph")
async def get_subgraph(node_id: str, depth: int = 2, current_user: dict = Depends(require_viewer)):
    query = f"""
    MATCH (n {{id: $node_id}})
    OPTIONAL MATCH path = (n)-[*1..{depth}]-(connected)
    UNWIND nodes(path) AS all_nodes
    UNWIND relationships(path) AS all_rels
    RETURN collect(DISTINCT {{
        id: all_nodes.id,
        type: labels(all_nodes)[0],
        props: properties(all_nodes)
    }}) AS nodes,
           collect(DISTINCT {{
               source_id: startNode(all_rels).id,
               source_type: labels(startNode(all_rels))[0],
               target_id: endNode(all_rels).id,
               target_type: labels(endNode(all_rels))[0],
               type: type(all_rels),
               props: properties(all_rels)
           }}) AS edges
    """
    result = await Neo4jConnection.run_query(query, {"node_id": node_id})
    if not result:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    return result[0]


@router.delete("/nodes/{node_id}", status_code=204)
async def delete_node(node_id: str, current_user: dict = Depends(require_admin)):
    await Neo4jConnection.run_query(
        "MATCH (n {id: $id}) DETACH DELETE n", {"id": node_id}
    )


@router.post("/edges", response_model=EdgeResponse, status_code=201)
async def create_edge(data: EdgeCreate, current_user: dict = Depends(require_engineer)):
    source_exists = await Neo4jConnection.run_query(
        "MATCH (n {id: $id}) RETURN n", {"id": data.source_id}
    )
    if not source_exists:
        raise HTTPException(status_code=404, detail=f"Source node '{data.source_id}' not found")

    target_exists = await Neo4jConnection.run_query(
        "MATCH (n {id: $id}) RETURN n", {"id": data.target_id}
    )
    if not target_exists:
        raise HTTPException(status_code=404, detail=f"Target node '{data.target_id}' not found")

    if data.type in VALID_EDGE_PAIRS:
        valid = (data.source_type, data.target_type) in VALID_EDGE_PAIRS[data.type]
        if not valid:
            raise HTTPException(
                status_code=400,
                detail=f"Edge '{data.type.value}' not valid between "
                f"{data.source_type.value} and {data.target_type.value}",
            )

    existing = await Neo4jConnection.run_query(
        """
        MATCH (source {id: $source_id})-[r]->(target {id: $target_id})
        WHERE type(r) = $edge_type
        RETURN type(r) AS rel_type LIMIT 1
        """,
        {
            "source_id": data.source_id,
            "target_id": data.target_id,
            "edge_type": data.type.value,
        },
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Edge '{data.type.value}' already exists between "
            f"'{data.source_id}' and '{data.target_id}'",
        )

    props_str = ", ".join(f"{k}: ${k}" for k in data.properties)
    query = f"""
    MATCH (source {{id: $source_id}})
    MATCH (target {{id: $target_id}})
    CREATE (source)-[r:{data.type.value} {{{props_str}}}]->(target)
    RETURN source, properties(r) AS rel_props, target
    """
    params = {"source_id": data.source_id, "target_id": data.target_id, **data.properties}
    result = await Neo4jConnection.run_query(query, params)
    record = result[0]
    return EdgeResponse(
        source_id=record["source"]["id"],
        source_type=data.source_type.value,
        target_id=record["target"]["id"],
        target_type=data.target_type.value,
        type=data.type.value,
        properties=record["rel_props"],
    )


@router.get("/edges")
async def list_edges(type: str | None = None, limit: int = 100, current_user: dict = Depends(require_viewer)):
    if type:
        type_upper = type.upper()
        if type_upper not in EdgeType.__members__:
            raise HTTPException(status_code=400, detail=f"Invalid edge type: {type}")
        edge_type = EdgeType[type_upper].value
        query = f"""
        MATCH (source)-[r:{edge_type}]->(target)
        RETURN source.id AS source_id, labels(source)[0] AS source_type,
               type(r) AS rel_type, properties(r) AS rel_props,
               target.id AS target_id, labels(target)[0] AS target_type
        LIMIT $limit
        """
    else:
        query = """
        MATCH (source)-[r]->(target)
        RETURN source.id AS source_id, labels(source)[0] AS source_type,
               type(r) AS rel_type, properties(r) AS rel_props,
               target.id AS target_id, labels(target)[0] AS target_type
        LIMIT $limit
        """
    result = await Neo4jConnection.run_query(query, {"limit": limit})
    return [
        {
            "source_id": rec["source_id"],
            "source_type": rec["source_type"],
            "target_id": rec["target_id"],
            "target_type": rec["target_type"],
            "type": rec["rel_type"],
            "properties": rec["rel_props"],
        }
        for rec in result
    ]


@router.post("/query", response_model=list[dict])
async def execute_cypher_query(data: CypherQuery, current_user: dict = Depends(require_engineer)):
    lowered = data.query.strip().lower()
    if not any(
        keyword in lowered
        for keyword in ["match", "return", "create", "merge", "optional"]
    ):
        raise HTTPException(status_code=400, detail="Only read and create queries are allowed")
    if any(
        keyword in lowered
        for keyword in ["delete", "detach", "drop", "remove", "set"]
    ):
        raise HTTPException(status_code=400, detail="Destructive queries not allowed via this endpoint")
    try:
        result = await Neo4jConnection.run_query(data.query, data.params)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sync/github", status_code=200)
async def sync_github_actions(
    repo_owner: str,
    repo_name: str,
    token: str = "",
    workflow_name: str | None = None,
    branch: str | None = None,
    max_runs: int = 50,
    current_user: dict = Depends(require_engineer),
):
    try:
        from app.adapters.github_actions import GitHubActionsAdapter, GitHubActionsConfig

        config = GitHubActionsConfig(
            repo_owner=repo_owner,
            repo_name=repo_name,
            token=token,
            workflow_name=workflow_name,
            branch=branch,
            max_runs=max_runs,
        )
        adapter = GitHubActionsAdapter(config)
        result = await adapter.sync()
        return {"adapter": "github_actions", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync", status_code=200)
async def run_sync(
    source_type: str = "all",
    source: str | None = None,
    repo_name: str | None = None,
    cluster_name: str | None = None,
    current_user: dict = Depends(require_engineer),
):
    results = []

    if source_type in ("git", "all") and source and repo_name:
        try:
            from app.adapters.git import GitAdapter, GitAdapterConfig

            config = GitAdapterConfig(source=source, repo_name=repo_name)
            adapter = GitAdapter(config)
            result = await adapter.sync()
            results.append({"adapter": "git", **result})
        except Exception as e:
            results.append({"adapter": "git", "error": str(e)})

    if source_type in ("k8s", "all"):
        try:
            from app.adapters.kubernetes import K8sAdapter, K8sAdapterConfig

            config = K8sAdapterConfig(
                cluster_name=cluster_name or "argus-cluster"
            )
            adapter = K8sAdapter(config)
            result = await adapter.sync()
            results.append({"adapter": "kubernetes", **result})
        except Exception as e:
            results.append({"adapter": "kubernetes", "error": str(e)})

    return {"results": results}


def validate_properties(node_type: str, properties: dict):
    expected = NODE_PROPERTIES.get(NodeType(node_type), {})
    required_fields = {"id"}
    for key in properties:
        if key not in expected and key != "id":
            raise HTTPException(
                status_code=400,
                detail=f"Unknown property '{key}' for node type '{node_type}'. "
                f"Allowed: {list(expected.keys())}",
            )
