#!/usr/bin/env python3
"""Run all Argus adapters to sync data into the knowledge graph."""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sync data sources into the Argus knowledge graph"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # git sync
    git_parser = sub.add_parser("git", help="Sync a Git repository")
    git_parser.add_argument("source", help="Local path or remote URL")
    git_parser.add_argument("--name", required=True, help="Repository name")
    git_parser.add_argument(
        "--repo-id", default="", help="Repository ID (auto-generated if empty)"
    )
    git_parser.add_argument("--branch", default="main", help="Default branch")
    git_parser.add_argument("--max-commits", type=int, default=500)

    # k8s sync
    k8s_parser = sub.add_parser("k8s", help="Sync a Kubernetes cluster")
    k8s_parser.add_argument("--cluster-name", default="default-cluster")
    k8s_parser.add_argument(
        "--cluster-id", default="", help="Cluster ID (auto-generated if empty)"
    )
    k8s_parser.add_argument("--namespace", default="", help="Filter to namespace")
    k8s_parser.add_argument(
        "--kubeconfig", default=None, help="Path to kubeconfig file"
    )

    # github-actions sync
    gh_parser = sub.add_parser("github", help="Sync GitHub Actions workflow runs")
    gh_parser.add_argument("--owner", required=True, help="Repository owner")
    gh_parser.add_argument("--repo", required=True, help="Repository name")
    gh_parser.add_argument("--token", default="", help="GitHub personal access token")
    gh_parser.add_argument("--workflow", default=None, help="Filter by workflow name")
    gh_parser.add_argument("--branch", default=None, help="Filter by branch")
    gh_parser.add_argument("--status", default=None, help="Filter by status (queued, in_progress, completed)")
    gh_parser.add_argument("--max-runs", type=int, default=50, help="Maximum runs to sync")

    # all sync
    sub.add_parser("all", help="Run every available adapter")

    return parser.parse_args()


async def sync_git(args) -> dict:
    from app.adapters.git import GitAdapter, GitAdapterConfig

    config = GitAdapterConfig(
        source=args.source,
        repo_name=args.name,
        repo_id=args.repo_id or f"repo-{uuid.uuid4().hex[:8]}",
        branch=args.branch,
        max_commits=args.max_commits,
    )
    adapter = GitAdapter(config)
    return await adapter.sync()


async def sync_k8s(args) -> dict:
    from app.adapters.kubernetes import K8sAdapter, K8sAdapterConfig

    config = K8sAdapterConfig(
        cluster_name=args.cluster_name,
        cluster_id=args.cluster_id or f"cluster-{uuid.uuid4().hex[:8]}",
        namespace=args.namespace,
        kubeconfig_path=args.kubeconfig,
    )
    adapter = K8sAdapter(config)
    return await adapter.sync()


async def sync_github(args) -> dict:
    from app.adapters.github_actions import GitHubActionsAdapter, GitHubActionsConfig

    config = GitHubActionsConfig(
        repo_owner=args.owner,
        repo_name=args.repo,
        token=args.token,
        workflow_name=args.workflow,
        branch=args.branch,
        status=args.status,
        max_runs=args.max_runs,
    )
    adapter = GitHubActionsAdapter(config)
    return await adapter.sync()


async def sync_all() -> list[dict]:
    results = []

    # Default K8s sync (in-cluster or ~/.kube/config)
    try:
        from app.adapters.kubernetes import K8sAdapter, K8sAdapterConfig

        k8s = K8sAdapter(K8sAdapterConfig())
        result = await k8s.sync()
        results.append({"adapter": "kubernetes", **result})
    except Exception as e:
        results.append({"adapter": "kubernetes", "error": str(e)})

    return results


async def main():
    args = parse_args()
    if args.command == "git":
        result = await sync_git(args)
    elif args.command == "k8s":
        result = await sync_k8s(args)
    elif args.command == "github":
        result = await sync_github(args)
    elif args.command == "all":
        result = await sync_all()
    else:
        print(f"Unknown command: {args.command}")
        sys.exit(1)

    import json
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
