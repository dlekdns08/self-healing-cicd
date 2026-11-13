"""
FastAPI webhook server — GitHub Actions workflow_run 이벤트 수신 + 범용 CI webhook
"""
import hashlib
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from agent.graph import run_healing_agent
from storage.db import init_db, save_run_event
from tools._guard import is_known_repo
from webhook.log_fetcher import fetch_workflow_logs
from webhook.parser import classify_error

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 DB 초기화 — uvicorn으로 직접 실행해도 동작."""
    init_db()
    logger.info("[webhook] DB 초기화 완료")
    yield


app = FastAPI(title="Self-Healing CI/CD Webhook", lifespan=lifespan)

_GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
CI_WEBHOOK_TOKEN = os.environ.get("CI_WEBHOOK_TOKEN", "")

# 로그는 그대로 LLM 프롬프트에 들어간다 — 비용/DoS 방지를 위해 꼬리 부분만 유지
MAX_LOG_CHARS = 200_000


def _clip_logs(logs: str) -> str:
    return logs if len(logs) <= MAX_LOG_CHARS else logs[-MAX_LOG_CHARS:]


def _verify_signature(body: bytes, sig_header: str) -> None:
    secret = _GITHUB_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(status_code=500, detail="GITHUB_WEBHOOK_SECRET이 설정되지 않았습니다")
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    # bytes 비교 — 비ASCII 헤더가 들어와도 TypeError(500) 대신 401
    if not hmac.compare_digest(expected.encode(), sig_header.encode("utf-8", "replace")):
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
    if not isinstance(run_id, int) or not is_known_repo(repo):
        return {"status": "ignored", "reason": "unknown repository"}

    logs = _clip_logs(await fetch_workflow_logs(repo, run_id))
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
    # fail-closed: 토큰 미설정 시 인증 없이 열리던 문제 수정.
    # 이 엔드포인트는 코드 수정 + git push 권한을 가진 에이전트를 기동하므로 반드시 인증한다.
    if not CI_WEBHOOK_TOKEN:
        raise HTTPException(status_code=503, detail="CI_WEBHOOK_TOKEN이 설정되지 않았습니다")
    if not hmac.compare_digest(CI_WEBHOOK_TOKEN.encode(), x_ci_token.encode("utf-8", "replace")):
        raise HTTPException(status_code=401, detail="Invalid CI token")
    if not is_known_repo(payload.repo):
        raise HTTPException(status_code=400, detail="Unknown repository")

    run_id = payload.run_id or int(time.time())
    logs = payload.logs
    if payload.error_message:
        logs = f"{payload.error_message}\n\n{logs}".strip()
    logs = _clip_logs(logs)

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
