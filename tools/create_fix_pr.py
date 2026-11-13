"""
Tool: create_fix_pr — 수정 사항을 새 브랜치에 커밋하고 GitHub PR 생성
git_commit_push(직접 push) 대신 사용하면 코드 리뷰 프로세스를 거칠 수 있어 더 안전합니다.
"""
import os
import time

import httpx
from langchain_core.tools import tool

from tools._guard import is_known_repo, resolve_repo_root, run_git, scrub_remote_credentials


def _run(cmd: list[str], cwd: str) -> tuple[int, str]:
    """git 서브커맨드 실행 — 토큰은 환경변수로만 전달되고 출력에서 마스킹된다."""
    assert cmd[0] == "git"
    return run_git(cmd[1:], cwd=cwd)


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
    root = resolve_repo_root(repo_path)
    if root is None:
        return f"ERROR: 등록된 저장소 경로가 아닙니다 — {repo_path}"
    if not is_known_repo(repo):
        return f"ERROR: 허용되지 않은 저장소입니다 — {repo}"
    repo_path = root

    branch_name = f"self-healing/fix-{int(time.time())}"

    # 토큰은 remote URL 에 저장하지 않고 환경변수로만 전달 (tools/_guard.git_env)
    scrub_remote_credentials(repo_path)

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
    resp = httpx.post(url, headers=_get_headers(), timeout=30, json={
        "title": pr_title,
        "body": pr_body,
        "head": branch_name,
        "base": base_branch,
    })
    if resp.status_code == 201:
        pr_url = resp.json().get("html_url", "")
        return f"SUCCESS: PR 생성 완료 — {pr_url} (브랜치: {branch_name})"
    return f"ERROR: PR 생성 실패 — HTTP {resp.status_code}\n{resp.text}"
