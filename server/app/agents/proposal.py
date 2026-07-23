from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agents.base import BaseAgent
from app.agents.models import (
    AnalysisResult,
    AgentQuery,
    EvidenceItem,
    Proposal,
    ProposalAction,
    RiskLevel,
)


class ProposalAgent(BaseAgent):
    agent_type = "proposal"
    agent_version = "1.0.0"
    description = "Generates evidence-backed GitOps change proposals from graph analysis"

    async def analyze(self, query: AgentQuery) -> AnalysisResult:
        return AnalysisResult(
            summary="ProposalAgent delegates analysis to IncidentAgent",
            severity="info",
            confidence=1.0,
            agent=self.agent_type,
            agent_version=self.agent_version,
        )

    async def propose(self, analysis: AnalysisResult) -> Proposal | None:
        if not analysis.evidence:
            return None

        commit_evidence = [
            e for e in analysis.evidence
            if e.category.value == "commit_change"
        ]
        pod_evidence = [
            e for e in analysis.evidence
            if e.category.value == "pod_state"
        ]
        service_evidence = [
            e for e in analysis.evidence
            if e.category.value == "service_config"
        ]
        repo_evidence = [
            e for e in analysis.evidence
            if e.category.value == "deployment_history"
        ]

        if not commit_evidence:
            return self._no_rollback_proposal(analysis)

        return self._generate_rollback_proposal(
            analysis, commit_evidence, pod_evidence, service_evidence, repo_evidence
        )

    def _generate_rollback_proposal(
        self,
        analysis: AnalysisResult,
        commits: list[EvidenceItem],
        pods: list[EvidenceItem],
        services: list[EvidenceItem],
        repos: list[EvidenceItem],
    ) -> Proposal:
        latest = commits[0]
        target_repo = repos[0].label.replace("Repository: ", "") if repos else "unknown"
        target_svc = services[0].label.replace("Service: ", "") if services else "unknown"

        risk = self._assess_risk(analysis.severity.value, len(commits))
        pr_body = self._build_pr_body(
            analysis, latest, target_repo, target_svc, commits
        )

        return Proposal(
            title=f"[Argus] Rollback {target_svc} — {latest.detail[:80]}",
            description=(
                f"Automated rollback proposal for service **{target_svc}** "
                f"deployed from **{target_repo}**.\n\n"
                f"**Trigger**: Pod '{self._pod_name(pods)}' is in "
                f"'{self._pod_status(pods)}' state."
            ),
            action=ProposalAction.ROLLBACK,
            target_id=latest.source_id,
            target_type="Commit",
            rationale=(
                f"The latest commit {latest.source_id[:12]} ({latest.detail}) "
                f"is the most recent change. Rolling back may restore stability."
            ),
            evidence=[latest] + pods[:2],
            pr_body=pr_body,
            risk_level=risk,
            created_at=datetime.now(timezone.utc),
        )

    def _no_rollback_proposal(self, analysis: AnalysisResult) -> Proposal:
        return Proposal(
            title="[Argus] Manual intervention required",
            description=(
                "No recent commits found in the provenance chain. "
                "Automatic rollback is not possible."
            ),
            action=ProposalAction.MANUAL_INTERVENTION,
            target_id="",
            target_type="none",
            rationale="No deploy-related commits found in the graph.",
            evidence=analysis.evidence[:3],
            pr_body=self._manual_pr_body(analysis),
            risk_level=RiskLevel.HIGH,
            created_at=datetime.now(timezone.utc),
        )

    def _build_pr_body(
        self,
        analysis: AnalysisResult,
        latest: EvidenceItem,
        repo: str,
        svc: str,
        commits: list[EvidenceItem],
    ) -> str:
        lines = [
            f"## Automated Rollback Proposal — {svc}",
            "",
            f"**Generated**: {datetime.now(timezone.utc).isoformat()}",
            f"**Severity**: {analysis.severity.value}",
            f"**Confidence**: {analysis.confidence:.0%}",
            "",
            "### Evidence",
            "",
        ]
        for e in analysis.evidence:
            lines.append(f"- **{e.label}**: {e.detail}")
        lines.extend(
            [
                "",
                "### Proposed Action",
                "",
                f"Rollback commit `{latest.source_id[:12]}`:",
                f"> {latest.detail}",
                "",
                "### Risk Assessment",
                "",
                f"**Risk Level**: {self._assess_risk(analysis.severity.value, len(commits)).value}",
                "",
                "### Recent Commits (newest first)",
                "",
            ]
        )
        for i, c in enumerate(commits[:10]):
            lines.append(f"{i+1}. `{c.source_id[:12]}` — {c.detail}")
        lines.extend(
            [
                "",
                "---",
                "",
                "_Generated by Argus Agent v1.0.0_",
            ]
        )
        return "\n".join(lines)

    def _manual_pr_body(self, analysis: AnalysisResult) -> str:
        lines = [
            "## Manual Intervention Required",
            "",
            f"**Generated**: {datetime.now(timezone.utc).isoformat()}",
            f"**Severity**: {analysis.severity.value}",
            "",
            "### Observed State",
            "",
        ]
        for e in analysis.evidence[:5]:
            lines.append(f"- **{e.label}**: {e.detail}")
        lines.extend(
            [
                "",
                "### Suggested Actions",
                "",
                *[f"- {s}" for s in analysis.suggestions],
                "",
                "---",
                "_Generated by Argus Agent v1.0.0_",
            ]
        )
        return "\n".join(lines)

    def _assess_risk(self, severity: str, commit_count: int) -> RiskLevel:
        if severity in ("critical",) or commit_count <= 1:
            return RiskLevel.LOW
        if severity in ("high", "medium") or commit_count <= 5:
            return RiskLevel.MEDIUM
        return RiskLevel.HIGH

    def _pod_name(self, pods: list[EvidenceItem]) -> str:
        for p in pods:
            return p.label.replace("Pod ", "")
        return "unknown"

    def _pod_status(self, pods: list[EvidenceItem]) -> str:
        for p in pods:
            return p.status or "unknown"
        return "unknown"
