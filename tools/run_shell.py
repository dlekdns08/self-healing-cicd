"""
Tool: run_shell — Docker 샌드박스 안에서 쉘 명령 실행
"""
import subprocess

from langchain_core.tools import tool

from config.safety import SAFETY_CONFIG
from sandbox.docker_runner import run_in_sandbox


@tool
def run_shell(cmd: str) -> str:
    """
    Docker 샌드박스 안에서 쉘 명령을 실행합니다.
    패키지 설치(pip install, npm ci), 린트 자동수정 등에 사용하세요.
    위험 명령(rm -rf 등)은 자동 차단됩니다.
    """
    for forbidden in SAFETY_CONFIG["forbidden_commands"]:
        if forbidden in cmd:
            return f"ERROR: 금지된 명령어 포함 — '{forbidden}'"

    return run_in_sandbox(cmd)
