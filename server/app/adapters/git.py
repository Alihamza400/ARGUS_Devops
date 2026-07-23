from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from git import Repo, GitCommandError
from git.objects import Commit as GitCommit

from app.graph.connection import Neo4jConnection


@dataclass
class GitAdapterConfig:
    source: str
    repo_name: str
    repo_id: str = ""
    branch: str = "main"
    max_commits: int = 500
    cache_dir: str = "/tmp/argus-git-cache"
    sync_tags: bool = True
    sync_branches: bool = True


class GitAdapter:
    def __init__(self, config: GitAdapterConfig):
        self.config = config
        if not config.repo_id:
            config.repo_id = f"repo-{uuid.uuid4().hex[:8]}"
        self._repo: Repo | None = None

    async def sync(self) -> dict:
        repo = self._ensure_repo()
        repo_node = await self._sync_repo_node(repo)
        result = {"repo_id": repo_node["id"], "commits_created": 0, "errors": []}
        for branch_name in self._get_branches(repo):
            try:
                commits = await self._sync_branch(repo, branch_name)
                result["commits_created"] += commits
            except Exception as e:
                result["errors"].append(f"branch '{branch_name}': {e}")
        return result

    def _ensure_repo(self) -> Repo:
        if self._repo:
            return self._repo

        path = Path(self.config.source)
        if path.is_dir():
            self._repo = Repo(path)
        else:
            cache_path = Path(self.config.cache_dir) / self.config.repo_name
            if cache_path.is_dir():
                self._repo = Repo(cache_path)
                origin = self._repo.remotes.origin
                origin.fetch()
            else:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                self._repo = Repo.clone_from(self.config.source, cache_path)
        return self._repo

    def _get_branches(self, repo: Repo) -> list[str]:
        branches = [h.name for h in repo.heads]
        if not branches:
            branches = ["HEAD"]
        return branches

    async def _sync_repo_node(self, repo: Repo) -> dict:
        remote_url = ""
        try:
            remote_url = repo.remotes.origin.url
        except (AttributeError, IndexError):
            pass

        existing = await Neo4jConnection.run_query(
            "MATCH (n:Repository {id: $id}) RETURN n",
            {"id": self.config.repo_id},
        )
        if existing:
            return dict(existing[0]["n"])

        await Neo4jConnection.run_query(
            """
            CREATE (n:Repository {
                id: $id, name: $name, url: $url,
                default_branch: $branch, provider: $provider
            })
            RETURN n
            """,
            {
                "id": self.config.repo_id,
                "name": self.config.repo_name,
                "url": remote_url or self.config.source,
                "branch": self.config.branch,
                "provider": "git",
            },
        )
        result = await Neo4jConnection.run_query(
            "MATCH (n {id: $id}) RETURN n",
            {"id": self.config.repo_id},
        )
        return dict(result[0]["n"])

    async def _sync_branch(self, repo: Repo, branch_ref: str) -> int:
        commits_created = 0
        try:
            head = repo.commit(branch_ref)
        except (ValueError, GitCommandError):
            return 0

        branch_name = branch_ref.replace("refs/heads/", "")
        walked = 0
        for commit in repo.iter_commits(
            branch_ref, max_count=self.config.max_commits
        ):
            walked += 1
            created = await self._sync_commit(commit, repo)
            if created:
                commits_created += 1
                await self._link_commit_to_repo(commit)

        return commits_created

    async def _sync_commit(self, commit: GitCommit, repo: Repo) -> bool:
        commit_id = f"commit-{commit.hexsha[:12]}"
        existing = await Neo4jConnection.run_query(
            "MATCH (n:Commit {id: $id}) RETURN n",
            {"id": commit_id},
        )
        if existing:
            return False

        author_name = commit.author.name if commit.author else "unknown"
        author_email = commit.author.email if commit.author else ""
        ts = datetime.fromtimestamp(commit.committed_date).isoformat()

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
                "sha": commit.hexsha,
                "message": commit.message.split("\n")[0][:200],
                "author": author_name,
                "email": author_email,
                "timestamp": ts,
                "branch": "",
            },
        )
        return True

    async def _link_commit_to_repo(self, commit: GitCommit) -> None:
        commit_id = f"commit-{commit.hexsha[:12]}"
        existing = await Neo4jConnection.run_query(
            """
            MATCH (source:Commit {id: $commit_id})
            MATCH (target:Repository {id: $repo_id})
            MATCH (source)-[r:IS_IN]->(target)
            RETURN r LIMIT 1
            """,
            {"commit_id": commit_id, "repo_id": self.config.repo_id},
        )
        if existing:
            return

        await Neo4jConnection.run_query(
            """
            MATCH (source:Commit {id: $commit_id})
            MATCH (target:Repository {id: $repo_id})
            CREATE (source)-[:IS_IN]->(target)
            """,
            {"commit_id": commit_id, "repo_id": self.config.repo_id},
        )

    async def close(self):
        self._repo = None
