"""
애플리케이션 진입점
"""
from dotenv import load_dotenv
load_dotenv()

import logging
import os
import sys
import uvicorn
from storage.db import init_db
from webhook.server import app  # noqa: F401 — lifespan에서 init_db 호출됨


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)


def _validate_env() -> None:
    """필수 환경변수 검증 — 미설정 시 명확한 오류 메시지로 즉시 종료."""
    required = ["GITHUB_TOKEN", "GITHUB_WEBHOOK_SECRET"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        logger.error("필수 환경변수가 설정되지 않았습니다: %s", ", ".join(missing))
        logger.error(".env 파일 또는 환경변수를 확인하세요 (.env.example 참고)")
        sys.exit(1)

    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("LLM_PROVIDER=anthropic이지만 ANTHROPIC_API_KEY가 설정되지 않았습니다")
        sys.exit(1)
    elif provider in ("openai", "openai_compatible") and not os.environ.get("OPENAI_API_KEY"):
        logger.error("LLM_PROVIDER=%s이지만 OPENAI_API_KEY가 설정되지 않았습니다", provider)
        sys.exit(1)
    elif provider == "ollama" and not os.environ.get("OLLAMA_BASE_URL"):
        logger.warning("LLM_PROVIDER=ollama이지만 OLLAMA_BASE_URL이 설정되지 않았습니다 — 기본값 http://localhost:11434 사용")


if __name__ == "__main__":
    _validate_env()
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=8080, reload=False)
