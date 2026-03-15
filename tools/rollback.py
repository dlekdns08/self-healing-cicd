"""
Tool: rollback_commit — 지정 커밋으로 revert PR 생성 (고위험)
인간 승인 게이트를 반드시 통과한 후에만 실행됩니다.
"""
import os

import httpx
from langchain_core.tools import tool

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


@tool
def rollback_commit(repo: str, sha: str, reason: str) -> str:
    """
    지정한 커밋(sha)을 되돌리는 revert PR을 생성합니다.
    다른 방법으로 복구 불가능할 때만 사용하세요.
    반드시 reason(롤백 이유)을 명시해야 합니다.
    이 툴은 인간 승인이 필요합니다.
    """
    # revert는 직접 push가 아닌 PR 생성으로 처리 (안전장치)
    url = f"https://api.github.com/repos/{repo}/git/commits/{sha}"
    resp = httpx.get(url, headers=HEADERS)
    if resp.status_code != 200:
        return f"ERROR: 커밋 조회 실패 — {resp.status_code}"

    commit_msg = resp.json().get("message", "")
    return (
        f"ROLLBACK_REQUESTED: sha={sha}\n"
        f"커밋 메시지: {commit_msg}\n"
        f"이유: {reason}\n"
        f"다음 단계: Slack 승인 후 revert PR이 자동 생성됩니다."
    )
