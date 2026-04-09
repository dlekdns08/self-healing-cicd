"""
Tool: rollback_commit — 지정 커밋으로 revert PR 생성 (고위험)
인간 승인 게이트를 반드시 통과한 후에만 실행됩니다.
"""
import os
import subprocess
import time

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


def _run(cmd: list[str], cwd: str) -> tuple[int, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result.returncode, (result.stdout + result.stderr).strip()


# 저장소 이름 → 로컬 경로 매핑 (graph.py와 동일한 기본값)
_REPO_PATH_DEFAULTS: dict[str, str] = {
    "api": "/app/api",
    "blog": "/app/blog",
}


def _resolve_repo_path(repo: str) -> str:
    """'owner/repo' → 로컬 절대경로. REPO_PATH_MAP 환경변수 우선 적용."""
    repo_name = repo.split("/")[-1].lower()
    raw = os.environ.get("REPO_PATH_MAP", "")
    overrides: dict[str, str] = {}
    for entry in raw.split(","):
        if ":" in entry:
            name, path = entry.split(":", 1)
            overrides[name.strip().lower()] = path.strip()
    mapping = {**_REPO_PATH_DEFAULTS, **overrides}
    return mapping.get(repo_name, f"/home/{repo_name}")


@tool
def rollback_commit(repo: str, sha: str, reason: str, repo_path: str = "") -> str:
    """
    지정한 커밋(sha)을 되돌리는 revert PR을 생성합니다.
    다른 방법으로 복구 불가능할 때만 사용하세요.
    반드시 reason(롤백 이유)을 명시해야 합니다.
    이 툴은 인간 승인이 필요합니다.
    repo_path: 저장소 로컬 경로 (미지정 시 자동 추론)
    """
    resolved_path = repo_path or _resolve_repo_path(repo)
    if not os.path.isdir(resolved_path):
        return f"ERROR: 저장소 경로가 존재하지 않습니다 — {resolved_path}"

    # 1. 커밋 존재 여부 확인
    url = f"https://api.github.com/repos/{repo}/git/commits/{sha}"
    resp = httpx.get(url, headers=_get_headers())
    if resp.status_code != 200:
        return f"ERROR: 커밋 조회 실패 — HTTP {resp.status_code}"
    commit_msg = resp.json().get("message", "")

    # 2. GITHUB_TOKEN을 remote URL에 삽입
    token = os.environ.get("GITHUB_TOKEN", "")
    code, remote = _run(["git", "remote", "get-url", "origin"], cwd=resolved_path)
    if code != 0:
        return f"ERROR: remote URL 조회 실패\n{remote}"
    if token and "github.com" in remote and "@" not in remote:
        authed_remote = remote.replace("https://", f"https://{token}@")
        _run(["git", "remote", "set-url", "origin", authed_remote], cwd=resolved_path)

    # 3. 현재 브랜치 저장 (실패 시 복구용)
    _, base_branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=resolved_path)

    # 4. revert 브랜치 생성
    branch_name = f"self-healing/revert-{sha[:8]}-{int(time.time())}"

    setup_steps = [
        (["git", "config", "user.email", "self-healing@ci.local"], "git config email"),
        (["git", "config", "user.name", "Self-Healing CI"], "git config name"),
        (["git", "fetch", "origin"], "git fetch"),
        (["git", "checkout", "-b", branch_name], f"브랜치 생성 {branch_name}"),
    ]
    for cmd, label in setup_steps:
        code, out = _run(cmd, cwd=resolved_path)
        if code != 0:
            _run(["git", "checkout", base_branch], cwd=resolved_path)
            return f"ERROR: {label} 실패\n{out}"

    # 5. git revert (커밋 없이 — 메시지 커스터마이징을 위해)
    code, out = _run(["git", "revert", "--no-edit", "--no-commit", sha], cwd=resolved_path)
    if code != 0:
        _run(["git", "revert", "--abort"], cwd=resolved_path)
        _run(["git", "checkout", base_branch], cwd=resolved_path)
        return f"ERROR: git revert 실패 (충돌 가능성)\n{out}"

    revert_msg = (
        f"Revert: {sha[:8]} — {reason[:80]}\n\n"
        f"원본 커밋 메시지: {commit_msg[:200]}\n"
        f"롤백 이유: {reason}\n\n"
        f"이 커밋은 Self-Healing CI/CD에 의해 자동 생성되었습니다."
    )
    code, out = _run(["git", "commit", "-m", revert_msg], cwd=resolved_path)
    if code != 0:
        _run(["git", "checkout", base_branch], cwd=resolved_path)
        return f"ERROR: git commit 실패\n{out}"

    # 6. 브랜치 push
    code, out = _run(["git", "push", "-u", "origin", branch_name], cwd=resolved_path)
    if code != 0:
        _run(["git", "checkout", base_branch], cwd=resolved_path)
        return f"ERROR: git push 실패\n{out}"

    # 7. GitHub PR 생성
    pr_url_api = f"https://api.github.com/repos/{repo}/pulls"
    pr_resp = httpx.post(pr_url_api, headers=_get_headers(), json={
        "title": f"[Rollback] Revert {sha[:8]}: {reason[:60]}",
        "body": (
            f"## 자동 롤백 PR\n\n"
            f"**롤백 대상 커밋:** `{sha}`\n"
            f"**원본 커밋 메시지:** {commit_msg[:300]}\n"
            f"**롤백 이유:** {reason}\n\n"
            f"> 이 PR은 Self-Healing CI/CD에 의해 자동 생성되었습니다. "
            f"머지 전 반드시 변경 내용을 검토하세요."
        ),
        "head": branch_name,
        "base": base_branch,
    })

    # 현재 브랜치 복구
    _run(["git", "checkout", base_branch], cwd=resolved_path)

    if pr_resp.status_code == 201:
        pr_html = pr_resp.json().get("html_url", "")
        return (
            f"SUCCESS: 롤백 PR 생성 완료 — {pr_html}\n"
            f"(커밋: {sha[:8]}, 브랜치: {branch_name})\n"
            f"PR을 검토 후 머지하세요."
        )
    return f"ERROR: PR 생성 실패 — HTTP {pr_resp.status_code}\n{pr_resp.text}"
