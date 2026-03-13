"""
Tool: apply_patch — unified diff를 파일에 적용
"""
import os
import subprocess
import tempfile

from langchain_core.tools import tool

from config.safety import SAFETY_CONFIG


@tool
def apply_patch(diff: str, file_path: str) -> str:
    """
    unified diff 형식의 패치를 지정 파일에 적용합니다.
    LLM이 생성한 코드 수정을 실제 파일에 반영할 때 사용하세요.
    허용된 확장자(.py, .ts, .json 등)만 수정 가능합니다.
    """
    ext = os.path.splitext(file_path)[1]
    if ext not in SAFETY_CONFIG["allowed_file_extensions"]:
        return f"ERROR: 허용되지 않은 파일 확장자 — {ext}"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(diff)
        patch_file = f.name

    try:
        result = subprocess.run(
            ["patch", "--dry-run", "-p1", file_path, patch_file],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return f"ERROR: dry-run 실패\n{result.stderr}"

        result = subprocess.run(
            ["patch", "-p1", file_path, patch_file],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return f"SUCCESS: 패치 적용 완료 — {file_path}"
        return f"ERROR: 패치 적용 실패\n{result.stderr}"
    finally:
        os.unlink(patch_file)
