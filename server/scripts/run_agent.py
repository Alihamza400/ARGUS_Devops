#!/usr/bin/env python3
"""Run the Argus reference agent from the command line."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime


def parse_args():
    parser = argparse.ArgumentParser(
        description="Argus Reference Agent — analyze incidents and generate GitOps proposals"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="Analyze a pod or service")
    analyze.add_argument("--pod-id", help="Graph node ID of the pod")
    analyze.add_argument("--pod-name", help="Pod name (fuzzy search)")
    analyze.add_argument("--namespace", help="Filter by namespace")
    analyze.add_argument(
        "--proposal", action="store_true", help="Generate a GitOps proposal"
    )
    analyze.add_argument("--max-commits", type=int, default=20)
    analyze.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format"
    )

    unhealthy = sub.add_parser(
        "unhealthy", help="List all unhealthy pods in the graph"
    )
    unhealthy.add_argument("--limit", type=int, default=20)
    unhealthy.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format"
    )

    agents = sub.add_parser("list", help="List available agents")

    return parser.parse_args()


def print_analysis_text(result: dict):
    analysis = result.get("analysis", {})
    proposal = result.get("proposal")
    errors = result.get("errors", [])

    sev = analysis.get("severity", "unknown").upper()
    summary = analysis.get("summary", "")
    confidence = analysis.get("confidence", 0)
    elapsed = analysis.get("query_time_ms", 0)

    print(f"\n{'='*60}")
    print(f"  ARGUS ANALYSIS  [{sev}]  ({confidence:.0%} confidence)")
    print(f"{'='*60}")
    print(f"  {summary}")
    print(f"  Query: {elapsed:.0f}ms")
    print()

    evidence = analysis.get("evidence", [])
    if evidence:
        print(f"  Evidence ({len(evidence)} items):")
        for e in evidence:
            print(f"    [{e['category']}] {e['label']}")
            print(f"      {e['detail']}")

    suggestions = analysis.get("suggestions", [])
    if suggestions:
        print(f"\n  Suggestions:")
        for s in suggestions:
            print(f"    → {s}")

    timeline = analysis.get("timeline", [])
    if timeline:
        print(f"\n  Timeline ({len(timeline)} events):")
        for t in timeline:
            print(f"    [{t['event_type']}] {t['summary']}")
            print(f"      {t['detail']}")

    if proposal:
        print(f"\n{'─'*60}")
        print(f"  PROPOSAL: {proposal.get('title', '')}")
        print(f"{'─'*60}")
        print(f"  Action: {proposal.get('action', '')}")
        print(f"  Risk:   {proposal.get('risk_level', '')}")
        print(f"\n  PR Body:")
        for line in proposal.get("pr_body", "").split("\n"):
            print(f"    {line}")

    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for e in errors:
            print(f"    ✗ {e}")

    print()


async def cmd_analyze(args):
    from app.agents.coordinator import AgentCoordinator
    from app.agents.models import AgentQuery

    query = AgentQuery(
        query=f"CLI: analyze pod {args.pod_id or args.pod_name}",
        pod_id=args.pod_id,
        pod_name=args.pod_name,
        namespace=args.namespace,
        generate_proposal=args.proposal,
        max_commits=args.max_commits,
    )
    response = await AgentCoordinator.analyze(query)
    result = response.model_dump(mode="json")

    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        print_analysis_text(result)


async def cmd_unhealthy(args):
    from app.agents.queries import GraphQueries

    pods = await GraphQueries.get_unhealthy_pods(limit=args.limit)
    if args.format == "json":
        print(
            json.dumps(
                {
                    "count": len(pods),
                    "pods": [
                        {
                            "id": p.id,
                            "name": p.name,
                            "phase": p.phase,
                            "namespace": p.namespace,
                            "node": p.node,
                        }
                        for p in pods
                    ],
                },
                indent=2,
                default=str,
            )
        )
    else:
        print(f"\nUnhealthy Pods ({len(pods)} found):")
        print(f"{'─'*60}")
        for p in pods:
            print(f"  {p.name:<45} {p.phase:<20} {p.namespace}")
        print()


async def cmd_list():
    from app.agents.coordinator import AgentCoordinator

    agents = AgentCoordinator.list_agents()
    print(f"\nAvailable Agents ({len(agents)}):")
    print(f"{'─'*60}")
    for a in agents:
        print(f"  {a['type']:<20} v{a['version']:<8} {a['description']}")
    print()


async def main():
    args = parse_args()
    if args.command == "analyze":
        await cmd_analyze(args)
    elif args.command == "unhealthy":
        await cmd_unhealthy(args)
    elif args.command == "list":
        await cmd_list()


if __name__ == "__main__":
    asyncio.run(main())
