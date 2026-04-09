"""
Tool: run_shell — Docker 샌드박스 안에서 쉘 명령 실행
"""
from langchain_core.tools import tool

from config.safety import SAFETY_CONFIG
from sandbox.docker_runner import run_in_sandbox

# 이 명령어들은 인터넷 접근이 필요 → bridge 네트워크 사용
_NETWORK_REQUIRED_PATTERNS = (
    "pip install",
    "pip3 install",
    "npm install",
    "npm ci",
    "npm audit",
    "yarn add",
    "yarn install",
    "apt-get install",
    "apt install",
    "apk add",
    "poetry install",
    "poetry add",
    "uv pip install",
    "uv sync",
)


@tool
def run_shell(cmd: str) -> str:
    """
    Docker 샌드박스 안에서 쉘 명령을 실행합니다.
    패키지 설치(pip install, npm ci 등)는 자동으로 네트워크 허용 환경에서 실행됩니다.
    위험 명령(rm -rf 등)은 자동 차단됩니다.
    """
    for forbidden in SAFETY_CONFIG["forbidden_commands"]:
        if forbidden in cmd:
            return f"ERROR: 금지된 명령어 포함 — '{forbidden}'"

    # 패키지 설치 명령은 bridge 네트워크 필요 (나머지는 격리)
    needs_network = any(pattern in cmd for pattern in _NETWORK_REQUIRED_PATTERNS)
    network = "bridge" if needs_network else "none"

    return run_in_sandbox(cmd, network=network)
