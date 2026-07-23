from __future__ import annotations

import time
from datetime import datetime, timezone

from app.agents.base import BaseAgent
from app.agents.models import (
    AnalysisResult,
    AgentQuery,
    EvidenceCategory,
    EvidenceItem,
    Severity,
    TimelineEvent,
)
from app.agents.queries import GraphQueries


class IncidentAgent(BaseAgent):
    agent_type = "incident"
    agent_version = "1.0.0"
    description = "Analyzes pod and service incidents by traversing the provenance graph"

    async def analyze(self, query: AgentQuery) -> AnalysisResult:
        start = time.perf_counter()
        errors: list[str] = []
        evidence: list[EvidenceItem] = []
        timeline: list[TimelineEvent] = []
        suggestions: list[str] = []

        pod_id = query.pod_id
        if not pod_id and query.pod_name:
            pods = await GraphQueries.find_pods_by_name(
                query.pod_name, query.namespace
            )
            if not pods:
                pods = await GraphQueries.search_pods(query.pod_name)
            if pods:
                pod_id = pods[0].id
            else:
                return AnalysisResult(
                    summary=f"No pods found matching '{query.pod_name}'",
                    severity=Severity.INFO,
                    confidence=1.0,
                    evidence=[],
                    suggestions=["Verify the pod name and namespace"],
                    query_time_ms=(time.perf_counter() - start) * 1000,
                    agent=self.agent_type,
                    agent_version=self.agent_version,
                )

        chain = await GraphQueries.get_provenance_chain(pod_id)

        pod = chain["pod"]
        if not pod:
            return AnalysisResult(
                summary=f"Pod '{pod_id}' not found in the graph",
                severity=Severity.INFO,
                confidence=1.0,
                evidence=[],
                suggestions=[
                    "Ingest the cluster with the K8s adapter first",
                    "Verify the pod ID is correct",
                ],
                query_time_ms=(time.perf_counter() - start) * 1000,
                agent=self.agent_type,
                agent_version=self.agent_version,
            )

        severity = self._classify_severity(pod.phase)

        evidence.append(
            EvidenceItem(
                category=EvidenceCategory.POD_STATE,
                label=f"Pod {pod.name}",
                detail=f"Phase: {pod.phase} | Node: {pod.node} | Namespace: {pod.namespace}",
                source_id=pod.id,
                source_type="Pod",
                status=pod.phase,
                timestamp=datetime.now(timezone.utc),
                properties={
                    "cpu_request": pod.cpu_request,
                    "cpu_limit": pod.cpu_limit,
                    "memory_request": pod.memory_request,
                    "memory_limit": pod.memory_limit,
                },
            )
        )

        namespace = chain["namespace"]
        if namespace:
            evidence.append(
                EvidenceItem(
                    category=EvidenceCategory.POD_STATE,
                    label=f"Namespace: {namespace.name}",
                    detail=f"Labels: {namespace.labels}",
                    source_id=namespace.id,
                    source_type="Namespace",
                    status="active",
                )
            )

        service = chain["service"]
        if service:
            evidence.append(
                EvidenceItem(
                    category=EvidenceCategory.SERVICE_CONFIG,
                    label=f"Service: {service.name}",
                    detail=f"Image: {service.image} | Replicas: {service.replicas} | Namespace: {service.namespace}",
                    source_id=service.id,
                    source_type="Service",
                    status="active",
                    properties={"replicas": service.replicas, "image": service.image},
                )
            )

            deployments = await GraphQueries.get_deployments_for_service(service.id)
            for dep in deployments:
                evidence.append(
                    EvidenceItem(
                        category=EvidenceCategory.DEPLOYMENT_HISTORY,
                        label=f"Deployment: {dep.name}",
                        detail=f"Strategy: {dep.strategy} | Revision: {dep.revision}",
                        source_id=dep.id,
                        source_type="Deployment",
                        status=dep.strategy,
                        properties={"revision": dep.revision},
                    )
                )

            all_pods = await GraphQueries.get_pods_for_service(service.id)
            unhealthy = [p for p in all_pods if p.phase != "Running" and p.phase != "Succeeded"]
            if unhealthy:
                evidence.append(
                    EvidenceItem(
                        category=EvidenceCategory.POD_STATE,
                        label=f"{len(unhealthy)}/{len(all_pods)} pods unhealthy",
                        detail=f"Unhealthy pods for service {service.name}: {', '.join(p.name for p in unhealthy[:5])}",
                        source_id=service.id,
                        source_type="Service",
                        status="degraded",
                    )
                )
        if pod.phase in ("CrashLoopBackOff", "Error") and pod.memory_limit:
            if pod.memory_limit.endswith("Gi") or (pod.memory_limit.endswith("Mi") and int(pod.memory_limit[:-2]) > 512):
                suggestions.append(
                    f"Pod is using {pod.memory_limit} memory limit. Check if this is appropriate for the workload."
                )
        if pod.phase == "OOMKilling" or pod.phase == "CrashLoopBackOff":
            suggestions.append("Consider increasing memory limits or investigating memory leaks")
        if pod.phase == "ImagePullBackOff":
            suggestions.append("Verify the container image tag exists and is accessible")
        if pod.phase == "Pending":
            suggestions.append("Check cluster resource availability (CPU, memory, nodes)")

        repo = chain["repository"]
        commits = chain["commits"]

        if repo:
            evidence.append(
                EvidenceItem(
                    category=EvidenceCategory.DEPLOYMENT_HISTORY,
                    label=f"Repository: {repo.name}",
                    detail=f"URL: {repo.url} | Default branch: {repo.default_branch}",
                    source_id=repo.id,
                    source_type="Repository",
                    status="active",
                )
            )

            recent_commits = commits[: query.max_commits]
            for c in recent_commits:
                event_time = c.timestamp if isinstance(c.timestamp, datetime) else datetime.min
                evidence.append(
                    EvidenceItem(
                        category=EvidenceCategory.COMMIT_CHANGE,
                        label=f"Commit {c.sha[:12]}",
                        detail=f"{c.message} — {c.author}",
                        source_id=c.sha,
                        source_type="Commit",
                        status="merged",
                        timestamp=event_time,
                        properties={"author_email": c.email, "branch": c.branch},
                    )
                )
                timeline.append(
                    TimelineEvent(
                        at=event_time,
                        event_type="commit",
                        summary=c.message,
                        detail=f"By {c.author} ({c.email}) on {c.branch}",
                        source_id=c.sha,
                    )
                )

            if commits:
                latest = commits[0]
                suggestions.append(
                    f"Latest commit ({latest.sha[:12]}) by {latest.author}: "
                    f"'{latest.message}' — review if this change could be related"
                )

        summary = self._build_summary(pod, service, repo, commits)

        return AnalysisResult(
            summary=summary,
            severity=severity,
            confidence=self._compute_confidence(chain),
            evidence=evidence,
            timeline=timeline,
            suggestions=suggestions,
            query_time_ms=(time.perf_counter() - start) * 1000,
            agent=self.agent_type,
            agent_version=self.agent_version,
        )

    def _build_summary(
        self, pod, service, repo, commits
    ) -> str:
        parts = [f"Pod '{pod.name}' is in '{pod.phase}' state"]
        if service:
            parts.append(f"running under service '{service.name}'")
        if repo:
            parts.append(f"deployed from repository '{repo.name}'")
        if commits:
            latest = commits[0]
            parts.append(
                f"latest commit '{latest.sha[:12]}' by {latest.author}: '{latest.message}'"
            )
        return " | ".join(parts)

    def _classify_severity(self, phase: str) -> Severity:
        critical = {"CrashLoopBackOff", "Error", "OOMKilling", "ImagePullBackOff"}
        high = {"Pending", "Init:CrashLoopBackOff", "Unknown"}
        medium = {"Running", "Terminating"}
        if phase in critical:
            return Severity.CRITICAL
        if phase in high:
            return Severity.HIGH
        if phase in medium:
            return Severity.MEDIUM
        return Severity.LOW

    def _compute_confidence(self, chain: dict) -> float:
        score = 0.0
        if chain["pod"]:
            score += 0.3
        if chain["namespace"]:
            score += 0.1
        if chain["service"]:
            score += 0.2
        if chain["repository"]:
            score += 0.2
        if chain["commits"]:
            score += 0.2
        return min(score, 1.0)
