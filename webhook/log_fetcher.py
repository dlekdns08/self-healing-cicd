"""
GitHub API — workflow run 로그 다운로드
"""
import io
import os
import zipfile

import httpx

BASE_URL = "https://api.github.com"


def _get_headers() -> dict:
    """헤더를 호출 시점에 생성 — import 시 토큰이 없어도 크래시 방지."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN 환경변수가 설정되지 않았습니다.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def fetch_workflow_logs(repo: str, run_id: int) -> str:
    """ZIP으로 압축된 로그를 다운받아 텍스트로 합쳐서 반환."""
    url = f"{BASE_URL}/repos/{repo}/actions/runs/{run_id}/logs"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url, headers=_get_headers())
        resp.raise_for_status()
        content = resp.content  # context manager 종료 전에 읽기

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            parts = []
            for name in zf.namelist():
                if name.endswith(".txt"):
                    parts.append(f"=== {name} ===\n")
                    parts.append(zf.read(name).decode("utf-8", errors="replace"))
            return "\n".join(parts)
    except zipfile.BadZipFile:
        # 로그가 ZIP이 아닌 경우 (예: 텍스트 직접 반환)
        return content.decode("utf-8", errors="replace")