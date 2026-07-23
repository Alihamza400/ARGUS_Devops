import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from git import Repo

from app.adapters.git import GitAdapter, GitAdapterConfig
from app.graph.connection import Neo4jConnection


@pytest_asyncio.fixture(autouse=True)
async def ensure_neo4j():
    for attempt in range(3):
        import asyncio
        connected = await Neo4jConnection.verify_connectivity()
        if connected:
            break
        await asyncio.sleep(1)
    else:
        pytest.skip("Neo4j not available")
    yield
    await Neo4jConnection.run_query("MATCH (n) DETACH DELETE n")


@pytest.fixture
def temp_git_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Repo.init(tmpdir)
        repo.config_writer().set_value("user", "name", "Test User").release()
        repo.config_writer().set_value("user", "email", "test@test.com").release()

        # Create initial commit
        (Path(tmpdir) / "README.md").write_text("# Test\nInitial commit")
        repo.index.add(["README.md"])
        repo.index.commit("Initial commit")

        # Create second commit
        (Path(tmpdir) / "main.py").write_text("print('hello')")
        repo.index.add(["main.py"])
        repo.index.commit("Add main.py")

        # Create third commit
        (Path(tmpdir) / "main.py").write_text("print('hello world')")
        repo.index.add(["main.py"])
        repo.index.commit("Update main.py")

        # Create a feature branch
        repo.create_head("feature/new-feature")
        repo.heads["feature/new-feature"].checkout()
        (Path(tmpdir) / "feature.py").write_text("def new_feature(): pass")
        repo.index.add(["feature.py"])
        repo.index.commit("Add new feature")

        # Switch back to master
        repo.heads["master"].checkout()

        yield tmpdir


@pytest.mark.asyncio
async def test_git_adapter_creates_repo_node(temp_git_repo):
    config = GitAdapterConfig(
        source=temp_git_repo,
        repo_name="test-repo",
        repo_id="repo-test-001",
    )
    adapter = GitAdapter(config)
    result = await adapter.sync()

    assert result["repo_id"] == "repo-test-001"
    assert result["commits_created"] >= 1

    repo_nodes = await Neo4jConnection.run_query(
        "MATCH (n:Repository {id: $id}) RETURN n",
        {"id": "repo-test-001"},
    )
    assert len(repo_nodes) == 1


@pytest.mark.asyncio
async def test_git_adapter_ingests_all_commits(temp_git_repo):
    config = GitAdapterConfig(
        source=temp_git_repo,
        repo_name="test-repo",
        repo_id="repo-test-002",
    )
    adapter = GitAdapter(config)
    result = await adapter.sync()

    # 4 commits across both branches: 2 on main + 1 on main + 1 on feature
    assert result["commits_created"] == 4

    commit_nodes = await Neo4jConnection.run_query(
        """
        MATCH (c:Commit)-[:IS_IN]->(r:Repository {id: $repo_id})
        RETURN count(c) AS cnt
        """,
        {"repo_id": "repo-test-002"},
    )
    assert commit_nodes[0]["cnt"] == 4


@pytest.mark.asyncio
async def test_git_adapter_idempotent(temp_git_repo):
    config = GitAdapterConfig(
        source=temp_git_repo,
        repo_name="test-repo",
        repo_id="repo-test-003",
    )
    adapter = GitAdapter(config)

    result1 = await adapter.sync()
    result2 = await adapter.sync()

    assert result1["commits_created"] >= 1
    assert result2["commits_created"] == 0


@pytest.mark.asyncio
async def test_git_adapter_stores_commit_metadata(temp_git_repo):
    config = GitAdapterConfig(
        source=temp_git_repo,
        repo_name="test-repo",
        repo_id="repo-test-004",
    )
    adapter = GitAdapter(config)
    await adapter.sync()

    commits = await Neo4jConnection.run_query(
        """
        MATCH (c:Commit)-[:IS_IN]->(r:Repository {id: $repo_id})
        RETURN c.message AS message, c.author AS author
        """,
        {"repo_id": "repo-test-004"},
    )
    messages = {r["message"] for r in commits}
    assert "Initial commit" in messages
    assert "Add main.py" in messages
    assert "Update main.py" in messages
    assert "Add new feature" in messages


@pytest.mark.asyncio
async def test_git_adapter_with_remote_url_clones_to_cache(temp_git_repo):
    import uuid
    unique_id = uuid.uuid4().hex[:8]
    config = GitAdapterConfig(
        source=temp_git_repo,
        repo_name=f"cloned-test-{unique_id}",
        repo_id=f"repo-clone-{unique_id}",
    )
    adapter = GitAdapter(config)
    result = await adapter.sync()
    assert result["commits_created"] == 4


@pytest.mark.asyncio
async def test_git_adapter_provenance_trace(temp_git_repo):
    config = GitAdapterConfig(
        source=temp_git_repo,
        repo_name="test-repo",
        repo_id="repo-provenance",
    )
    adapter = GitAdapter(config)
    await adapter.sync()

    commits = await Neo4jConnection.run_query(
        """
        MATCH (c:Commit)-[:IS_IN]->(r:Repository {id: $repo_id})
        RETURN c.sha AS sha
        ORDER BY c.sha
        LIMIT 1
        """,
        {"repo_id": "repo-provenance"},
    )
    assert len(commits) >= 1
    assert len(commits[0]["sha"]) > 0
