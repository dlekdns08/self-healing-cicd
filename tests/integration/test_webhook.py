"""
FastAPI webhook 엔드포인트 통합 테스트
unittest 수준 — 실제 GitHub API는 mock 처리
"""
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import os
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from webhook.server import app

client = TestClient(app)


def _make_sig(body: bytes, secret: str = "test-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


SAMPLE_PAYLOAD = {
    "workflow_run": {"id": 12345, "conclusion": "failure"},
    "repository": {"full_name": "org/repo"},
}


@pytest.fixture(autouse=True)
def mock_external():
    with (
        patch("webhook.server.fetch_workflow_logs", new_callable=AsyncMock, return_value="npm ERR! code ENOENT"),
        patch("webhook.server.save_run_event"),
        patch("webhook.server.run_healing_agent", new_callable=AsyncMock),
    ):
        yield


def test_webhook_accepts_failure_event():
    body = json.dumps(SAMPLE_PAYLOAD).encode()
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _make_sig(body),
            "X-GitHub-Event": "workflow_run",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_webhook_ignores_non_failure():
    payload = {**SAMPLE_PAYLOAD, "workflow_run": {"id": 1, "conclusion": "success"}}
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _make_sig(body),
            "X-GitHub-Event": "workflow_run",
            "Content-Type": "application/json",
        },
    )
    assert resp.json()["status"] == "ignored"


def test_webhook_rejects_invalid_signature():
    body = json.dumps(SAMPLE_PAYLOAD).encode()
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=invalidsig",
            "X-GitHub-Event": "workflow_run",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
