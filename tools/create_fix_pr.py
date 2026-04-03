"""
Tool: create_fix_pr — 수정 사항을 새 브랜치에 커밋하고 GitHub PR 생성
git_commit_push(직접 push) 대신 사용하면 코드 리뷰 프로세스를 거칠 수 있어 더 안전합니다.
"""
import os
import subprocess
import time

import httpx
from langchain_core.tools import tool


def _run(cmd: list[str], cwd: str) -> tuple[int, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result.returncode, (result.stdout + result.stderr).strip()


def _get_headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@tool
def create_fix_pr(
    repo_path: str,
    repo: str,
    commit_message: str,
    pr_title: str,
    pr_body: str,
) -> str:
    """
    변경된 파일을 새 브랜치에 커밋하고 GitHub PR을 생성합니다.
    git_commit_push 대신 이 툴을 사용하면 머지 전 코드 리뷰가 가능합니다.
    브랜치명은 self-healing/fix-{timestamp} 형식으로 자동 생성됩니다.
    repo_path: 저장소 루트 절대경로 (예: /app/api)
    repo: GitHub 저장소 full name (예: owner/repo)
    commit_message: 커밋 메시지
    pr_title: PR 제목
    pr_body: PR 본문 (수정 이유, 변경 내용 명시)
    """
    if not os.path.isdir(repo_path):
        return f"ERROR: 경로가 존재하지 않습니다 — {repo_path}"

    branch_name = f"self-healing/fix-{int(time.time())}"

    # GITHUB_TOKEN을 remote URL에 삽입
    token = os.environ.get("GITHUB_TOKEN", "")
    code, remote = _run(["git", "remote", "get-url", "origin"], cwd=repo_path)
    if code != 0:
        return f"ERROR: remote URL 조회 실패\n{remote}"
    if token and "github.com" in remote and "@" not in remote:
        authed_remote = remote.replace("https://", f"https://{token}@")
        _run(["git", "remote", "set-url", "origin", authed_remote], cwd=repo_path)

    # 현재 브랜치 이름 저장 (실패 시 복구용)
    _, base_branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)

    steps = [
        (["git", "config", "user.email", "self-healing@ci.local"], "git config email"),
        (["git", "config", "user.name", "Self-Healing CI"], "git config name"),
        (["git", "checkout", "-b", branch_name], f"브랜치 생성 {branch_name}"),
        (["git", "add", "-A"], "git add"),
        (["git", "commit", "-m", commit_message], "git commit"),
        (["git", "push", "-u", "origin", branch_name], "git push"),
    ]
    for cmd, label in steps:
        code, out = _run(cmd, cwd=repo_path)
        if code != 0:
            if label == "git commit" and "nothing to commit" in out:
                _run(["git", "checkout", base_branch], cwd=repo_path)
                return "ERROR: 변경된 파일이 없습니다. apply_patch가 먼저 실행되었는지 확인하세요."
            _run(["git", "checkout", base_branch], cwd=repo_path)
            return f"ERROR: {label} 실패\n{out}"

    # GitHub API로 PR 생성
    url = f"https://api.github.com/repos/{repo}/pulls"
    resp = httpx.post(url, headers=_get_headers(), json={
        "title": pr_title,
        "body": pr_body,
        "head": branch_name,
        "base": base_branch,
    })
    if resp.status_code == 201:
        pr_url = resp.json().get("html_url", "")
        return f"SUCCESS: PR 생성 완료 — {pr_url} (브랜치: {branch_name})"
    return f"ERROR: PR 생성 실패 — HTTP {resp.status_code}\n{resp.text}"
