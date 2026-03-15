"""
CI 로그 파서 — 에러 유형 분류 + 스니펫 추출
"""
import re

SNIPPET_WINDOW = 30  # 매칭 라인 앞뒤 몇 줄을 스니펫으로 잘라낼지

ERROR_PATTERNS: dict[str, list[str]] = {
    "dependency": [
        r"ModuleNotFoundError",
        r"npm ERR!",
        r"Cannot find module",
        r"No matching distribution found",
        r"pip.*ERROR",
    ],
    "test_failure": [
        r"FAILED tests/",
        r"AssertionError",
        r"pytest.*\d+ failed",
        r"FAIL\s",
    ],
    "lint": [
        r"flake8.*error",
        r"ESLint.*error",
        r"mypy.*error",
        r"ruff.*error",
    ],
    "build": [
        r"SyntaxError",
        r"Compilation failed",
        r"error TS\d+",
        r"exit code [^0]",
    ],
    "infra": [
        r"Connection refused",
        r"timed? ?out",
        r"ECONNRESET",
        r"dial tcp.*i/o timeout",
    ],
    "deploy": [
        r"systemctl.*failed",
        r"docker.*error",
        r"container.*exited",
        r"nginx.*\[emerg\]",
        r"Permission denied",
        r"No space left on device",
        r"port.*already in use",
        r"Health check failed",
    ],
}


def classify_error(log: str) -> dict:
    lines = log.splitlines()
    for category, patterns in ERROR_PATTERNS.items():
        for pattern in patterns:
            for i, line in enumerate(lines):
                if re.search(pattern, line, re.IGNORECASE):
                    start = max(0, i - SNIPPET_WINDOW)
                    end = min(len(lines), i + SNIPPET_WINDOW)
                    snippet = "\n".join(lines[start:end])
                    return {
                        "type": category,
                        "matched_pattern": pattern,
                        "matched_line": line.strip(),
                        "snippet": snippet,
                    }
    # 패턴 미매칭 — 마지막 2000자 전달
    return {
        "type": "unknown",
        "matched_pattern": None,
        "matched_line": None,
        "snippet": log[-2000:],
    }
