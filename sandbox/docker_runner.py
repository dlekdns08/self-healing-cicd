"""
Docker 샌드박스 — 격리된 컨테이너에서 명령 실행
"""
import subprocess


SANDBOX_IMAGE = "python:3.12-slim"
TIMEOUT_SECONDS = 120


def run_in_sandbox(cmd: str) -> str:
    """
    일회성 Docker 컨테이너 안에서 cmd를 실행하고 stdout/stderr를 반환합니다.
    컨테이너는 실행 후 자동 삭제됩니다(--rm).
    네트워크는 없음(--network none) — 패키지 설치 시에는 호출 전 이미지에 포함하거나
    network=bridge로 override.
    """
    docker_cmd = [
        "docker", "run", "--rm",
        "--network", "none",
        "--memory", "512m",
        "--cpus", "1",
        "--read-only",
        "--tmpfs", "/tmp",
        SANDBOX_IMAGE,
        "sh", "-c", cmd,
    ]
    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        output = result.stdout + result.stderr
        status = "SUCCESS" if result.returncode == 0 else "FAILED"
        return f"{status} (exit={result.returncode})\n{output}"
    except subprocess.TimeoutExpired:
        return f"ERROR: 명령 타임아웃 ({TIMEOUT_SECONDS}s 초과)"
    except FileNotFoundError:
        return "ERROR: Docker가 설치되어 있지 않습니다."
