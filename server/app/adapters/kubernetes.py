from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.adapters.base import BaseAdapter
from app.graph.schema import NodeType, EdgeType


@dataclass
class K8sAdapterConfig:
    cluster_name: str = "default-cluster"
    cluster_id: str = ""
    namespace: str = ""
    kubeconfig_path: str | None = None
    sync_namespaces: bool = True
    sync_services: bool = True
    sync_deployments: bool = True
    sync_pods: bool = True


class K8sAdapter(BaseAdapter):
    def __init__(self, config: K8sAdapterConfig):
        self.config = config
        if not config.cluster_id:
            config.cluster_id = f"cluster-{uuid.uuid4().hex[:8]}"

    async def sync(self) -> dict:
        result = {
            "cluster_id": self.config.cluster_id,
            "nodes_created": 0,
            "edges_created": 0,
            "errors": [],
        }

        try:
            import kubernetes.client as k8s_client
            import kubernetes.config as k8s_config
        except ImportError:
            result["errors"].append("kubernetes package not installed")
            return result

        try:
            if self.config.kubeconfig_path:
                k8s_config.load_kube_config(self.config.kubeconfig_path)
            else:
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
        except Exception as e:
            result["errors"].append(f"failed to load k8s config: {e}")
            return result

        v1 = k8s_client.CoreV1Api()
        apps_v1 = k8s_client.AppsV1Api()

        cluster_label = await self._sync_cluster(v1)
        if cluster_label:
            result["nodes_created"] += 1

        if self.config.sync_namespaces:
            try:
                ns_created = await self._sync_namespaces(v1, cluster_label)
                result["nodes_created"] += ns_created["nodes"]
                result["edges_created"] += ns_created["edges"]
            except Exception as e:
                result["errors"].append(f"namespaces: {e}")

        if self.config.sync_services:
            try:
                svc_created = await self._sync_services(v1, cluster_label)
                result["nodes_created"] += svc_created["nodes"]
                result["edges_created"] += svc_created["edges"]
            except Exception as e:
                result["errors"].append(f"services: {e}")

        if self.config.sync_deployments:
            try:
                dep_created = await self._sync_deployments(apps_v1, cluster_label)
                result["nodes_created"] += dep_created["nodes"]
                result["edges_created"] += dep_created["edges"]
            except Exception as e:
                result["errors"].append(f"deployments: {e}")

        if self.config.sync_pods:
            try:
                pod_created = await self._sync_pods(v1, cluster_label)
                result["nodes_created"] += pod_created["nodes"]
                result["edges_created"] += pod_created["edges"]
            except Exception as e:
                result["errors"].append(f"pods: {e}")

        return result

    async def _sync_cluster(self, v1: Any) -> str | None:
        try:
            version = v1.get_code().git_version
        except Exception:
            version = "unknown"

        created = await self._upsert_node(
            NodeType.CLUSTER,
            self.config.cluster_id,
            {
                "name": self.config.cluster_name,
                "version": version,
                "provider": "kubernetes",
            },
        )
        return self.config.cluster_id if created else None

    async def _sync_namespaces(self, v1: Any, cluster_id: str | None) -> dict:
        ns_list = v1.list_namespace().items
        nodes = 0
        edges = 0

        for ns in ns_list:
            ns_name = ns.metadata.name
            if self.config.namespace and ns_name != self.config.namespace:
                continue

            ns_id = f"ns-{ns_name}"
            label_str = str(dict(ns.metadata.labels or {}))
            created = await self._upsert_node(
                NodeType.NAMESPACE,
                ns_id,
                {"name": ns_name, "labels": label_str},
            )
            if created:
                nodes += 1

            if cluster_id:
                edge_created = await self._upsert_edge(
                    EdgeType.IN,
                    ns_id,
                    cluster_id,
                )
                if edge_created:
                    edges += 1

        return {"nodes": nodes, "edges": edges}

    async def _sync_services(self, v1: Any, cluster_id: str | None) -> dict:
        field_selector = ""
        if self.config.namespace:
            field_selector = f"metadata.namespace={self.config.namespace}"

        svc_list = v1.list_service_for_all_namespaces(field_selector=field_selector).items
        nodes = 0
        edges = 0

        for svc in svc_list:
            ns_name = svc.metadata.namespace
            svc_name = svc.metadata.name
            svc_id = f"svc-{ns_name}-{svc_name}"

            image = ""
            if svc.spec.selector:
                image = str(svc.spec.selector)

            created = await self._upsert_node(
                NodeType.SERVICE,
                svc_id,
                {
                    "name": svc_name,
                    "namespace": ns_name,
                    "image": image,
                    "replicas": 0,
                },
            )
            if created:
                nodes += 1

            ns_id = f"ns-{ns_name}"
            edge_created = await self._upsert_edge(
                EdgeType.IN,
                svc_id,
                ns_id,
            )
            if edge_created:
                edges += 1

        return {"nodes": nodes, "edges": edges}

    async def _sync_deployments(self, apps_v1: Any, cluster_id: str | None) -> dict:
        field_selector = ""
        if self.config.namespace:
            field_selector = f"metadata.namespace={self.config.namespace}"

        dep_list = apps_v1.list_deployment_for_all_namespaces(
            field_selector=field_selector
        ).items
        nodes = 0
        edges = 0

        for dep in dep_list:
            ns_name = dep.metadata.namespace
            dep_name = dep.metadata.name
            dep_id = f"dep-{ns_name}-{dep_name}"

            strategy = ""
            if dep.spec.strategy and dep.spec.strategy.type:
                strategy = dep.spec.strategy.type

            created = await self._upsert_node(
                NodeType.DEPLOYMENT,
                dep_id,
                {
                    "name": dep_name,
                    "strategy": strategy,
                    "revision": 0,
                    "namespace": ns_name,
                },
            )
            if created:
                nodes += 1

            ns_id = f"ns-{ns_name}"
            edge_created = await self._upsert_edge(
                EdgeType.IN,
                dep_id,
                ns_id,
            )
            if edge_created:
                edges += 1

            if dep.spec.selector and dep.spec.selector.match_labels:
                selector = dep.spec.selector.match_labels
                svc_id = f"svc-{ns_name}-{dep_name}"
                svc_edge = await self._upsert_edge(
                    EdgeType.DEPLOYS,
                    dep_id,
                    svc_id,
                )
                if svc_edge:
                    edges += 1

        return {"nodes": nodes, "edges": edges}

    async def _sync_pods(self, v1: Any, cluster_id: str | None) -> dict:
        field_selector = ""
        if self.config.namespace:
            field_selector = f"metadata.namespace={self.config.namespace}"

        pod_list = v1.list_pod_for_all_namespaces(field_selector=field_selector).items
        nodes = 0
        edges = 0

        for pod in pod_list:
            ns_name = pod.metadata.namespace
            pod_name = pod.metadata.name
            pod_id = f"pod-{ns_name}-{pod_name}"

            phase = pod.status.phase or "Unknown"
            node_name = pod.spec.node_name or ""

            cpu_req = ""
            cpu_lim = ""
            mem_req = ""
            mem_lim = ""
            if pod.spec.containers:
                c = pod.spec.containers[0]
                if c.resources:
                    if c.resources.requests:
                        cpu_req = c.resources.requests.get("cpu", "")
                        mem_req = c.resources.requests.get("memory", "")
                    if c.resources.limits:
                        cpu_lim = c.resources.limits.get("cpu", "")
                        mem_lim = c.resources.limits.get("memory", "")

            created = await self._upsert_node(
                NodeType.POD,
                pod_id,
                {
                    "name": pod_name,
                    "phase": phase,
                    "node": node_name,
                    "namespace": ns_name,
                    "cpu_request": cpu_req,
                    "cpu_limit": cpu_lim,
                    "memory_request": mem_req,
                    "memory_limit": mem_lim,
                },
            )
            if created:
                nodes += 1

            ns_id = f"ns-{ns_name}"
            edge_created = await self._upsert_edge(
                EdgeType.IN,
                pod_id,
                ns_id,
            )
            if edge_created:
                edges += 1

            if cluster_id:
                edge_created = await self._upsert_edge(
                    EdgeType.RUNS_ON,
                    pod_id,
                    cluster_id,
                )
                if edge_created:
                    edges += 1

            if pod.metadata.owner_references:
                for owner in pod.metadata.owner_references:
                    if owner.kind == "ReplicaSet":
                        svc_id = f"svc-{ns_name}-{owner.name.rsplit('-', 1)[0]}"
                        bel_edge = await self._upsert_edge(
                            EdgeType.BELONGS_TO,
                            pod_id,
                            svc_id,
                        )
                        if bel_edge:
                            edges += 1

        return {"nodes": nodes, "edges": edges}
