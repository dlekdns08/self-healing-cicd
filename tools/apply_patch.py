"""
Tool: apply_patch — unified diff를 파일에 적용
"""
import json
import os
import subprocess
import tempfile

from langchain_core.tools import tool

from config.safety import SAFETY_CONFIG
from tools._guard import resolve_repo_file


def _check_target(file_path: str) -> tuple[str | None, str]:
    """확장자 + 저장소 경로 검증. (실제경로, "") 또는 (None, 에러메시지)."""
    if not isinstance(file_path, str) or not file_path:
        return None, "ERROR: file_path가 비어 있습니다"
    ext = os.path.splitext(file_path)[1]
    basename = os.path.basename(file_path)
    if (ext not in SAFETY_CONFIG["allowed_file_extensions"]
            and basename not in SAFETY_CONFIG.get("allowed_file_names", [])):
        return None, f"ERROR: 허용되지 않은 파일 — {basename} (확장자: {ext or '없음'})"

    # 등록된 저장소 내부 + 민감/보호 파일(.env, .git, .github/ 등) 제외
    real, err = resolve_repo_file(file_path, for_write=True)
    if real is None:
        return None, err
    if not os.path.isfile(real):
        return None, f"ERROR: 파일이 존재하지 않습니다 — {file_path}"
    return real, ""


def _patch(diff: str, real_path: str, dry_run: bool) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(diff)
        patch_file = f.name
    try:
        # -p0 + 명시적 대상 파일: diff 헤더의 경로는 무시되고 real_path 에만 적용된다
        cmd = ["patch", "-p0", "-i", patch_file, real_path]
        if dry_run:
            cmd.insert(1, "--dry-run")
        return subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=os.path.dirname(real_path), timeout=60,
        )
    finally:
        os.unlink(patch_file)


@tool
def apply_patch(diff: str, file_path: str) -> str:
    """
    unified diff 형식의 패치를 지정 파일에 적용합니다.
    LLM이 생성한 코드 수정을 실제 파일에 반영할 때 사용하세요.
    허용된 확장자(.py, .ts, .json 등)의, 등록된 저장소 내부 파일만 수정 가능합니다.
    """
    real, err = _check_target(file_path)
    if real is None:
        return err

    dry = _patch(diff, real, dry_run=True)
    if dry.returncode != 0:
        return f"ERROR: dry-run 실패\n{dry.stderr or dry.stdout}"

    result = _patch(diff, real, dry_run=False)
    if result.returncode == 0:
        return f"SUCCESS: 패치 적용 완료 — {real}"
    return f"ERROR: 패치 적용 실패\n{result.stderr or result.stdout}"


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

    # 1단계: 전체 검증 + dry-run — 하나라도 실패하면 전체 중단
    targets: list[tuple[str, str]] = []
    dry_errors = []
    for item in patches:
        if not isinstance(item, dict) or not isinstance(item.get("diff"), str):
            dry_errors.append("각 항목은 {diff, file_path} 객체여야 합니다")
            continue
        real, err = _check_target(item.get("file_path", ""))
        if real is None:
            dry_errors.append(err)
            continue
        dry = _patch(item["diff"], real, dry_run=True)
        if dry.returncode != 0:
            dry_errors.append(f"dry-run 실패 [{real}]: {dry.stderr or dry.stdout}")
            continue
        targets.append((item["diff"], real))

    if dry_errors:
        return (
            "ERROR: dry-run 단계에서 실패 — 아무 파일도 수정되지 않았습니다\n"
            + "\n".join(dry_errors)
        )

    # 2단계: 실제 패치 적용
    results = []
    for diff, real in targets:
        result = _patch(diff, real, dry_run=False)
        if result.returncode == 0:
            results.append(f"SUCCESS: 패치 적용 완료 — {real}")
        else:
            results.append(f"ERROR: {real} — {result.stderr or result.stdout}")

    return "\n".join(results)
