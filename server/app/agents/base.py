from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from app.agents.models import AnalysisResult, AgentQuery, Proposal


class BaseAgent(ABC):
    agent_type: str = ""
    agent_version: str = "0.1.0"
    description: str = ""

    @abstractmethod
    async def analyze(self, query: AgentQuery) -> AnalysisResult:
        ...

    async def propose(self, analysis: AnalysisResult) -> Proposal | None:
        return None

    async def health(self) -> dict[str, Any]:
        return {
            "agent": self.agent_type,
            "version": self.agent_version,
            "description": self.description,
            "status": "available",
        }

    def _timed(self, coro):
        """Run a coroutine and return (result, elapsed_ms)."""
        import asyncio
        loop = asyncio.get_event_loop()
        start = time.perf_counter()
        result = loop.run_until_complete(coro) if loop.is_running() else None
        elapsed = (time.perf_counter() - start) * 1000
        return result, elapsed
