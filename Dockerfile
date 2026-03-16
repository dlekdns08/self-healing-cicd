FROM python:3.12-slim

WORKDIR /app

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 의존성 먼저 설치 (캐시 활용)
COPY pyproject.toml .
RUN uv sync --no-install-project --no-dev

# 소스 복사
COPY . .
RUN uv sync --no-dev

CMD ["uv", "run", "main.py"]
