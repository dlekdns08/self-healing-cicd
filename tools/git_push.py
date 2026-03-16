"""
Tool: git_commit_push — 수정된 파일을 커밋하고 GitHub에 푸시
"""
import os
import subprocess

from langchain_core.tools import tool

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


def _run(cmd: list[str], cwd: str) -> tuple[int, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result.returncode, (result.stdout + result.stderr).strip()


@tool
def git_commit_push(repo_path: str, message: str) -> str:
    """
    지정된 로컬 저장소 경로에서 변경된 파일을 git add → commit → push합니다.
    apply_patch로 파일을 수정한 뒤 반드시 이 툴을 호출해 GitHub에 반영하세요.
    repo_path: 저장소 루트 절대경로 (예: /home/api)
    message: 커밋 메시지
    """
    if not os.path.isdir(repo_path):
        return f"ERROR: 경로가 존재하지 않습니다 — {repo_path}"

    # GITHUB_TOKEN을 remote URL에 삽입
    code, remote = _run(["git", "remote", "get-url", "origin"], cwd=repo_path)
    if code != 0:
        return f"ERROR: remote URL 조회 실패\n{remote}"

    if GITHUB_TOKEN and "github.com" in remote and "@" not in remote:
        authed_remote = remote.replace("https://", f"https://{GITHUB_TOKEN}@")
        _run(["git", "remote", "set-url", "origin", authed_remote], cwd=repo_path)

    steps = [
        (["git", "config", "user.email", "self-healing@ci.local"], "git config email"),
        (["git", "config", "user.name", "Self-Healing CI"], "git config name"),
        (["git", "add", "-A"], "git add"),
        (["git", "commit", "-m", message], "git commit"),
        (["git", "push"], "git push"),
    ]

    for cmd, label in steps:
        code, out = _run(cmd, cwd=repo_path)
        if code != 0:
            # 커밋할 변경사항 없음은 에러 아님
            if label == "git commit" and "nothing to commit" in out:
                return "ERROR: 변경된 파일이 없습니다. apply_patch가 먼저 실행되었는지 확인하세요."
            return f"ERROR: {label} 실패\n{out}"

    return f"SUCCESS: 변경사항이 GitHub에 푸시되었습니다 (repo={repo_path})"
