"""
CI 로그 파서 — 에러 유형 분류 + 스니펫 추출
"""
import re

SNIPPET_WINDOW = 30  # 매칭 라인 앞뒤 몇 줄을 스니펫으로 잘라낼지

# 높은 인덱스일수록 낮은 우선순위 (구체적인 에러 유형을 먼저 처리)
CATEGORY_PRIORITY: list[str] = [
    "build",
    "dependency",
    "test_failure",
    "lint",
    "runtime",
    "database",
    "config",
    "deploy",
    "infra",
]

ERROR_PATTERNS: dict[str, list[str]] = {
    "dependency": [
        # Python
        r"ModuleNotFoundError",
        r"ImportError",
        r"No matching distribution found",
        r"pip.*ERROR",
        r"PackageNotFoundError",
        r"RequirementsFileParseError",
        # Node.js / npm / yarn / pnpm
        r"npm ERR!",
        r"Cannot find module",
        r"yarn error",
        r"pnpm ERR",
        r"Module not found",
        r"ERR_MODULE_NOT_FOUND",
        # Go / Rust / Java
        r"cannot find package",
        r"go: .*: no required module",
        r"error\[E\d+\].*unresolved import",
        r"package .* is not in GOROOT",
        r"cannot load.*no Go files",
    ],
    "test_failure": [
        r"FAILED tests/",
        r"AssertionError",
        r"pytest.*\d+ failed",
        r"FAIL\s",
        r"\d+ tests? failed",
        r"test.*FAILED",
        r"FAILURES",
        r"expected.*received",
        r"jest.*fail",
        r"vitest.*fail",
        r"● .*",                      # Jest 에러 블록
        r"not ok \d+",                # TAP 형식
        r"Error: expect\(",
    ],
    "lint": [
        r"flake8.*error",
        r"ESLint.*error",
        r"mypy.*error",
        r"ruff.*error",
        r"pylint.*error",
        r"prettier.*error",
        r"tslint.*error",
        r"biome.*error",
        r"\d+ error.*\d+ warning",    # ESLint 요약
        r"type error",
        r"Type '.*' is not assignable",
    ],
    "build": [
        r"SyntaxError",
        r"IndentationError",
        r"TabError",
        r"'.' was never closed",
        r"invalid syntax",
        r"unexpected EOF",
        r"unexpected indent",
        r"Compilation failed",
        r"error TS\d+",
        r"exit code [^0]",
        r"Build failed",
        r"build error",
        r"Failed to compile",
        r"ELIFECYCLE",
        r"next build.*error",
        r"webpack.*error",
        r"vite.*error",
        r"rollup.*error",
        r"make.*Error \d+",
        r"cmake.*error",
        r"ninja.*failed",
        r"error: ld returned",
        r"undefined reference to",
    ],
    "runtime": [
        r"Traceback \(most recent call last\)",
        r"NameError",
        r"AttributeError",
        r"TypeError",
        r"ValueError",
        r"KeyError",
        r"IndexError",
        r"UnboundLocalError",
        r"RecursionError",
        r"ZeroDivisionError",
        r"RuntimeError",
        r"NotImplementedError",
        r"raise .*Error",
        r"Exception: ",
    ],
    "infra": [
        r"Connection refused",
        r"timed? ?out",
        r"ECONNRESET",
        r"dial tcp.*i/o timeout",
        r"ENOTFOUND",
        r"EHOSTUNREACH",
        r"network.*unreachable",
        r"Name or service not known",
        r"getaddrinfo.*failed",
        r"SSL.*error",
        r"certificate.*expired",
        r"TLS handshake",
        r"502 Bad Gateway",
        r"503 Service Unavailable",
        r"curl.*\(6\)",              # curl: (6) Could not resolve host
        r"curl.*\(7\)",              # curl: (7) Failed to connect
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
        r"address already in use",
        r"bind: address already in use",
        r"OOMKilled",
        r"Out of memory",
        r"CrashLoopBackOff",
        r"ImagePullBackOff",
        r"ErrImagePull",
        r"kubectl.*error",
        r"helm.*failed",
        r"deployment.*failed",
        r"rollout.*failed",
        r"docker: Error response",
        r"cannot connect to the Docker daemon",
        r"failed to push",
        r"denied: access forbidden",
        r"unauthorized: authentication required",
    ],
    "config": [
        r"KeyError",
        r"ValueError.*env",
        r"Missing required environment variable",
        r"\.env.*not found",
        r"config.*not found",
        r"invalid.*configuration",
        r"YAML.*error",
        r"json.*parse.*error",
        r"toml.*error",
    ],
    "database": [
        r"OperationalError",
        r"ProgrammingError",
        r"IntegrityError",
        r"could not connect to server",
        r"FATAL.*database",
        r"migration.*failed",
        r"alembic.*error",
        r"prisma.*error",
        r"Connection to .* failed",
        r"too many connections",
        r"deadlock detected",
    ],
}


def classify_error(log: str) -> dict:
    """
    로그에서 모든 에러 유형을 매칭한 뒤 우선순위에 따라 정렬합니다.
    - type: 가장 높은 우선순위의 에러 유형 (기본 처리 대상)
    - secondary_types: 함께 감지된 추가 에러 유형 목록
    """
    lines = log.splitlines()
    all_matches: dict[str, dict] = {}

    for category, patterns in ERROR_PATTERNS.items():
        for pattern in patterns:
            for i, line in enumerate(lines):
                if re.search(pattern, line, re.IGNORECASE):
                    if category not in all_matches:  # 카테고리당 첫 매칭만 보존
                        start = max(0, i - SNIPPET_WINDOW)
                        end = min(len(lines), i + SNIPPET_WINDOW)
                        all_matches[category] = {
                            "matched_pattern": pattern,
                            "matched_line": line.strip(),
                            "snippet": "\n".join(lines[start:end]),
                        }
                    break  # 이미 매칭됐으면 같은 카테고리 내 다음 패턴 불필요

    if not all_matches:
        return {
            "type": "unknown",
            "secondary_types": [],
            "matched_pattern": None,
            "matched_line": None,
            "snippet": log[-2000:],
        }

    # 우선순위 순으로 정렬 (CATEGORY_PRIORITY에 없는 카테고리는 맨 뒤)
    ordered = sorted(
        all_matches.items(),
        key=lambda kv: (
            CATEGORY_PRIORITY.index(kv[0])
            if kv[0] in CATEGORY_PRIORITY
            else len(CATEGORY_PRIORITY)
        ),
    )
    primary_cat, primary_info = ordered[0]
    secondary = [cat for cat, _ in ordered[1:]]

    return {
        "type": primary_cat,
        "secondary_types": secondary,
        **primary_info,
    }
