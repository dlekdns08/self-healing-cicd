"""
FastAPI webhook server — GitHub Actions workflow_run 이벤트 수신
"""
import hashlib
import hmac
import os

from fastapi import FastAPI, Header, HTTPException, Request

from agent.graph import run_healing_agent
from storage.db import save_run_event
from webhook.log_fetcher import fetch_workflow_logs
from webhook.parser import classify_error

app = FastAPI(title="Self-Healing CI/CD Webhook")

GITHUB_WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]


def _verify_signature(body: bytes, sig_header: str) -> None:
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig_header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(...),
    x_github_event: str = Header(...),
):
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    payload = await request.json()

    # workflow_run 이벤트 중 실패한 것만 처리
    if x_github_event != "workflow_run":
        return {"status": "ignored"}
    if payload.get("workflow_run", {}).get("conclusion") != "failure":
        return {"status": "ignored", "reason": "not a failure"}

    run = payload["workflow_run"]
    run_id = run["id"]
    repo = payload["repository"]["full_name"]

    logs = await fetch_workflow_logs(repo, run_id)
    error_info = classify_error(logs)
    save_run_event(run_id=run_id, repo=repo, error_info=error_info)

    # 에이전트 비동기 실행 (백그라운드)
    import asyncio
    asyncio.create_task(
        run_healing_agent(run_id=run_id, repo=repo, error_info=error_info, logs=logs)
    )

    return {"status": "accepted", "run_id": run_id, "error_type": error_info["type"]}


@app.get("/health")
async def health():
    return {"status": "ok"}
