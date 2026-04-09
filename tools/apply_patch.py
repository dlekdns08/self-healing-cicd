"""
Tool: apply_patch — unified diff를 파일에 적용
"""
import json
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
    basename = os.path.basename(file_path)
    if (ext not in SAFETY_CONFIG["allowed_file_extensions"]
            and basename not in SAFETY_CONFIG.get("allowed_file_names", [])):
        return f"ERROR: 허용되지 않은 파일 — {basename} (확장자: {ext or '없음'})"

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


def _apply_one(diff: str, file_path: str) -> tuple[bool, str]:
    """단일 패치를 적용하고 (성공여부, 메시지)를 반환하는 내부 헬퍼."""
    ext = os.path.splitext(file_path)[1]
    basename = os.path.basename(file_path)
    if (ext not in SAFETY_CONFIG["allowed_file_extensions"]
            and basename not in SAFETY_CONFIG.get("allowed_file_names", [])):
        return False, f"허용되지 않은 파일: {file_path} (확장자: {ext or '없음'})"
    if not os.path.isfile(file_path):
        return False, f"파일이 존재하지 않습니다: {file_path}"
    cwd = os.path.dirname(os.path.abspath(file_path))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(diff)
        patch_file = f.name
    try:
        result = subprocess.run(
            ["patch", "-p0", "-i", patch_file, file_path],
            capture_output=True, text=True, cwd=cwd,
        )
        if result.returncode == 0:
            return True, f"SUCCESS: 패치 적용 완료 — {file_path}"
        return False, f"ERROR: {file_path} — {result.stderr or result.stdout}"
    finally:
        os.unlink(patch_file)


@tool
def apply_patches_batch(patches_json: str) -> str:
    """
    여러 파일에 unified diff 패치를 원자적으로 적용합니다.
    단일 에러가 여러 파일 수정을 요구할 때 사용하세요 (예: 소스 + requirements.txt 동시 수정).
    dry-run으로 전체를 먼저 검사하고, 하나라도 실패하면 아무 파일도 수정하지 않습니다.
    patches_json 형식: '[{"diff": "<unified diff>", "file_path": "/abs/path/file.py"}, ...]'
    """
    try:
        patches = json.loads(patches_json)
    except json.JSONDecodeError as e:
        return f"ERROR: patches_json 파싱 실패 — {e}"

    if not isinstance(patches, list) or not patches:
        return "ERROR: patches_json은 비어 있지 않은 배열이어야 합니다"

    # 1단계: 전체 dry-run — 하나라도 실패하면 전체 중단
    dry_errors = []
    for item in patches:
        diff = item.get("diff", "")
        file_path = item.get("file_path", "")
        ext = os.path.splitext(file_path)[1]
        basename = os.path.basename(file_path)
        if (ext not in SAFETY_CONFIG["allowed_file_extensions"]
                and basename not in SAFETY_CONFIG.get("allowed_file_names", [])):
            dry_errors.append(f"허용되지 않은 파일: {file_path} (확장자: {ext or '없음'})")
            continue
        if not os.path.isfile(file_path):
            dry_errors.append(f"파일이 존재하지 않습니다: {file_path}")
            continue
        cwd = os.path.dirname(os.path.abspath(file_path))
        with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
            f.write(diff)
            patch_file = f.name
        try:
            dry = subprocess.run(
                ["patch", "--dry-run", "-p0", "-i", patch_file, file_path],
                capture_output=True, text=True, cwd=cwd,
            )
            if dry.returncode != 0:
                dry_errors.append(f"dry-run 실패 [{file_path}]: {dry.stderr or dry.stdout}")
        finally:
            os.unlink(patch_file)

    if dry_errors:
        return (
            "ERROR: dry-run 단계에서 실패 — 아무 파일도 수정되지 않았습니다\n"
            + "\n".join(dry_errors)
        )

    # 2단계: 실제 패치 적용
    results = []
    for item in patches:
        ok, msg = _apply_one(item["diff"], item["file_path"])
        results.append(msg)

    return "\n".join(results)