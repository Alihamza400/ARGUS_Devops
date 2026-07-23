from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.graph.schema import NodeType, EdgeType


class NodeCreate(BaseModel):
    type: NodeType
    id: str
    properties: dict[str, Any] = Field(default_factory=dict)


class NodeResponse(BaseModel):
    id: str
    type: str
    properties: dict[str, Any]
    created_at: datetime | None = None


class EdgeCreate(BaseModel):
    source_type: NodeType
    source_id: str
    target_type: NodeType
    target_id: str
    type: EdgeType
    properties: dict[str, Any] = Field(default_factory=dict)


class EdgeResponse(BaseModel):
    source_id: str
    source_type: str
    target_id: str
    target_type: str
    type: str
    properties: dict[str, Any]


class CypherQuery(BaseModel):
    query: str
    params: dict[str, Any] = Field(default_factory=dict)


class GraphPath(BaseModel):
    nodes: list[NodeResponse]
    edges: list[EdgeResponse]


class NodeQueryParams(BaseModel):
    type: NodeType | None = None
    limit: int = 100
    skip: int = 0
    filters: dict[str, Any] = Field(default_factory=dict)
