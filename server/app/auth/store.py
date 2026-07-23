from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Any

from app.graph.connection import Neo4jConnection
from app.auth.models import Role


def _hash_password(password: str) -> str:
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + key.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
        return key.hex() == key_hex
    except (ValueError, TypeError):
        return False


def _generate_api_key() -> tuple[str, str]:
    raw = os.urandom(32).hex()
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


class AuthStore:
    @staticmethod
    async def ensure_schema() -> list[dict]:
        results = []
        for constraint in [
            "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (n:User) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT user_username IF NOT EXISTS FOR (n:User) REQUIRE n.username IS UNIQUE",
            "CREATE CONSTRAINT api_key_id IF NOT EXISTS FOR (n:ApiKey) REQUIRE n.id IS UNIQUE",
        ]:
            try:
                await Neo4jConnection.run_query(constraint)
                results.append({"constraint": constraint[:60], "status": "ok"})
            except Exception as e:
                results.append({"constraint": constraint[:60], "status": "error", "error": str(e)})
        return results

    @staticmethod
    async def create_user(
        username: str,
        password: str,
        role: Role | str = Role.ENGINEER,
        email: str = "",
    ) -> dict[str, Any] | None:
        if isinstance(role, str):
            role = Role(role)
        user_id = f"user-{username}"
        existing = await Neo4jConnection.run_query(
            "MATCH (n:User {id: $id}) RETURN n", {"id": user_id},
        )
        if existing:
            return None

        password_hash = _hash_password(password)
        now = datetime.utcnow().isoformat()
        await Neo4jConnection.run_query(
            """
            CREATE (n:User {
                id: $id, username: $username, role: $role,
                email: $email, password_hash: $hash, created_at: $now
            })
            RETURN n
            """,
            {
                "id": user_id,
                "username": username,
                "role": role.value,
                "email": email,
                "hash": password_hash,
                "now": now,
            },
        )
        return {"id": user_id, "username": username, "role": role.value, "email": email, "created_at": now}

    @staticmethod
    async def get_user_by_username(username: str) -> dict[str, Any] | None:
        result = await Neo4jConnection.run_query(
            "MATCH (n:User {username: $username}) RETURN n",
            {"username": username},
        )
        if not result:
            return None
        n = result[0]["n"]
        return dict(n.items())

    @staticmethod
    async def get_user_by_id(user_id: str) -> dict[str, Any] | None:
        result = await Neo4jConnection.run_query(
            "MATCH (n:User {id: $id}) RETURN n",
            {"id": user_id},
        )
        if not result:
            return None
        n = result[0]["n"]
        return dict(n.items())

    @staticmethod
    async def list_users() -> list[dict[str, Any]]:
        result = await Neo4jConnection.run_query(
            "MATCH (n:User) RETURN n ORDER BY n.created_at",
        )
        return [dict(rec["n"].items()) for rec in result]

    @staticmethod
    async def update_role(user_id: str, role: Role) -> bool:
        await Neo4jConnection.run_query(
            "MATCH (n:User {id: $id}) SET n.role = $role",
            {"id": user_id, "role": role.value},
        )
        return True

    @staticmethod
    async def authenticate(username: str, password: str) -> dict[str, Any] | None:
        user = await AuthStore.get_user_by_username(username)
        if not user:
            return None
        stored_hash = user.get("password_hash", "")
        if not _verify_password(password, stored_hash):
            return None
        return user

    @staticmethod
    async def create_api_key(username: str, name: str) -> dict[str, Any] | None:
        raw_key, hashed = _generate_api_key()
        key_id = f"apikey-{username}-{name.lower().replace(' ', '-')}"
        now = datetime.utcnow().isoformat()
        await Neo4jConnection.run_query(
            """
            CREATE (n:ApiKey {
                id: $id, name: $name, key_hash: $hash,
                username: $username, created_at: $now
            })
            RETURN n
            """,
            {"id": key_id, "name": name, "hash": hashed, "username": username, "now": now},
        )
        return {"id": key_id, "name": name, "key_preview": raw_key[:8] + "...", "key": raw_key, "created_at": now}

    @staticmethod
    async def list_api_keys(username: str) -> list[dict[str, Any]]:
        result = await Neo4jConnection.run_query(
            "MATCH (n:ApiKey {username: $username}) RETURN n ORDER BY n.created_at",
            {"username": username},
        )
        return [
            {
                "id": rec["n"]["id"],
                "name": rec["n"]["name"],
                "key_preview": rec["n"]["key_hash"][:8] + "...",
                "created_at": rec["n"].get("created_at", ""),
            }
            for rec in result
        ]

    @staticmethod
    async def delete_api_key(key_id: str) -> bool:
        await Neo4jConnection.run_query(
            "MATCH (n:ApiKey {id: $id}) DETACH DELETE n",
            {"id": key_id},
        )
        return True

    @staticmethod
    async def resolve_api_key(raw_key: str) -> dict[str, Any] | None:
        hashed = hashlib.sha256(raw_key.encode()).hexdigest()
        result = await Neo4jConnection.run_query(
            "MATCH (n:ApiKey {key_hash: $hash}) RETURN n",
            {"hash": hashed},
        )
        if not result:
            return None
        n = result[0]["n"]
        username = n.get("username", "")
        user = await AuthStore.get_user_by_username(username)
        if not user:
            return None
        return user
