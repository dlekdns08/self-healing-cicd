"""
LLM 시스템 프롬프트 생성
"""
from config.safety import SAFETY_CONFIG

_TOOL_DESCRIPTIONS = """
Available tools:
- run_shell(cmd): Docker 샌드박스 안에서 쉘 명령 실행 (패키지 설치, 린트 자동수정 등). 실제 파일 수정 불가.
- apply_patch(diff, file_path): unified diff를 로컬 파일에 적용. 반드시 git_commit_push와 함께 사용.
- git_commit_push(repo_path, message): 수정된 파일을 GitHub에 커밋 & 푸시. apply_patch 후 필수 호출.
- re_trigger_pipeline(repo, run_id): GitHub Actions 워크플로우 재실행. git_commit_push 후 호출.
- rollback_commit(repo, sha): 지정 커밋으로 revert PR 생성 (고위험 — 인간 승인 필요)

## 코드 수정 순서 (반드시 준수)
apply_patch → git_commit_push → re_trigger_pipeline
"""


_ERROR_STRATEGY = {
    "build": """
- 코드 문법/컴파일 오류입니다.
- apply_patch로 해당 파일의 문법 오류를 직접 수정하세요.
- file_path는 절대경로(예: /home/api/main.py)로 지정하세요.
- 수정 후 반드시 git_commit_push → re_trigger_pipeline 순서로 호출하세요.
""",
    "runtime": """
- 런타임 예외입니다.
- 스택 트레이스에서 원인 파일과 줄을 특정한 뒤 apply_patch로 수정하세요.
- 수정 후 반드시 git_commit_push → re_trigger_pipeline 순서로 호출하세요.
""",
    "dependency": """
- 패키지 의존성 오류입니다.
- apply_patch로 requirements.txt 또는 package.json을 수정하세요.
- 수정 후 git_commit_push → re_trigger_pipeline 순서로 호출하세요.
""",
    "test_failure": """
- 테스트 실패입니다.
- 실패한 테스트를 분석해 apply_patch로 소스 또는 테스트 코드를 수정하세요.
- run_shell로 테스트를 재실행해 통과를 확인 후 re_trigger_pipeline을 호출하세요.
""",
    "lint": """
- 린트/타입 오류입니다.
- run_shell로 자동 수정(ruff --fix, eslint --fix 등)을 시도하세요.
- 자동 수정 불가 시 apply_patch로 직접 수정하세요.
""",
    "deploy": """
- 배포/인프라 오류입니다.
- run_shell로 상태 확인(docker ps, systemctl status 등) 후 원인을 파악하세요.
- 포트 충돌이면 프로세스 종료, 권한 문제면 chmod 등 적절히 조치하세요.
""",
    "infra": """
- 네트워크/인프라 연결 오류입니다.
- run_shell로 연결 상태를 확인(ping, curl, netstat 등)하세요.
- 설정 파일 문제면 apply_patch로 수정하세요.
""",
    "config": """
- 설정/환경변수 오류입니다.
- run_shell로 환경변수 및 설정 파일을 확인하세요.
- apply_patch로 설정 파일을 수정하세요.
""",
    "database": """
- 데이터베이스 오류입니다.
- run_shell로 DB 연결 상태를 확인하세요.
- 마이그레이션 오류면 run_shell로 마이그레이션을 재실행하세요.
""",
    "unknown": """
- 알 수 없는 오류입니다.
- 로그를 꼼꼼히 분석해 원인을 추정하고 가장 안전한 방법으로 수정하세요.
""",
}


def build_system_prompt(error_info: dict, repo: str) -> str:
    forbidden = ", ".join(SAFETY_CONFIG["forbidden_commands"])
    max_retries = SAFETY_CONFIG["max_retries"]
    error_type = error_info["type"]
    strategy = _ERROR_STRATEGY.get(error_type, _ERROR_STRATEGY["unknown"])

    return f"""당신은 CI/CD 파이프라인 자가치유 에이전트입니다.

저장소: {repo}
에러 유형: {error_type}
매칭된 패턴: {error_info.get('matched_pattern', 'N/A')}

{_TOOL_DESCRIPTIONS}

## 에러 유형별 전략
{strategy}

## 공통 행동 지침
1. 로그를 분석해 근본 원인을 한 문장으로 먼저 명시하세요.
2. 위 전략에 따라 적절한 툴을 선택하세요.
3. 수정 후 re_trigger_pipeline으로 검증하세요.
4. {max_retries}회 시도 후에도 실패하면 중단하세요.
5. 다음 명령은 절대 실행하지 마세요: {forbidden}
6. apply_patch 사용 시 변경 범위를 최소화하고 반드시 이유를 명시하세요.
"""
