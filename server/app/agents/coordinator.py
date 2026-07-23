from __future__ import annotations

import time
from typing import Any

from app.agents.base import BaseAgent
from app.agents.incident import IncidentAgent
from app.agents.models import AgentQuery, AgentResponse, AnalysisResult, Proposal
from app.agents.proposal import ProposalAgent


class AgentCoordinator:
    _agents: dict[str, BaseAgent] = {}

    @classmethod
    def register(cls, agent: BaseAgent) -> None:
        cls._agents[agent.agent_type] = agent

    @classmethod
    def get_agent(cls, agent_type: str) -> BaseAgent | None:
        return cls._agents.get(agent_type)

    @classmethod
    def list_agents(cls) -> list[dict[str, Any]]:
        return [
            {
                "type": a.agent_type,
                "version": a.agent_version,
                "description": a.description,
            }
            for a in cls._agents.values()
        ]

    @classmethod
    async def analyze(cls, query: AgentQuery) -> AgentResponse:
        start = time.perf_counter()
        errors: list[str] = []
        analysis: AnalysisResult | None = None
        proposal: Proposal | None = None

        incident = cls.get_agent("incident")
        if not incident:
            return AgentResponse(
                analysis=AnalysisResult(
                    summary="IncidentAgent not registered",
                    severity="critical",
                    confidence=0.0,
                    query_time_ms=(time.perf_counter() - start) * 1000,
                    agent="coordinator",
                    agent_version="1.0.0",
                ),
                errors=["IncidentAgent is not available. Register agents first."],
            )

        try:
            analysis = await incident.analyze(query)
        except Exception as e:
            errors.append(f"IncidentAgent error: {e}")

        if analysis and query.generate_proposal:
            proposal_agent = cls.get_agent("proposal")
            if proposal_agent:
                try:
                    proposal = await proposal_agent.propose(analysis)
                except Exception as e:
                    errors.append(f"ProposalAgent error: {e}")
            else:
                errors.append("ProposalAgent not registered")

        if not analysis:
            analysis = AnalysisResult(
                summary="Analysis failed",
                severity="info",
                confidence=0.0,
                errors=errors,
                query_time_ms=(time.perf_counter() - start) * 1000,
                agent="coordinator",
                agent_version="1.0.0",
            )

        return AgentResponse(
            analysis=analysis,
            proposal=proposal,
            errors=errors,
        )


AgentCoordinator.register(IncidentAgent())
AgentCoordinator.register(ProposalAgent())
