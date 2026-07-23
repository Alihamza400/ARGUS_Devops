from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

from app.config import settings
from app.graph.connection import Neo4jConnection
from app.graph.schema import NodeType, EdgeType


class GitHubWebhookHandler:
    def __init__(self):
        self.secret = settings.github_webhook_secret

    def verify_signature(self, payload_body: bytes, signature_header: str | None) -> bool:
        if not self.secret:
            return True
        if not signature_header:
            return False
        sha_name, signature = signature_header.split("=", 1) if "=" in signature_header else ("", "")
        if sha_name != "sha256":
            return False
        expected = hmac.new(self.secret.encode(), payload_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def handle_push(self, payload: dict) -> dict:
        repo_name = payload.get("repository", {}).get("full_name", "unknown")
        repo_url = payload.get("repository", {}).get("clone_url", "")
        default_branch = payload.get("repository", {}).get("default_branch", "main")
        repo_id = f"github-{repo_name.replace('/', '-')}"

        await Neo4jConnection.run_query(
            """
            MERGE (n:Repository {id: $id})
            SET n.name = $name, n.url = $url, n.default_branch = $branch, n.provider = 'github'
            """,
            {"id": repo_id, "name": repo_name, "url": repo_url, "branch": default_branch},
        )

        commits_created = 0
        ref = payload.get("ref", "")
        branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else "main"

        for commit in payload.get("commits", []):
            sha = commit.get("id", "")
            if not sha:
                continue
            commit_id = f"commit-{sha[:12]}"
            existing = await Neo4jConnection.run_query(
                "MATCH (n:Commit {id: $id}) RETURN n", {"id": commit_id},
            )
            if existing:
                continue

            await Neo4jConnection.run_query(
                """
                CREATE (n:Commit {
                    id: $id, sha: $sha, message: $message,
                    author: $author, email: $email,
                    timestamp: $timestamp, branch: $branch
                })
                RETURN n
                """,
                {
                    "id": commit_id,
                    "sha": sha,
                    "message": (commit.get("message") or "")[:500],
                    "author": commit.get("author", {}).get("name", "unknown"),
                    "email": commit.get("author", {}).get("email", ""),
                    "timestamp": commit.get("timestamp", datetime.utcnow().isoformat()),
                    "branch": branch,
                },
            )

            await Neo4jConnection.run_query(
                """
                MATCH (source:Commit {id: $commit_id})
                MATCH (target:Repository {id: $repo_id})
                MERGE (source)-[:IS_IN]->(target)
                """,
                {"commit_id": commit_id, "repo_id": repo_id},
            )
            commits_created += 1

        return {
            "event": "push",
            "repo_id": repo_id,
            "commits_created": commits_created,
            "branch": branch,
        }

    async def handle_pull_request(self, payload: dict) -> dict:
        action = payload.get("action", "")
        pr = payload.get("pull_request", {})
        pr_number = pr.get("number", 0)
        repo_name = payload.get("repository", {}).get("full_name", "unknown")
        repo_id = f"github-{repo_name.replace('/', '-')}"
        pr_id = f"pr-{repo_name.replace('/', '-')}-{pr_number}"

        await Neo4jConnection.run_query(
            """
            MERGE (n:PullRequest {id: $id})
            SET n.number = $number, n.title = $title,
                n.state = $state, n.author = $author
            """,
            {
                "id": pr_id,
                "number": pr_number,
                "title": (pr.get("title") or "")[:200],
                "state": pr.get("state", "open"),
                "author": pr.get("user", {}).get("login", "unknown"),
            },
        )

        await Neo4jConnection.run_query(
            """
            MATCH (source:PullRequest {id: $pr_id})
            MATCH (target:Repository {id: $repo_id})
            MERGE (source)-[:MERGED_INTO {merged_at: $merged_at}]->(target)
            """,
            {
                "pr_id": pr_id,
                "repo_id": repo_id,
                "merged_at": pr.get("merged_at") or datetime.utcnow().isoformat(),
            },
        )

        return {
            "event": "pull_request",
            "action": action,
            "pr_id": pr_id,
            "repo_id": repo_id,
        }

    async def handle_release(self, payload: dict) -> dict:
        release = payload.get("release", {})
        repo_name = payload.get("repository", {}).get("full_name", "unknown")
        tag = release.get("tag_name", "")
        release_id = f"release-{repo_name.replace('/', '-')}-{tag}"

        await Neo4jConnection.run_query(
            """
            MERGE (n:Release {id: $id})
            SET n.tag = $tag, n.name = $name,
                n.author = $author, n.published_at = $published_at
            """,
            {
                "id": release_id,
                "tag": tag,
                "name": (release.get("name") or tag)[:200],
                "author": release.get("author", {}).get("login", "unknown"),
                "published_at": release.get("published_at") or datetime.utcnow().isoformat(),
            },
        )

        return {
            "event": "release",
            "release_id": release_id,
            "tag": tag,
        }

    async def handle(self, event_type: str | None, payload: dict) -> dict:
        if event_type == "push":
            return await self.handle_push(payload)
        elif event_type == "pull_request":
            return await self.handle_pull_request(payload)
        elif event_type == "release":
            return await self.handle_release(payload)
        else:
            return {"event": event_type or "unknown", "handled": False}
