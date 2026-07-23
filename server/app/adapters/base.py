from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.graph.connection import Neo4jConnection
from app.graph.schema import NodeType, EdgeType


class BaseAdapter(ABC):
    @abstractmethod
    async def sync(self) -> dict:
        ...

    async def _upsert_node(
        self, node_type: NodeType, node_id: str, properties: dict[str, Any]
    ) -> bool:
        existing = await Neo4jConnection.run_query(
            "MATCH (n:" + node_type.value + " {id: $id}) RETURN n",
            {"id": node_id},
        )
        if existing:
            return False

        props_list = ", ".join(f"{k}: ${k}" for k in properties)
        await Neo4jConnection.run_query(
            f"CREATE (n:{node_type.value} {{id: $id, {props_list}}}) RETURN n",
            {"id": node_id, **properties},
        )
        return True

    async def _upsert_edge(
        self,
        edge_type: EdgeType,
        source_id: str,
        target_id: str,
        properties: dict[str, Any] | None = None,
    ) -> bool:
        existing = await Neo4jConnection.run_query(
            """
            MATCH (source {id: $source_id})-[r:"""
            + edge_type.value
            + """]->(target {id: $target_id})
            RETURN r LIMIT 1
            """,
            {"source_id": source_id, "target_id": target_id},
        )
        if existing:
            return False

        if properties:
            props_list = ", ".join(f"{k}: ${k}" for k in properties)
            await Neo4jConnection.run_query(
                """
                MATCH (source {id: $source_id})
                MATCH (target {id: $target_id})
                CREATE (source)-[r:"""
                + edge_type.value
                + """ {"""
                + props_list
                + """}]->(target)
                """,
                {"source_id": source_id, "target_id": target_id, **properties},
            )
        else:
            await Neo4jConnection.run_query(
                """
                MATCH (source {id: $source_id})
                MATCH (target {id: $target_id})
                CREATE (source)-[r:"""
                + edge_type.value
                + """]->(target)
                """,
                {"source_id": source_id, "target_id": target_id},
            )
        return True

    async def _node_exists(self, node_id: str) -> bool:
        result = await Neo4jConnection.run_query(
            "MATCH (n {id: $id}) RETURN n LIMIT 1",
            {"id": node_id},
        )
        return len(result) > 0

    async def _count_created(self, label: str, repo_id: str | None = None) -> int:
        if repo_id:
            result = await Neo4jConnection.run_query(
                "MATCH (n:" + label + " {repo_id: $repo_id}) RETURN count(n) AS cnt",
                {"repo_id": repo_id},
            )
        else:
            result = await Neo4jConnection.run_query(
                "MATCH (n:" + label + ") RETURN count(n) AS cnt",
            )
        return result[0]["cnt"] if result else 0
