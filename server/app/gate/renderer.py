from __future__ import annotations
from typing import Any

class EvidenceRenderer:
    @staticmethod
    def render_proposal_for_review(proposal: dict[str, Any]) -> str:
        lines = ["="*72, f"  PROPOSAL REVIEW  [{proposal.get('status','unknown').upper()}]", "="*72,
            f"  ID:        {proposal.get('id','')}", f"  Title:     {proposal.get('title','')}",
            f"  Agent:     {proposal.get('agent','')} v{proposal.get('agent_version','')}",
            f"  Action:    {proposal.get('action','')}", f"  Target:    {proposal.get('target_type','')} {proposal.get('target_id','')}",
            f"  Severity:  {proposal.get('severity','')}", f"  Risk:      {proposal.get('risk_level','')}",
            f"  Confidence: {proposal.get('confidence',0):.0%}", "", f"  Description:", f"    {proposal.get('description','')}", "",
            f"  Rationale:", f"    {proposal.get('rationale','')}", "", f"  Evidence: {proposal.get('evidence_count',0)} items",
            f"    {proposal.get('evidence_summary','')}", ""]
        if proposal.get("pr_body"):
            preview = proposal["pr_body"][:200].replace("\n","\n    ")
            lines += ["  PR Body Preview:", f"    {preview}{'...' if len(proposal['pr_body'])>200 else ''}", ""]
        lines += [f"  Created:   {proposal.get('created_at','')}", "-"*72]
        return "\n".join(lines)

    @staticmethod
    def render_review_status(status: dict[str, Any]) -> str:
        lines = [f"  Proposal:   {status.get('proposal_id','')}", f"  Status:     {status.get('proposal_status','')}",
            f"  Reviews:    {status.get('total_reviews',0)}", f"  Approvals:  {status.get('approvals',0)} / {status.get('min_required',1)}",
            f"  Rejections: {status.get('rejections',0)}", f"  Approved:   {'YES' if status.get('approval_met') else 'NO'}"]
        for r in status.get("reviews",[]):
            lines.append(f"    [{r.get('decision','').upper()}] {r.get('reviewer','')} ({r.get('reviewer_role','')})")
            if r.get("comment"): lines.append(f"      > {r['comment']}")
        return "\n".join(lines)

    @staticmethod
    def render_pending_list(items: list[dict[str, Any]]) -> str:
        if not items: return "  No pending proposals."
        lines = ["  Pending Proposals:", "", f"  {'ID':<30} {'Status':<18} {'Approvals':<10}", "  "+"-"*60]
        for i in items:
            lines.append(f"  {i.get('proposal_id',''):<30} {i.get('proposal_status',''):<18} {i.get('approvals',0)}/{i.get('min_required',1):<8}")
        return "\n".join(lines)

    @staticmethod
    def render_policy_config(config: dict[str, Any]) -> str:
        lines = ["  Approval Policy:", "  "+"-"*40]
        for k, v in config.get("rules",{}).items(): lines.append(f"    {k.replace('_',' ').title():<30} {v}")
        return "\n".join(lines)

    @staticmethod
    def render_review_result(response: dict[str, Any]) -> str:
        lines = ["="*60, "  REVIEW RESULT", "="*60, f"  Message:  {response.get('message','')}", f"  Status:   {response.get('proposal_status','')}"]
        for v in response.get("policy_violations",[]): lines.append(f"    ! {v}")
        return "\n".join(lines)
