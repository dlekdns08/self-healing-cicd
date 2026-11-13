"""
Tool: git_commit_push — 수정된 파일을 커밋하고 GitHub에 푸시
"""
from langchain_core.tools import tool

from tools._guard import resolve_repo_root, run_git, scrub_remote_credentials


@tool
def git_commit_push(repo_path: str, message: str) -> str:
    """
    지정된 로컬 저장소 경로에서 변경된 파일을 git add → commit → push합니다.
    apply_patch로 파일을 수정한 뒤 반드시 이 툴을 호출해 GitHub에 반영하세요.
    repo_path: 저장소 루트 절대경로 (예: /app/api)
    message: 커밋 메시지
    """
    root = resolve_repo_root(repo_path)
    if root is None:
        return f"ERROR: 등록된 저장소 경로가 아닙니다 — {repo_path}"

    # 토큰은 remote URL 에 저장하지 않고 환경변수로만 전달 (tools/_guard.git_env)
    scrub_remote_credentials(root)

    steps = [
        (["config", "user.email", "self-healing@ci.local"], "git config email"),
        (["config", "user.name", "Self-Healing CI"], "git config name"),
        (["add", "-A"], "git add"),
        (["commit", "-m", message], "git commit"),
        (["push"], "git push"),
    ]

    for args, label in steps:
        code, out = run_git(args, cwd=root)
        if code != 0:
            # 커밋할 변경사항 없음은 에러 아님
            if label == "git commit" and "nothing to commit" in out:
                return "ERROR: 변경된 파일이 없습니다. apply_patch가 먼저 실행되었는지 확인하세요."
            return f"ERROR: {label} 실패\n{out}"

    return f"SUCCESS: 변경사항이 GitHub에 푸시되었습니다 (repo={root})"
