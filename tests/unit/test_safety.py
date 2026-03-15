"""
tools/run_shell.py 안전 가드레일 단위 테스트
"""
from unittest.mock import patch
from tools.run_shell import run_shell


def test_forbidden_command_blocked():
    result = run_shell.invoke({"cmd": "rm -rf /"})
    assert "ERROR" in result
    assert "금지된 명령어" in result


def test_fork_bomb_blocked():
    result = run_shell.invoke({"cmd": ":(){ :|:& };:"})
    assert "ERROR" in result


def test_allowed_command_passes_to_sandbox():
    with patch("tools.run_shell.run_in_sandbox", return_value="SUCCESS (exit=0)\n") as mock:
        result = run_shell.invoke({"cmd": "pip install requests"})
        mock.assert_called_once_with("pip install requests")
        assert "SUCCESS" in result
