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

    if not os.path.isfile(file_path):
        return f"ERROR: 파일이 존재하지 않습니다 — {file_path}"

    # 파일이 있는 디렉터리를 cwd로 설정 (상대경로 패치도 안전하게 처리)
    cwd = os.path.dirname(os.path.abspath(file_path))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(diff)
        patch_file = f.name

    try:
        # -p0: 명시적 file_path를 사용할 때는 경로 스트리핑 불필요
        # -i: 패치 파일 지정 (stdin 대신)
        base_cmd = ["patch", "-p0", "-i", patch_file, file_path]

        dry = subprocess.run(
            ["patch", "--dry-run", "-p0", "-i", patch_file, file_path],
            capture_output=True, text=True, cwd=cwd,
        )
        if dry.returncode != 0:
            return f"ERROR: dry-run 실패\n{dry.stderr or dry.stdout}"

        result = subprocess.run(
            base_cmd,
            capture_output=True, text=True, cwd=cwd,
        )
        if result.returncode == 0:
            return f"SUCCESS: 패치 적용 완료 — {file_path}"
        return f"ERROR: 패치 적용 실패\n{result.stderr or result.stdout}"
    finally:
        os.unlink(patch_file)