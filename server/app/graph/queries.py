CREATE_NODE = """
CREATE (n:{node_type} {{
    id: $id,
    {props}
}})
RETURN n
"""

GET_NODE = """
MATCH (n {{id: $id}})
RETURN n, labels(n) AS type
"""

GET_NODES_BY_TYPE = """
MATCH (n:{node_type})
RETURN n, labels(n) AS type
SKIP $skip
LIMIT $limit
"""

CREATE_EDGE = """
MATCH (source {{id: $source_id}})
MATCH (target {{id: $target_id}})
CREATE (source)-[r:{edge_type} {{
    {props}
}}]->(target)
RETURN source, r, target
"""

TRAVERSE_PATH = """
MATCH path = (start {{id: $start_id}})-[*1..{max_depth}]->(end {{id: $end_id}})
RETURN path
"""

GET_CONNECTED_SUBGRAPH = """
MATCH (n {{id: $node_id}})
OPTIONAL MATCH (n)-[r]-(connected)
RETURN n, labels(n) AS node_type,
       collect({{source: startNode(r), rel: type(r), target: endNode(r)}}) AS edges
"""

FIND_PATHS_BETWEEN = """
MATCH path = (start {{id: $source_id}})-[*1..{max_depth}]-(end {{id: $target_id}})
RETURN path
LIMIT 10
"""

SEARCH_NODES = """
MATCH (n:{node_type})
WHERE n.$field CONTAINS $value
RETURN n, labels(n) AS type
LIMIT $limit
"""

DELETE_NODE = """
MATCH (n {{id: $id}})
DETACH DELETE n
"""
