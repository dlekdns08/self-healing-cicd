"""
Slack 알림 — 에스컬레이션 및 인간 승인 요청
"""
import asyncio
import logging
import os
import time

import httpx

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_ALERT_CHANNEL", "#ci-alerts")
APPROVAL_TIMEOUT = int(os.environ.get("APPROVAL_TIMEOUT_SECONDS", "300"))  # 5분


def _post_message(text: str, blocks: list | None = None) -> str:
    """Slack 메시지 전송 후 ts(타임스탬프) 반환."""
    if not SLACK_BOT_TOKEN:
        logging.warning("[slack] SLACK_BOT_TOKEN 미설정 — 메시지 전송 생략")
        return ""
    payload: dict = {"channel": SLACK_CHANNEL, "text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        resp = httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            logging.error("[slack] 메시지 전송 실패: %s", data)
        return data.get("ts", "")
    except Exception as exc:
        logging.error("[slack] 메시지 전송 예외: %s", exc)
        return ""


def notify_started(run_id: int, repo: str, error_info: dict) -> None:
    text = (
        f":mag: *CI/CD 자가치유 시작*\n"
        f"저장소: `{repo}` | Run ID: `{run_id}`\n"
        f"에러 유형: `{error_info['type']}`\n"
        f"매칭 패턴: `{error_info.get('matched_pattern', 'N/A')}`"
    )
    _post_message(text)


def notify_resolved(run_id: int, repo: str, attempt_count: int) -> None:
    text = (
        f":white_check_mark: *CI/CD 자가치유 완료*\n"
        f"저장소: `{repo}` | Run ID: `{run_id}`\n"
        f"시도 횟수: {attempt_count}"
    )
    _post_message(text)


def notify_escalation(run_id: int, repo: str, error_info: dict, attempt_count: int) -> None:
    text = (
        f":rotating_light: *CI/CD 자가치유 실패 — 에스컬레이션*\n"
        f"저장소: `{repo}` | Run ID: `{run_id}`\n"
        f"에러 유형: `{error_info['type']}` | 시도 횟수: {attempt_count}\n"
        f"매칭 패턴: `{error_info.get('matched_pattern', 'N/A')}`\n"
        f"수동 확인이 필요합니다."
    )
    _post_message(text)


def _get_reactions(ts: str) -> list[str]:
    try:
        resp = httpx.get(
            "https://slack.com/api/reactions.get",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            params={"channel": SLACK_CHANNEL, "timestamp": ts},
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            return []
        reactions = data.get("message", {}).get("reactions", [])
        return [r["name"] for r in reactions]
    except Exception as exc:
        logging.error("[slack] reactions.get 예외: %s", exc)
        return []


async def request_human_approval(run_id: int, tool_name: str, tool_args: dict) -> bool:
    """
    Slack에 승인 요청 메시지를 보내고 리액션을 폴링합니다 (비동기).
    :white_check_mark: = 승인, :x: = 거부.
    APPROVAL_TIMEOUT 초 내 응답 없으면 자동 거부.
    """
    text = (
        f":warning: *[승인 필요] 고위험 툴 실행 요청*\n"
        f"Run ID: `{run_id}` | 툴: `{tool_name}`\n"
        f"인자: `{tool_args}`\n"
        f"승인하려면 :white_check_mark:, 거부하려면 :x: 리액션을 추가하세요.\n"
        f"({APPROVAL_TIMEOUT}초 내 응답 없으면 자동 거부됩니다)"
    )
    ts = _post_message(text)
    if not ts:
        logging.warning("[slack] 승인 메시지 전송 실패 — 자동 거부")
        return False

    deadline = time.monotonic() + APPROVAL_TIMEOUT
    while time.monotonic() < deadline:
        await asyncio.sleep(10)  # 이벤트 루프를 막지 않는 비동기 대기
        reactions = await asyncio.to_thread(_get_reactions, ts)
        if "white_check_mark" in reactions:
            logging.info("[slack] 승인 확인 | run_id=%s tool=%s", run_id, tool_name)
            return True
        if "x" in reactions:
            logging.info("[slack] 거부 확인 | run_id=%s tool=%s", run_id, tool_name)
            return False

    _post_message(f":clock1: Run ID `{run_id}` 승인 타임아웃 — 자동 거부")
    return False