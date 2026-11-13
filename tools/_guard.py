"""
보안 가드 — 에이전트 툴이 공유하는 저장소/경로 검증 + git 인증 헬퍼

LLM이 넘기는 repo / repo_path / file_path 는 신뢰할 수 없는 입력이다
(CI 로그를 통한 프롬프트 인젝션 가능). 모든 툴은 여기 함수로 검증한 뒤에만
파일시스템·git·GitHub API에 접근한다.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess

_REPO_PATH_DEFAULTS: dict[str, str] = {"api": "/app/api", "blog": "/app/blog"}

# "owner/repo" — GitHub API URL 에 그대로 삽입되므로 형식을 엄격히 제한
_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")

# 읽기·쓰기 모두 금지 (시크릿 / git 내부)
_SENSITIVE_DIRS = {".git", ".ssh", ".aws", ".gnupg"}
_SENSITIVE_NAMES = {".npmrc", ".netrc", ".pypirc", "id_rsa", "id_ed25519", "credentials"}
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3")
# 쓰기만 금지 — 워크플로우 수정은 self-hosted runner 에서 임의 코드 실행으로 이어진다
_WRITE_PROTECTED_DIRS = {".github"}


def repo_path_map() -> dict[str, str]:
    """REPO_PATH_MAP('api:/app/api,blog:/app/blog') 파싱. 미설정 시 기본값."""
    overrides: dict[str, str] = {}
    for entry in os.environ.get("REPO_PATH_MAP", "").split(","):
        if ":" in entry:
            name, path = entry.split(":", 1)
            if name.strip() and path.strip():
                overrides[name.strip().lower()] = path.strip()
    return {**_REPO_PATH_DEFAULTS, **overrides}


def allowed_roots() -> list[str]:
    return [os.path.realpath(p) for p in repo_path_map().values()]


def is_known_repo(repo: str) -> bool:
    """형식이 올바르고, 로컬 경로가 매핑된(= 우리가 관리하는) 저장소인지."""
    if not isinstance(repo, str) or not _REPO_RE.match(repo) or ".." in repo:
        return False
    allowed = [r.strip().lower() for r in os.environ.get("ALLOWED_REPOS", "").split(",") if r.strip()]
    if allowed and repo.lower() not in allowed:
        return False
    return repo.split("/")[-1].lower() in repo_path_map()


def is_valid_sha(sha: str) -> bool:
    return isinstance(sha, str) and bool(_SHA_RE.match(sha))


def resolve_repo_root(repo_path: str) -> str | None:
    """repo_path 가 허용된 저장소 루트면 실제 경로를, 아니면 None."""
    if not isinstance(repo_path, str) or not repo_path:
        return None
    real = os.path.realpath(repo_path)
    return real if real in allowed_roots() else None


def _is_sensitive(rel_parts: list[str], for_write: bool) -> bool:
    name = rel_parts[-1].lower()
    dirs = {p.lower() for p in rel_parts[:-1]}
    if dirs & _SENSITIVE_DIRS or name in _SENSITIVE_DIRS:
        return True
    if for_write and (dirs & _WRITE_PROTECTED_DIRS):
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if name in _SENSITIVE_NAMES or name.endswith(_SENSITIVE_SUFFIXES):
        return True
    return False


def resolve_repo_file(file_path: str, for_write: bool = False) -> tuple[str | None, str]:
    """
    file_path 를 검증해 (실제경로, "") 또는 (None, 에러메시지) 반환.
    - 심볼릭 링크를 해석한 실제 경로가 허용 루트 '안'에 있어야 한다
      (startswith 가 아닌 경로 구성요소 단위 비교 — /app/api-secret 같은 형제 경로 차단)
    - .env, .git, 키 파일 등 민감 파일은 거부
    """
    if not isinstance(file_path, str) or not file_path or "\x00" in file_path:
        return None, "ERROR: 잘못된 경로입니다."
    real = os.path.realpath(file_path)
    for root in allowed_roots():
        try:
            inside = os.path.commonpath([real, root]) == root and real != root
        except ValueError:
            inside = False
        if not inside:
            continue
        rel_parts = os.path.relpath(real, root).split(os.sep)
        if _is_sensitive(rel_parts, for_write):
            return None, f"ERROR: 보호된 파일입니다 — {file_path}"
        return real, ""
    return None, f"ERROR: 허용되지 않은 경로입니다. 허용 경로: {sorted(repo_path_map().values())}"


# ── git ────────────────────────────────────────────────────

def redact(text: str) -> str:
    """출력에서 토큰을 제거 — git 에러 메시지가 LLM/DB/Slack 으로 흘러가기 전에 호출."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        text = text.replace(token, "***")
    return re.sub(r"(https?://)[^/\s@]+@", r"\1", text)


def git_env() -> dict[str, str]:
    """
    GITHUB_TOKEN 을 remote URL(.git/config, 평문·영구 저장)에 넣지 않고
    이 프로세스 환경변수로만 전달한다.
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
    return env


def run_git(args: list[str], cwd: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=cwd, env=git_env(), timeout=120,
    )
    return result.returncode, redact((result.stdout + result.stderr).strip())


def scrub_remote_credentials(cwd: str) -> None:
    """이전 버전이 origin URL 에 심어둔 토큰(https://<token>@github.com/...)을 제거."""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=cwd,
    )
    url = result.stdout.strip()
    cleaned = re.sub(r"^(https?://)[^/@]+@", r"\1", url)
    if result.returncode == 0 and cleaned != url:
        subprocess.run(["git", "remote", "set-url", "origin", cleaned], capture_output=True, cwd=cwd)
