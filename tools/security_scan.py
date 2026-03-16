"""
Tool: security_scan — 수정된 코드 보안 검사
apply_patch 후, git_commit_push 전에 반드시 호출합니다.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from pathlib import Path

from langchain_core.tools import tool


# 공통 보안 패턴 (Python/JS/TS 공통)
_SECRET_PATTERNS = [
    (r'(?i)(password|passwd|secret|api_key|apikey|token|private_key)\s*=\s*["\'][^"\']{6,}["\']', "하드코딩된 시크릿 값"),
    (r'(?i)-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----', "하드코딩된 개인키"),
    (r'(?i)(aws_access_key_id|aws_secret_access_key)\s*=\s*["\'][^"\']+["\']', "AWS 자격증명 하드코딩"),
]

_DANGEROUS_PATTERNS = [
    (r'\beval\s*\(', "eval() 사용 — 코드 인젝션 위험"),
    (r'\bexec\s*\(', "exec() 사용 — 코드 인젝션 위험"),
    (r'subprocess\.(call|run|Popen)\s*\([^)]*shell\s*=\s*True', "shell=True subprocess — 커맨드 인젝션 위험"),
    (r'os\.system\s*\(', "os.system() — 커맨드 인젝션 위험"),
    (r'pickle\.loads?\s*\(', "pickle 역직렬화 — 임의코드 실행 위험"),
    (r'(?i)SELECT\s+.+\s+FROM\s+.+\s+WHERE\s+.+["\'\s]\s*\+', "SQL 문자열 연결 — SQL 인젝션 위험"),
]


def _scan_python_ast(file_path: str) -> list[dict]:
    """AST로 Python 파일의 보안 이슈 탐지"""
    findings = []
    try:
        source = Path(file_path).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # assert 구문 (최적화 시 제거됨)
            if isinstance(node, ast.Assert):
                findings.append({
                    "severity": "LOW",
                    "line": node.lineno,
                    "issue": "assert 구문 — -O 플래그로 무력화될 수 있습니다.",
                })
            # __import__ 동적 임포트
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "__import__":
                    findings.append({
                        "severity": "MEDIUM",
                        "line": node.lineno,
                        "issue": "__import__() 동적 임포트 — 코드 인젝션 가능성",
                    })
    except SyntaxError:
        pass
    return findings


def _scan_patterns(file_path: str) -> list[dict]:
    """정규식 패턴으로 보안 이슈 탐지"""
    findings = []
    try:
        lines = Path(file_path).read_text().splitlines()
    except Exception:
        return findings

    for lineno, line in enumerate(lines, 1):
        for pattern, description in _SECRET_PATTERNS:
            if re.search(pattern, line):
                findings.append({"severity": "HIGH", "line": lineno, "issue": description})
        for pattern, description in _DANGEROUS_PATTERNS:
            if re.search(pattern, line):
                findings.append({"severity": "MEDIUM", "line": lineno, "issue": description})
    return findings


def _run_bandit(repo_path: str) -> list[dict]:
    """bandit으로 Python 보안 스캔"""
    bandit_bin = "/usr/local/bin/bandit"
    if not os.path.exists(bandit_bin):
        return []
    try:
        result = subprocess.run(
            [bandit_bin, "-r", repo_path, "-f", "json", "-ll"],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(result.stdout or "{}")
        findings = []
        for r in data.get("results", []):
            findings.append({
                "severity": r.get("issue_severity", "MEDIUM"),
                "line": r.get("line_number", 0),
                "file": r.get("filename", ""),
                "issue": f"[bandit] {r.get('issue_text', '')} ({r.get('test_id', '')})",
            })
        return findings
    except Exception:
        return []


def _run_npm_audit(repo_path: str) -> list[dict]:
    """npm audit으로 Node.js 의존성 취약점 스캔"""
    if not os.path.exists(os.path.join(repo_path, "package.json")):
        return []
    try:
        result = subprocess.run(
            ["npm", "audit", "--json"],
            capture_output=True, text=True, cwd=repo_path, timeout=60,
        )
        data = json.loads(result.stdout or "{}")
        findings = []
        for vuln in data.get("vulnerabilities", {}).values():
            severity = vuln.get("severity", "low").upper()
            if severity in ("HIGH", "CRITICAL"):
                findings.append({
                    "severity": severity,
                    "line": 0,
                    "file": "package.json",
                    "issue": f"[npm audit] {vuln.get('name')} — {vuln.get('title', '알 수 없는 취약점')}",
                })
        return findings
    except Exception:
        return []


@tool
def security_scan(repo_path: str) -> str:
    """
    저장소의 보안 취약점을 스캔합니다.
    apply_patch로 파일을 수정한 후, git_commit_push 전에 반드시 호출하세요.
    repo_path: 저장소 루트 절대경로 (예: /home/api)
    HIGH 이상 이슈가 발견되면 커밋하지 말고 에스컬레이션하세요.
    """
    if not os.path.isdir(repo_path):
        return f"ERROR: 경로가 존재하지 않습니다 — {repo_path}"

    all_findings: list[dict] = []

    # Python 프로젝트 스캔
    py_files = list(Path(repo_path).rglob("*.py"))
    if py_files:
        all_findings.extend(_run_bandit(repo_path))
        for f in py_files:
            fstr = str(f)
            if ".venv" in fstr or "site-packages" in fstr:
                continue
            for finding in _scan_python_ast(fstr):
                finding["file"] = fstr
                all_findings.append(finding)
            for finding in _scan_patterns(fstr):
                finding["file"] = fstr
                all_findings.append(finding)

    # Node.js 프로젝트 스캔
    ts_js_files = list(Path(repo_path).rglob("*.ts")) + list(Path(repo_path).rglob("*.js"))
    if ts_js_files:
        all_findings.extend(_run_npm_audit(repo_path))
        for f in ts_js_files:
            fstr = str(f)
            if "node_modules" in fstr:
                continue
            for finding in _scan_patterns(fstr):
                finding["file"] = fstr
                all_findings.append(finding)

    if not all_findings:
        return "SUCCESS: 보안 이슈가 발견되지 않았습니다. git_commit_push를 진행하세요."

    high = [f for f in all_findings if f["severity"] in ("HIGH", "CRITICAL")]
    medium = [f for f in all_findings if f["severity"] == "MEDIUM"]
    low = [f for f in all_findings if f["severity"] == "LOW"]

    lines = [f"보안 스캔 결과: HIGH={len(high)}, MEDIUM={len(medium)}, LOW={len(low)}"]
    for f in all_findings:
        loc = f":{f['line']}" if f.get("line") else ""
        lines.append(f"  [{f['severity']}] {f.get('file', '')}{loc} — {f['issue']}")

    if high:
        lines.append("\nHIGH 이상 이슈가 있습니다. git_commit_push를 중단하고 에스컬레이션하세요.")
        return "\n".join(lines)

    lines.append("\nSUCCESS: HIGH 이슈 없음. git_commit_push를 진행해도 됩니다.")
    return "\n".join(lines)
