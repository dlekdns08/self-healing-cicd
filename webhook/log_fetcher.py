"""
GitHub API — workflow run 로그 다운로드
"""
import io
import os
import zipfile

import httpx

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
BASE_URL = "https://api.github.com"


async def fetch_workflow_logs(repo: str, run_id: int) -> str:
    """ZIP으로 압축된 로그를 다운받아 텍스트로 합쳐서 반환."""
    url = f"{BASE_URL}/repos/{repo}/actions/runs/{run_id}/logs"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url, headers=HEADERS)
        resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        parts = []
        for name in zf.namelist():
            if name.endswith(".txt"):
                parts.append(f"=== {name} ===\n")
                parts.append(zf.read(name).decode("utf-8", errors="replace"))
        return "\n".join(parts)
