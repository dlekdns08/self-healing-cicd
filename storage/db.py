"""
SQLite 기반 이벤트 / 시도 이력 저장
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "data/healing.db")


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS run_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      INTEGER NOT NULL,
            repo        TEXT NOT NULL,
            error_type  TEXT,
            error_info  TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS healing_attempts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      INTEGER NOT NULL,
            attempt     INTEGER NOT NULL,
            messages    TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        """)


def save_run_event(run_id: int, repo: str, error_info: dict) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO run_events (run_id, repo, error_type, error_info) VALUES (?,?,?,?)",
            (run_id, repo, error_info.get("type"), json.dumps(error_info)),
        )


def save_attempt(run_id: int, attempt: int, messages: list) -> None:
    # Pydantic v2는 .model_dump(), v1은 .dict() — 둘 다 fallback 처리
    def _serialize(m):
        if hasattr(m, "model_dump"):
            return m.model_dump()
        if hasattr(m, "dict"):
            return m.dict()
        return str(m)

    serialized = json.dumps([_serialize(m) for m in messages])
    with _conn() as con:
        con.execute(
            "INSERT INTO healing_attempts (run_id, attempt, messages) VALUES (?,?,?)",
            (run_id, attempt, serialized),
        )


def get_run_history(run_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM healing_attempts WHERE run_id=? ORDER BY attempt",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]
