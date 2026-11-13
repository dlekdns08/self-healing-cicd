"""
Tool: read_file — 호스트 파일시스템의 파일 내용 읽기
"""
from langchain_core.tools import tool

from tools._guard import resolve_repo_file

_MAX_BYTES = 512 * 1024


@tool
def read_file(file_path: str) -> str:
    """
    호스트의 파일 내용을 읽어 반환합니다. apply_patch 전에 현재 파일 내용을 확인할 때 사용하세요.
    file_path: 절대경로 (예: /app/api/main.py)
    허용 경로: 등록된 저장소 내부만 (/app/api/**, /app/blog/**). .env·.git·키 파일은 읽을 수 없습니다.
    """
    real, err = resolve_repo_file(file_path)
    if real is None:
        return err
    import os
    if not os.path.isfile(real):
        return f"ERROR: 파일이 존재하지 않습니다 — {file_path}"
    if os.path.getsize(real) > _MAX_BYTES:
        return f"ERROR: 파일이 너무 큽니다 (>{_MAX_BYTES // 1024}KB) — {file_path}"
    try:
        with open(real, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.splitlines()
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        return f"# {real} ({len(lines)} lines)\n{numbered}"
    except Exception as e:
        return f"ERROR: 파일 읽기 실패 — {e}"
