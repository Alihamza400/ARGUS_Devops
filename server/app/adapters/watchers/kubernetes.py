from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.agents.coordinator import AgentCoordinator
from app.agents.models import AgentQuery
from app.config import settings
from app.graph.connection import Neo4jConnection
from app.graph.schema import NodeType, EdgeType


class K8sWatcher:
    UNHEALTHY_PHASES = {
        "CrashLoopBackOff",
        "Error",
        "ImagePullBackOff",
        "Init:CrashLoopBackOff",
        "OOMKilling",
        "RunContainerError",
        "CreateContainerConfigError",
    }

    def __init__(self):
        self._watch_task: asyncio.Task | None = None
        self._running = False

    def _load_k8s_config(self):
        try:
            if settings.k8s_kubeconfig_path:
                config.load_kube_config(config_file=settings.k8s_kubeconfig_path)
            else:
                config.load_incluster_config()
        except Exception:
            config.load_kube_config()

    async def watch_pods(self):
        self._load_k8s_config()
        v1 = client.CoreV1Api()
        field_selector = "involvedObject.kind=Pod"
        namespace = settings.k8s_watcher_namespace or None

        while self._running:
            try:
                if namespace:
                    stream = v1.list_namespaced_event(
                        namespace=namespace,
                        watch=True,
                        timeout_seconds=5,
                    )
                else:
                    stream = v1.list_event_for_all_namespaces(
                        watch=True,
                        timeout_seconds=5,
                    )

                for event in stream:
                    if not self._running:
                        break
                    await self._process_event(event)

            except ApiException as e:
                if e.status != 504:
                    print(f"K8s watcher API error: {e}")
            except Exception as e:
                print(f"K8s watcher error: {e}")

            await asyncio.sleep(1)

    async def _process_event(self, event):
        raw = event.get("raw_object", event.get("object", {}))
        involved = raw.get("involvedObject", {})
        if involved.get("kind") != "Pod":
            return

        reason = raw.get("reason", "")
        message = raw.get("message", "")
        pod_name = involved.get("name", "")
        pod_namespace = involved.get("namespace", "")

        if reason not in self.UNHEALTHY_PHASES:
            return

        incident_id = f"inc-{pod_namespace}-{pod_name}-{datetime.utcnow().timestamp():.0f}"
        existing = await Neo4jConnection.run_query(
            "MATCH (n:Incident {id: $id}) RETURN n LIMIT 1",
            {"id": incident_id},
        )
        if existing:
            return

        pod_id = f"pod-{pod_namespace}-{pod_name}"
        await Neo4jConnection.run_query(
            """
            CREATE (n:Incident {
                id: $id, type: $type, severity: $severity,
                status: $status, message: $message,
                source: $source, detected_at: $detected_at
            })
            RETURN n
            """,
            {
                "id": incident_id,
                "type": "pod_crash",
                "severity": "critical",
                "status": "open",
                "message": message[:500],
                "source": "k8s-watcher",
                "detected_at": datetime.utcnow().isoformat(),
            },
        )

        await Neo4jConnection.run_query(
            """
            MATCH (source:Incident {id: $incident_id})
            MATCH (target:Pod {id: $pod_id})
            MERGE (source)-[:DETECTED_IN {detected_at: $detected_at}]->(target)
            """,
            {
                "incident_id": incident_id,
                "pod_id": pod_id,
                "detected_at": datetime.utcnow().isoformat(),
            },
        )

        try:
            analysis = await AgentCoordinator.analyze(
                AgentQuery(
                    query=f"Auto-detected incident: {message[:200]}",
                    pod_id=pod_id,
                    generate_proposal=True,
                )
            )
            return {
                "incident_id": incident_id,
                "analysis": analysis.analysis.summary if analysis.analysis else None,
                "proposal": analysis.proposal.title if analysis.proposal else None,
            }
        except Exception as e:
            print(f"Auto-analysis failed for {incident_id}: {e}")
            return {"incident_id": incident_id, "error": str(e)}

    async def start(self):
        if self._running:
            return
        self._running = True
        self._watch_task = asyncio.create_task(self.watch_pods())
        print("K8s watcher started")

    async def stop(self):
        self._running = False
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None
        print("K8s watcher stopped")


k8s_watcher = K8sWatcher()
