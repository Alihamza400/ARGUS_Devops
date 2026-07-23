from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.adapters.webhooks.github import GitHubWebhookHandler

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

github_handler = GitHubWebhookHandler()


@router.post("/github")
async def github_webhook(request: Request):
    payload_body = await request.body()
    event_type = request.headers.get("X-GitHub-Event")
    signature = request.headers.get("X-Hub-Signature-256")

    if not github_handler.verify_signature(payload_body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not event_type:
        return {"handled": False, "reason": "No X-GitHub-Event header"}

    result = await github_handler.handle(event_type, payload)
    return {"handled": True, "result": result}


@router.get("/github/verify")
async def verify_webhook():
    secret_configured = len(github_handler.secret) > 0
    return {
        "secret_configured": secret_configured,
        "message": "Webhook endpoint is active"
        if secret_configured
        else "Webhook secret not configured - signatures not verified",
    }
