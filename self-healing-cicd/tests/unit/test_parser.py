"""
webhook/parser.py 단위 테스트
"""
import pytest
from webhook.parser import classify_error


def test_dependency_error():
    log = "Step 3/5\nError: ModuleNotFoundError: No module named 'requests'\n"
    result = classify_error(log)
    assert result["type"] == "dependency"
    assert "snippet" in result


def test_test_failure():
    log = "collected 10 items\nFAILED tests/test_auth.py::test_login - AssertionError\n"
    result = classify_error(log)
    assert result["type"] == "test_failure"


def test_unknown_error():
    log = "Some completely unknown output with no known pattern"
    result = classify_error(log)
    assert result["type"] == "unknown"
    assert result["snippet"] == log[-2000:]


def test_snippet_contains_context():
    lines = ["line " + str(i) for i in range(100)]
    lines[50] = "npm ERR! code ENOENT"
    log = "\n".join(lines)
    result = classify_error(log)
    assert result["type"] == "dependency"
    assert "line 49" in result["snippet"]
    assert "line 51" in result["snippet"]
