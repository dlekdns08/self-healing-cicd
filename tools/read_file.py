"""
Tool: read_file — 호스트 파일시스템의 파일 내용 읽기
"""
import os

from langchain_core.tools import tool

_ALLOWED_ROOTS = ["/app/api", "/app/blog"]


@tool
def read_file(file_path: str) -> str:
    """
    호스트의 파일 내용을 읽어 반환합니다. apply_patch 전에 현재 파일 내용을 확인할 때 사용하세요.
    file_path: 절대경로 (예: /app/api/main.py)
    허용 경로: /app/api/**, /app/blog/**
    """
    file_path = os.path.normpath(file_path)
    if not any(file_path.startswith(root) for root in _ALLOWED_ROOTS):
        return f"ERROR: 허용되지 않은 경로입니다. 허용 경로: {_ALLOWED_ROOTS}"
    if not os.path.isfile(file_path):
        return f"ERROR: 파일이 존재하지 않습니다 — {file_path}"
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.splitlines()
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        return f"# {file_path} ({len(lines)} lines)\n{numbered}"
    except Exception as e:
        return f"ERROR: 파일 읽기 실패 — {e}"
