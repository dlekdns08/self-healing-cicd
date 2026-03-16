"""
FastAPI webhook server — GitHub Actions workflow_run 이벤트 수신 + 범용 CI webhook
"""
import hashlib
import hmac
import os
import time

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from agent.graph import run_healing_agent
from storage.db import save_run_event
from webhook.log_fetcher import fetch_workflow_logs
from webhook.parser import classify_error

app = FastAPI(title="Self-Healing CI/CD Webhook")

GITHUB_WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]
CI_WEBHOOK_TOKEN = os.environ.get("CI_WEBHOOK_TOKEN", "")


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
    import logging

    async def _run_with_log():
        try:
            await run_healing_agent(run_id=run_id, repo=repo, error_info=error_info, logs=logs)
        except Exception:
            logging.exception("[self-healing] agent 실행 중 오류")

    asyncio.create_task(_run_with_log())

    return {"status": "accepted", "run_id": run_id, "error_type": error_info["type"]}


class CIPayload(BaseModel):
    repo: str                          # "owner/repo" 형식
    run_id: int | None = None          # 없으면 타임스탬프로 자동 생성
    logs: str = ""                     # 배포/빌드 로그 전문
    error_message: str = ""            # 로그 없이 에러 메시지만 전달할 때


@app.post("/webhook/ci")
async def ci_webhook(
    payload: CIPayload,
    x_ci_token: str = Header(default=""),
):
    """
    범용 CI webhook — git runner, 배포 스크립트 등에서 직접 호출.

    호출 예시 (배포 스크립트 내):
        curl -X POST http://<서버>:8080/webhook/ci \\
          -H 'Content-Type: application/json' \\
          -H 'x-ci-token: <CI_WEBHOOK_TOKEN>' \\
          -d '{"repo":"owner/repo","logs":"...","error_message":"..."}'
    """
    if CI_WEBHOOK_TOKEN and not hmac.compare_digest(CI_WEBHOOK_TOKEN, x_ci_token):
        raise HTTPException(status_code=401, detail="Invalid CI token")

    run_id = payload.run_id or int(time.time())
    logs = payload.logs
    if payload.error_message:
        logs = f"{payload.error_message}\n\n{logs}".strip()

    error_info = classify_error(logs)

    save_run_event(run_id=run_id, repo=payload.repo, error_info=error_info)

    import asyncio
    import logging

    async def _run_with_log():
        try:
            await run_healing_agent(run_id=run_id, repo=payload.repo, error_info=error_info, logs=logs)
        except Exception:
            logging.exception("[self-healing] agent 실행 중 오류")

    asyncio.create_task(_run_with_log())

    return {"status": "accepted", "run_id": run_id, "error_type": error_info["type"]}


@app.get("/health")
async def health():
    return {"status": "ok"}
