#!/usr/bin/env python3
"""Argus Approval Gate CLI — review proposals and manage approvals."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def parse_args():
    parser = argparse.ArgumentParser(
        description="Argus Approval Gate — review proposals and manage approvals"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List pending proposals")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--format", choices=["text", "json"], default="text")

    view_parser = sub.add_parser("view", help="View a proposal with evidence")
    view_parser.add_argument("proposal_id", help="Proposal ID to view")
    view_parser.add_argument("--format", choices=["text", "json"], default="text")

    approve = sub.add_parser("approve", help="Approve a proposal")
    approve.add_argument("proposal_id", help="Proposal ID")
    approve.add_argument("--reviewer", required=True, help="Reviewer identifier")
    approve.add_argument("--role", default="peer", choices=["senior", "team_lead", "peer", "automated"])
    approve.add_argument("--comment", default="", help="Review comment")
    approve.add_argument("--format", choices=["text", "json"], default="text")

    reject = sub.add_parser("reject", help="Reject a proposal")
    reject.add_argument("proposal_id", help="Proposal ID")
    reject.add_argument("--reviewer", required=True, help="Reviewer identifier")
    reject.add_argument("--role", default="peer", choices=["senior", "team_lead", "peer", "automated"])
    reject.add_argument("--comment", default="", help="Reason for rejection")
    reject.add_argument("--format", choices=["text", "json"], default="text")

    status = sub.add_parser("status", help="Check review status of a proposal")
    status.add_argument("proposal_id", help="Proposal ID")
    status.add_argument("--format", choices=["text", "json"], default="text")

    policy = sub.add_parser("policy", help="View or update approval policy")
    policy.add_argument("--show", action="store_true", help="Show current policy")
    policy.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="Set a policy rule")
    policy.add_argument("--format", choices=["text", "json"], default="text")

    return parser.parse_args()


def _fmt(d: dict) -> str:
    return json.dumps(d, indent=2, default=str)


async def cmd_list(args):
    from app.gate.engine import ReviewEngine
    from app.gate.renderer import EvidenceRenderer

    items = await ReviewEngine.list_pending_reviews(limit=args.limit)
    if args.format == "json":
        print(_fmt({"count": len(items), "items": items}))
    else:
        print()
        print(EvidenceRenderer.render_pending_list(items))
        print()


async def cmd_view(args):
    from app.coordinator.store import ProposalStore
    from app.gate.renderer import EvidenceRenderer

    proposal = await ProposalStore.get_proposal(args.proposal_id)
    if not proposal:
        print(f"Proposal '{args.proposal_id}' not found.")
        sys.exit(1)

    if args.format == "json":
        print(_fmt(proposal.model_dump()))
    else:
        print()
        print(EvidenceRenderer.render_proposal_for_review(proposal.model_dump()))
        print()


async def cmd_approve(args):
    from app.gate.engine import ReviewEngine
    from app.gate.models import ReviewDecision, ReviewSubmission, ReviewerRole
    from app.gate.renderer import EvidenceRenderer

    submission = ReviewSubmission(
        proposal_id=args.proposal_id,
        reviewer=args.reviewer,
        reviewer_role=ReviewerRole(args.role),
        decision=ReviewDecision.APPROVED,
        comment=args.comment,
        evidence_checked=True,
    )
    response = await ReviewEngine.submit_review(submission)
    if args.format == "json":
        print(_fmt(response.model_dump()))
    else:
        print()
        print(EvidenceRenderer.render_review_result(response.model_dump()))
        print()


async def cmd_reject(args):
    from app.gate.engine import ReviewEngine
    from app.gate.models import ReviewDecision, ReviewSubmission, ReviewerRole
    from app.gate.renderer import EvidenceRenderer

    submission = ReviewSubmission(
        proposal_id=args.proposal_id,
        reviewer=args.reviewer,
        reviewer_role=ReviewerRole(args.role),
        decision=ReviewDecision.REJECTED,
        comment=args.comment,
        evidence_checked=True,
    )
    response = await ReviewEngine.submit_review(submission)
    if args.format == "json":
        print(_fmt(response.model_dump()))
    else:
        print()
        print(EvidenceRenderer.render_review_result(response.model_dump()))
        print()


async def cmd_status(args):
    from app.gate.engine import ReviewEngine
    from app.gate.renderer import EvidenceRenderer

    status = await ReviewEngine.get_review_status(args.proposal_id)
    if args.format == "json":
        print(_fmt(status))
    else:
        print()
        print(EvidenceRenderer.render_review_status(status))
        print()


async def cmd_policy(args):
    from app.gate.store import ReviewStore
    from app.gate.renderer import EvidenceRenderer

    if args.set:
        config = await ReviewStore.get_policy_config()
        key, value = args.set
        try:
            value = int(value)
        except ValueError:
            try:
                value = float(value)
            except ValueError:
                if value.lower() in ("true", "false"):
                    value = value.lower() == "true"
        config.rules[key] = value
        await ReviewStore.save_policy_config(config)
        print(f"Policy updated: {key} = {value}")

    config = await ReviewStore.get_policy_config()
    if args.format == "json":
        print(_fmt(config.model_dump()))
    else:
        print()
        print(EvidenceRenderer.render_policy_config(config.model_dump()))
        print()


async def main():
    args = parse_args()
    if args.command == "list":
        await cmd_list(args)
    elif args.command == "view":
        await cmd_view(args)
    elif args.command == "approve":
        await cmd_approve(args)
    elif args.command == "reject":
        await cmd_reject(args)
    elif args.command == "status":
        await cmd_status(args)
    elif args.command == "policy":
        await cmd_policy(args)


if __name__ == "__main__":
    asyncio.run(main())
