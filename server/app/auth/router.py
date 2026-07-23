from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import _create_token, get_current_user, require_role
from app.auth.models import ApiKeyCreate, ApiKeyFullResponse, ApiKeyResponse, LoginRequest, Role, TokenResponse, UserCreate, UserResponse
from app.auth.store import AuthStore

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/schema")
async def ensure_schema():
    return {"migrations": await AuthStore.ensure_schema()}


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    if not request.username or not request.password:
        raise HTTPException(400, "username and password required")
    user = await AuthStore.authenticate(request.username, request.password)
    if not user:
        raise HTTPException(401, "Invalid credentials")
    token = _create_token(user["username"], user["role"])
    return TokenResponse(
        access_token=token,
        role=Role(user["role"]),
        username=user["username"],
    )


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "username": current_user.get("username", ""),
        "role": current_user.get("role", ""),
        "email": current_user.get("email", ""),
    }


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    request: UserCreate,
    current_user: dict = Depends(require_role(Role.ADMIN)),
):
    user = await AuthStore.create_user(
        username=request.username,
        password=request.password,
        role=request.role,
        email=request.email,
    )
    if not user:
        raise HTTPException(409, f"User '{request.username}' already exists")
    return UserResponse(
        id=user["id"],
        username=user["username"],
        role=Role(user["role"]),
        email=user["email"],
        created_at=datetime.fromisoformat(user["created_at"]),
    )


@router.get("/users")
async def list_users(current_user: dict = Depends(require_role(Role.ADMIN))):
    users = await AuthStore.list_users()
    return {
        "count": len(users),
        "users": [
            UserResponse(
                id=u["id"],
                username=u["username"],
                role=Role(u["role"]),
                email=u.get("email", ""),
                created_at=datetime.fromisoformat(u["created_at"]),
            )
            for u in users
        ],
    }


@router.put("/users/{user_id}/role")
async def update_role(
    user_id: str,
    role: Role,
    current_user: dict = Depends(require_role(Role.ADMIN)),
):
    user = await AuthStore.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    await AuthStore.update_role(user_id, role)
    return {"status": "updated", "user_id": user_id, "role": role.value}


@router.post("/api-keys", response_model=ApiKeyFullResponse, status_code=201)
async def create_api_key(
    request: ApiKeyCreate,
    current_user: dict = Depends(get_current_user),
):
    username = current_user["username"]
    result = await AuthStore.create_api_key(username, request.name)
    return ApiKeyFullResponse(
        id=result["id"],
        name=result["name"],
        key_preview=result["key_preview"],
        key=result["key"],
        created_at=datetime.fromisoformat(result["created_at"]),
    )


@router.get("/api-keys")
async def list_api_keys(current_user: dict = Depends(get_current_user)):
    username = current_user["username"]
    keys = await AuthStore.list_api_keys(username)
    return {
        "count": len(keys),
        "keys": [
            ApiKeyResponse(
                id=k["id"],
                name=k["name"],
                key_preview=k["key_preview"],
                created_at=datetime.fromisoformat(k["created_at"]) if k.get("created_at") else datetime.utcnow(),
            )
            for k in keys
        ],
    }


@router.delete("/api-keys/{key_id}")
async def delete_api_key(
    key_id: str,
    current_user: dict = Depends(get_current_user),
):
    from app.graph.connection import Neo4jConnection

    result = await Neo4jConnection.run_query(
        "MATCH (n:ApiKey {id: $id, username: $username}) RETURN n",
        {"id": key_id, "username": current_user["username"]},
    )
    if not result:
        raise HTTPException(404, "API key not found")
    await AuthStore.delete_api_key(key_id)
    return {"status": "deleted"}
