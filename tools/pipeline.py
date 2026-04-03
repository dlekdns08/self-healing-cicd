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


@tool
def check_pipeline_status(repo: str, run_id: int) -> str:
    """
    GitHub Actions 워크플로우 실행의 현재 상태를 확인합니다.
    re_trigger_pipeline 호출 후 파이프라인이 실제로 성공했는지 검증할 때 사용하세요.
    반환 형식:
      - 진행 중: "status: queued|in_progress | conclusion: (진행 중)"
      - 완료: "status: completed | conclusion: success|failure|cancelled"
    """
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
    resp = httpx.get(url, headers=_get_headers())
    if resp.status_code != 200:
        return f"ERROR: 상태 조회 실패 — HTTP {resp.status_code}\n{resp.text}"

    data = resp.json()
    status = data.get("status", "unknown")
    conclusion = data.get("conclusion")
    html_url = data.get("html_url", "")

    if status == "completed":
        return f"status: completed | conclusion: {conclusion} | url: {html_url}"
    return f"status: {status} | conclusion: (진행 중) | url: {html_url}"