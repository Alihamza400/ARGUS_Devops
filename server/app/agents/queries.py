from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.graph.connection import Neo4jConnection


@dataclass
class PodInfo:
    id: str
    name: str
    phase: str
    namespace: str
    node: str
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str


@dataclass
class ServiceInfo:
    id: str
    name: str
    namespace: str
    image: str
    replicas: int


@dataclass
class CommitInfo:
    sha: str
    message: str
    author: str
    email: str
    timestamp: datetime
    branch: str


@dataclass
class DeploymentInfo:
    id: str
    name: str
    namespace: str
    strategy: str
    revision: int


@dataclass
class NamespaceInfo:
    id: str
    name: str
    labels: str


@dataclass
class RepositoryInfo:
    id: str
    name: str
    url: str
    default_branch: str


class GraphQueries:
    TIMEOUT_SECONDS = 10

    @staticmethod
    async def find_pod_by_id(pod_id: str) -> PodInfo | None:
        result = await Neo4jConnection.run_query(
            "MATCH (n:Pod {id: $id}) RETURN n LIMIT 1",
            {"id": pod_id},
        )
        if not result:
            return None
        n = result[0]["n"]
        return PodInfo(
            id=n.get("id", ""),
            name=n.get("name", ""),
            phase=n.get("phase", "Unknown"),
            namespace=n.get("namespace", ""),
            node=n.get("node", ""),
            cpu_request=n.get("cpu_request", ""),
            cpu_limit=n.get("cpu_limit", ""),
            memory_request=n.get("memory_request", ""),
            memory_limit=n.get("memory_limit", ""),
        )

    @staticmethod
    async def find_pods_by_name(name: str, namespace: str | None = None) -> list[PodInfo]:
        if namespace:
            result = await Neo4jConnection.run_query(
                "MATCH (n:Pod) WHERE toLower(n.name) CONTAINS toLower($name) AND n.namespace = $ns RETURN n",
                {"name": name, "ns": namespace},
            )
        else:
            result = await Neo4jConnection.run_query(
                "MATCH (n:Pod) WHERE toLower(n.name) CONTAINS toLower($name) RETURN n",
                {"name": name},
            )
        return [
            PodInfo(
                id=n["n"].get("id", ""),
                name=n["n"].get("name", ""),
                phase=n["n"].get("phase", "Unknown"),
                namespace=n["n"].get("namespace", ""),
                node=n["n"].get("node", ""),
                cpu_request=n["n"].get("cpu_request", ""),
                cpu_limit=n["n"].get("cpu_limit", ""),
                memory_request=n["n"].get("memory_request", ""),
                memory_limit=n["n"].get("memory_limit", ""),
            )
            for n in result
        ]

    @staticmethod
    async def find_pods_by_phase(phase: str, limit: int = 50) -> list[PodInfo]:
        result = await Neo4jConnection.run_query(
            "MATCH (n:Pod {phase: $phase}) RETURN n LIMIT $limit",
            {"phase": phase, "limit": limit},
        )
        return [
            PodInfo(
                id=n["n"].get("id", ""),
                name=n["n"].get("name", ""),
                phase=n["n"].get("phase", "Unknown"),
                namespace=n["n"].get("namespace", ""),
                node=n["n"].get("node", ""),
                cpu_request=n["n"].get("cpu_request", ""),
                cpu_limit=n["n"].get("cpu_limit", ""),
                memory_request=n["n"].get("memory_request", ""),
                memory_limit=n["n"].get("memory_limit", ""),
            )
            for n in result
        ]

    @staticmethod
    async def get_service_for_pod(pod_id: str) -> ServiceInfo | None:
        result = await Neo4jConnection.run_query(
            """
            MATCH (pod:Pod {id: $pod_id})-[:BELONGS_TO]->(svc:Service)
            RETURN svc LIMIT 1
            """,
            {"pod_id": pod_id},
        )
        if not result:
            result = await Neo4jConnection.run_query(
                """
                MATCH (pod:Pod {id: $pod_id})-[:IN]->(ns:Namespace)<-[:IN]-(svc:Service)
                RETURN svc LIMIT 1
                """,
                {"pod_id": pod_id},
            )
        if not result:
            return None
        s = result[0]["svc"]
        return ServiceInfo(
            id=s.get("id", ""),
            name=s.get("name", ""),
            namespace=s.get("namespace", ""),
            image=s.get("image", ""),
            replicas=s.get("replicas", 0),
        )

    @staticmethod
    async def get_namespace_for_pod(pod_id: str) -> NamespaceInfo | None:
        result = await Neo4jConnection.run_query(
            """
            MATCH (pod:Pod {id: $pod_id})-[:IN]->(ns:Namespace)
            RETURN ns LIMIT 1
            """,
            {"pod_id": pod_id},
        )
        if not result:
            return None
        n = result[0]["ns"]
        return NamespaceInfo(
            id=n.get("id", ""),
            name=n.get("name", ""),
            labels=n.get("labels", ""),
        )

    @staticmethod
    async def get_repo_for_service(svc_id: str) -> RepositoryInfo | None:
        result = await Neo4jConnection.run_query(
            """
            MATCH (svc:Service {id: $svc_id})-[:DEPLOYED_FROM]->(repo:Repository)
            RETURN repo LIMIT 1
            """,
            {"svc_id": svc_id},
        )
        if not result:
            return None
        r = result[0]["repo"]
        return RepositoryInfo(
            id=r.get("id", ""),
            name=r.get("name", ""),
            url=r.get("url", ""),
            default_branch=r.get("default_branch", ""),
        )

    @staticmethod
    async def get_commits_for_repo(
        repo_id: str, max_count: int = 20
    ) -> list[CommitInfo]:
        result = await Neo4jConnection.run_query(
            """
            MATCH (c:Commit)-[:IS_IN]->(r:Repository {id: $repo_id})
            RETURN c
            ORDER BY c.timestamp DESC
            LIMIT $limit
            """,
            {"repo_id": repo_id, "limit": max_count},
        )
        return [
            CommitInfo(
                sha=c["c"].get("sha", ""),
                message=c["c"].get("message", ""),
                author=c["c"].get("author", ""),
                email=c["c"].get("email", ""),
                timestamp=c["c"].get("timestamp", datetime.min),
                branch=c["c"].get("branch", ""),
            )
            for c in result
        ]

    @staticmethod
    async def get_deployments_for_service(svc_id: str) -> list[DeploymentInfo]:
        result = await Neo4jConnection.run_query(
            """
            MATCH (dep:Deployment)-[:DEPLOYS]->(svc:Service {id: $svc_id})
            RETURN dep
            """,
            {"svc_id": svc_id},
        )
        return [
            DeploymentInfo(
                id=d["dep"].get("id", ""),
                name=d["dep"].get("name", ""),
                namespace=d["dep"].get("namespace", ""),
                strategy=d["dep"].get("strategy", ""),
                revision=d["dep"].get("revision", 0),
            )
            for d in result
        ]

    @staticmethod
    async def get_pods_for_service(svc_id: str) -> list[PodInfo]:
        result = await Neo4jConnection.run_query(
            """
            MATCH (svc:Service {id: $svc_id})<-[:BELONGS_TO]-(pod:Pod)
            RETURN pod
            """,
            {"svc_id": svc_id},
        )
        pods = [
            PodInfo(
                id=p["pod"].get("id", ""),
                name=p["pod"].get("name", ""),
                phase=p["pod"].get("phase", "Unknown"),
                namespace=p["pod"].get("namespace", ""),
                node=p["pod"].get("node", ""),
                cpu_request=p["pod"].get("cpu_request", ""),
                cpu_limit=p["pod"].get("cpu_limit", ""),
                memory_request=p["pod"].get("memory_request", ""),
                memory_limit=p["pod"].get("memory_limit", ""),
            )
            for p in result
        ]
        return pods

    @staticmethod
    async def get_provenance_chain(
        pod_id: str,
    ) -> dict[str, Any]:
        pod = await GraphQueries.find_pod_by_id(pod_id)
        if not pod:
            return {"pod": None, "namespace": None, "service": None, "repository": None, "commits": []}

        namespace = await GraphQueries.get_namespace_for_pod(pod_id)
        service = await GraphQueries.get_service_for_pod(pod_id)

        repo = None
        commits = []
        if service:
            repo = await GraphQueries.get_repo_for_service(service.id)
            if repo:
                commits = await GraphQueries.get_commits_for_repo(repo.id)

        return {
            "pod": pod,
            "namespace": namespace,
            "service": service,
            "repository": repo,
            "commits": commits,
        }

    @staticmethod
    async def get_unhealthy_pods(limit: int = 20) -> list[PodInfo]:
        unhealthy_phases = ["CrashLoopBackOff", "Error", "ImagePullBackOff", "Init:CrashLoopBackOff", "OOMKilling", "Pending"]
        results = []
        for phase in unhealthy_phases:
            pods = await GraphQueries.find_pods_by_phase(phase, limit)
            results.extend(pods)
        return results

    @staticmethod
    async def search_pods(query: str, limit: int = 20) -> list[PodInfo]:
        result = await Neo4jConnection.run_query(
            """
            MATCH (n:Pod)
            WHERE toLower(n.name) CONTAINS toLower($query)
               OR toLower(n.namespace) CONTAINS toLower($query)
               OR toLower(n.phase) CONTAINS toLower($query)
            RETURN n LIMIT $limit
            """,
            {"query": query, "limit": limit},
        )
        return [
            PodInfo(
                id=n["n"].get("id", ""),
                name=n["n"].get("name", ""),
                phase=n["n"].get("phase", "Unknown"),
                namespace=n["n"].get("namespace", ""),
                node=n["n"].get("node", ""),
                cpu_request=n["n"].get("cpu_request", ""),
                cpu_limit=n["n"].get("cpu_limit", ""),
                memory_request=n["n"].get("memory_request", ""),
                memory_limit=n["n"].get("memory_limit", ""),
            )
            for n in result
        ]
