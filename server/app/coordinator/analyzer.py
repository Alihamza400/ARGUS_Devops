from __future__ import annotations

from app.coordinator.models import (
    ConflictRecord,
    ConflictSeverity,
    ProposalRecord,
    ResolutionStrategy,
)


class ConflictAnalyzer:
    WEIGHT_CONFIDENCE = 0.30
    WEIGHT_EVIDENCE = 0.25
    WEIGHT_RISK = 0.20
    WEIGHT_SEVERITY = 0.15
    WEIGHT_RECENCY = 0.10

    RISK_SCORES = {"low": 1.0, "medium": 0.6, "high": 0.2}
    SEVERITY_SCORES = {"info": 0.2, "low": 0.4, "medium": 0.6, "high": 0.8, "critical": 1.0}

    @staticmethod
    def score_proposal(proposal: ProposalRecord) -> float:
        confidence_score = proposal.confidence

        evidence_score = min(proposal.evidence_count / 10.0, 1.0)

        risk_score = ConflictAnalyzer.RISK_SCORES.get(proposal.risk_level, 0.5)

        severity_score = ConflictAnalyzer.SEVERITY_SCORES.get(proposal.severity, 0.5)

        age_hours = 0.0
        if proposal.created_at:
            age = (proposal.updated_at - proposal.created_at).total_seconds()
            age_hours = age / 3600
        recency_score = max(0.0, 1.0 - (age_hours / 48.0))

        total = (
            ConflictAnalyzer.WEIGHT_CONFIDENCE * confidence_score
            + ConflictAnalyzer.WEIGHT_EVIDENCE * evidence_score
            + ConflictAnalyzer.WEIGHT_RISK * risk_score
            + ConflictAnalyzer.WEIGHT_SEVERITY * severity_score
            + ConflictAnalyzer.WEIGHT_RECENCY * recency_score
        )

        return round(total, 4)

    @staticmethod
    def rank_proposals(proposals: list[ProposalRecord]) -> list[tuple[ProposalRecord, float]]:
        scored = [(p, ConflictAnalyzer.score_proposal(p)) for p in proposals]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    @staticmethod
    def assess_conflict_severity(conflict: ConflictRecord) -> ConflictSeverity:
        score_diff = abs(conflict.score_a - conflict.score_b)

        if conflict.conflict_type.value == "direct":
            if score_diff > 0.3:
                return ConflictSeverity.MAJOR
            return ConflictSeverity.BLOCKING

        if conflict.conflict_type.value == "indirect":
            if score_diff > 0.3:
                return ConflictSeverity.MINOR
            return ConflictSeverity.MAJOR

        if conflict.conflict_type.value == "cascading":
            if score_diff > 0.4:
                return ConflictSeverity.NONE
            return ConflictSeverity.MINOR

        return ConflictSeverity.NONE

    @staticmethod
    def recommend_resolution(
        conflict: ConflictRecord,
        ranked: list[tuple[ProposalRecord, float]],
    ) -> ResolutionStrategy:
        if conflict.conflict_type.value == "direct":
            top_score = ranked[0][1] if ranked else 0
            if top_score >= 0.7:
                return ResolutionStrategy.RANK_AND_PICK
            return ResolutionStrategy.AUTO_BLOCK

        if conflict.conflict_type.value == "indirect":
            if len(ranked) >= 2 and ranked[0][1] - ranked[1][1] > 0.2:
                return ResolutionStrategy.AUTO_APPROVE
            return ResolutionStrategy.FLAG_FOR_REVIEW

        if conflict.conflict_type.value == "cascading":
            return ResolutionStrategy.FLAG_FOR_REVIEW

        return ResolutionStrategy.MERGE_IF_COMPATIBLE

    @staticmethod
    def generate_recommendation(
        conflict: ConflictRecord,
        ranked: list[tuple[ProposalRecord, float]],
    ) -> str:
        if not ranked:
            return "No ranked proposals available for recommendation."

        top = ranked[0]
        if conflict.conflict_type.value == "direct":
            return (
                f"Recommend adopting proposal '{top[0].id}' "
                f"(score: {top[1]:.3f}) over alternatives. "
                f"Direct conflict requires choosing one proposal."
            )

        if conflict.conflict_type.value == "indirect":
            return (
                f"Proposals target the same resource with compatible actions. "
                f"Top-ranked proposal '{top[0].id}' (score: {top[1]:.3f}) "
                f"is recommended unless review determines otherwise."
            )

        if conflict.conflict_type.value == "cascading":
            return (
                f"Cascading impact detected on related resources. "
                f"Manual review required to assess compatibility."
            )

        return "No action required. Proposals are complementary."
