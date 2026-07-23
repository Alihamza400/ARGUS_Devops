from __future__ import annotations

from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.models import Role
from app.config import settings

security = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"
JWT_SECRET = settings.neo4j_password  # deterministic secret per deployment


def _create_token(username: str, role: str) -> str:
    import time as t

    payload = {
        "sub": username,
        "role": role,
        "iat": t.time(),
        "exp": t.time() + 86400,  # 24 hours
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def _decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    token = credentials.credentials

    # Check if it's an API key (64-char hex)
    if len(token) == 64 and all(c in "0123456789abcdef" for c in token.lower()):
        from app.auth.store import AuthStore

        user = await AuthStore.resolve_api_key(token)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        return user

    # JWT token
    payload = _decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    from app.auth.store import AuthStore

    user = await AuthStore.get_user_by_username(payload.get("sub", ""))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


def require_role(*roles: Role):
    async def checker(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        user_role = current_user.get("role", "")
        if user_role not in [r.value for r in roles]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {[r.value for r in roles]}",
            )
        return current_user

    return checker


require_admin = require_role(Role.ADMIN)
require_engineer = require_role(Role.ENGINEER, Role.ADMIN)
require_viewer = require_role(Role.VIEWER, Role.ENGINEER, Role.ADMIN)

__all__ = ["_create_token", "get_current_user", "require_role", "require_admin", "require_engineer", "require_viewer"]
