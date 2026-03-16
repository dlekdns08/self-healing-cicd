"""
안전 가드레일 설정
"""

SAFETY_CONFIG: dict = {
    # 자동 시도 횟수 — 초과 시 에스컬레이션
    "max_retries": 3,

    # apply_patch가 수정할 수 있는 파일 확장자
    "allowed_file_extensions": [".py", ".ts", ".tsx", ".js", ".json", "requirements.txt", "package.json"],

    # run_shell에서 절대 실행 불가 명령어 패턴
    "forbidden_commands": [
        "rm -rf",
        "DROP TABLE",
        "git push --force",
        "chmod 777",
        "> /dev/sda",
        ":(){ :|:& };:",  # fork bomb
    ],

    # 실행 전 인간 승인이 필요한 툴
    "require_human_approval_for": [
        "rollback_commit",
    ],

    # Docker 샌드박스 강제 여부
    "sandbox": True,
}
