# Self-Healing CI/CD

GitOps 배포 파이프라인에서 에러가 발생하면 LLM 에이전트가 자동으로 원인을 분석하고, 코드를 수정해 GitHub에 커밋 & 푸시까지 처리하는 자가치유 시스템입니다.

## 동작 흐름

```
GitHub Actions 실패
       ↓
  /webhook/ci 호출 (curl)
       ↓
  로그 파싱 & 에러 분류
       ↓
  LLM 에이전트 실행
       ↓
  read_file → apply_patch → security_scan → git_commit_push → re_trigger_pipeline
       ↓                          ↓
  Slack 해결 알림          HIGH 취약점 발견 시
                            에스컬레이션 & Slack 알림
```

## 프로젝트 구조

```
self-healing-cicd/
├── main.py                  # 진입점 (FastAPI + uvicorn)
├── webhook/
│   ├── server.py            # /webhook/ci, /webhook/github 엔드포인트
│   ├── parser.py            # CI 로그 파싱 & 에러 유형 분류
│   └── log_fetcher.py       # GitHub Actions 로그 수집
├── agent/
│   ├── graph.py             # LangGraph ReAct 에이전트 (진단→실행→검증 루프)
│   └── prompts.py           # 에러 유형별 시스템 프롬프트
├── tools/
│   ├── read_file.py         # 호스트 파일 읽기
│   ├── apply_patch.py       # unified diff 파일 패치
│   ├── security_scan.py     # 보안 취약점 스캔 (bandit + 패턴 분석)
│   ├── git_push.py          # git commit & push
│   ├── pipeline.py          # GitHub Actions 재실행
│   └── rollback.py          # 커밋 롤백 (인간 승인 필요)
├── notifier/
│   └── slack.py             # Slack 알림 (시작/해결/에스컬레이션)
├── config/
│   └── safety.py            # 가드레일 설정
├── storage/
│   └── db.py                # SQLite 실행 이력 저장
└── sandbox/
    └── docker_runner.py     # Docker 격리 실행 환경
```

## 지원 에러 유형

| 유형 | 예시 |
|------|------|
| `build` | SyntaxError, IndentationError, TypeScript 컴파일 오류 |
| `runtime` | Traceback, NameError, AttributeError |
| `dependency` | ModuleNotFoundError, npm ERR!, Cannot find module |
| `test_failure` | pytest failed, Jest fail, AssertionError |
| `lint` | ESLint error, mypy error, ruff error |
| `deploy` | container exited, Health check failed, port already in use |
| `infra` | Connection refused, SSL error, 502 Bad Gateway |
| `config` | Missing env variable, YAML error |
| `database` | OperationalError, migration failed |

## 에이전트 툴

| 툴 | 설명 |
|----|------|
| `read_file` | 호스트 파일 내용 읽기 (apply_patch 전 필수) |
| `run_shell` | Docker 샌드박스 내 명령 실행 (검증용, 파일 접근 불가) |
| `apply_patch` | unified diff를 실제 파일에 적용 |
| `security_scan` | bandit + 패턴 분석으로 보안 취약점 검사 |
| `git_commit_push` | 수정된 파일을 GitHub에 커밋 & 푸시 |
| `re_trigger_pipeline` | GitHub Actions 워크플로우 재실행 |
| `rollback_commit` | 지정 커밋으로 revert (Slack 인간 승인 필요) |

## 보안 스캔 항목

- 하드코딩된 시크릿/API 키/비밀번호
- `eval()`, `exec()`, `os.system()` 사용
- `shell=True` subprocess
- pickle 역직렬화
- SQL 문자열 연결 (인젝션)
- npm 의존성 취약점 (npm audit)
- bandit 정적 분석 (Python)

HIGH 이상 이슈 발견 시 커밋을 차단하고 Slack으로 에스컬레이션합니다.

## 설치 & 실행

```bash
# uv 설치
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env

# 의존성 설치
cd /home/self-healing-cicd
uv sync

# 환경변수 설정
cp .env.example .env
# .env 편집 후

# 실행
uv run main.py
```

## 환경변수

| 변수 | 설명 |
|------|------|
| `GITHUB_TOKEN` | GitHub API 접근 토큰 (로그 수집 + 코드 푸시) |
| `GITHUB_WEBHOOK_SECRET` | GitHub Webhook 서명 검증 키 |
| `CI_WEBHOOK_TOKEN` | /webhook/ci 인증 토큰 (API, Blog 레포 공유) |
| `LLM_PROVIDER` | `anthropic` / `openai` / `ollama` |
| `ANTHROPIC_API_KEY` | Anthropic API 키 |
| `CLAUDE_MODEL` | 사용할 Claude 모델 (기본: claude-sonnet-4-6) |
| `SLACK_BOT_TOKEN` | Slack Bot OAuth 토큰 (`xoxb-`로 시작) |
| `SLACK_ALERT_CHANNEL` | Slack 알림 채널 (예: #ci-alerts) |
| `APPROVAL_TIMEOUT_SECONDS` | 인간 승인 대기 시간 (기본: 300초) |

## 배포 레포에서 webhook 호출 예시

```yaml
# .github/workflows/deploy.yaml
- name: Notify self-healing on failure
  if: failure()
  run: |
    LOGS=$(cat /tmp/deploy_logs.txt 2>/dev/null | tail -c 4000 || echo "Deploy failed")
    curl -X POST http://<서버IP>:8080/webhook/ci \
      -H 'Content-Type: application/json' \
      -H "x-ci-token: ${{ secrets.CI_WEBHOOK_TOKEN }}" \
      -d "{\"repo\":\"${{ github.repository }}\",\"run_id\":${{ github.run_id }},\"logs\":$(echo "$LOGS" | jq -Rs .)}"
```

## 자동 배포

`main` 브랜치에 push하면 GitHub Actions runner가 자동으로 Docker 컨테이너를 재빌드합니다.

```
push → runner → docker compose up --build → health check (localhost:8080/health)
```
