"""
Tool: re_trigger_pipeline — GitHub Actions 워크플로우 재실행
"""
import os

import httpx
from langchain_core.tools import tool


def _get_headers() -> dict:
    """헤더를 호출 시점에 생성 — import 시 토큰이 없어도 크래시 방지."""
    token = os.environ.get("GITHUB_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@tool
def re_trigger_pipeline(repo: str, run_id: int) -> str:
    """
    실패한 GitHub Actions 워크플로우를 재실행합니다.
    수정 적용 후 결과를 검증할 때 사용하세요.
    """
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/rerun"
    resp = httpx.post(url, headers=_get_headers())
    if resp.status_code == 201:
        return f"SUCCESS: 파이프라인 재실행 요청 완료 (run_id={run_id})"
    return f"ERROR: 재실행 실패 — HTTP {resp.status_code}\n{resp.text}"