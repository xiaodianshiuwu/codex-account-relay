from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from codex_relay.store import RelayPaths, Store

# Hard safety: tests must never quit/launch/open the real desktop app.
os.environ["CODEX_RELAY_TEST_MODE"] = "1"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def make_jwt(payload: Dict[str, Any]) -> str:
    header = b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = b64url(json.dumps(payload).encode())
    return f"{header}.{body}.signature"


def make_auth(
    account_id: str = "acct-aaaa",
    email: str = "a@example.com",
    *,
    plan: str = "plus",
    user_id: str = "user-aaaa",
    refresh_token: str = "refresh-aaaa",
    exp_offset: int = 3600,
) -> Dict[str, Any]:
    now = int(time.time())
    auth_claims = {
        "chatgpt_account_id": account_id,
        "chatgpt_user_id": user_id,
        "chatgpt_plan_type": plan,
    }
    access = make_jwt(
        {
            "exp": now + exp_offset,
            "iat": now,
            "https://api.openai.com/auth": auth_claims,
            "https://api.openai.com/profile": {"email": email, "name": "Test User"},
        }
    )
    id_token = make_jwt(
        {
            "exp": now + exp_offset,
            "email": email,
            "https://api.openai.com/auth": auth_claims,
            "https://api.openai.com/profile": {"email": email, "name": "Test User"},
        }
    )
    return {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "access_token": access,
            "id_token": id_token,
            "refresh_token": refresh_token,
            "account_id": account_id,
        },
        "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def make_paths(root: Path) -> RelayPaths:
    return RelayPaths(relay_home=root / "relay", codex_home=root / "codex")


def new_store() -> Store:
    root = Path(tempfile.mkdtemp(prefix="relay-test-"))
    paths = make_paths(root)
    paths.codex_home.mkdir(parents=True, exist_ok=True)
    store = Store(paths)
    store.ensure_layout()
    return store


def write_state_db(paths: RelayPaths, threads: list) -> None:
    path = paths.state_db
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                git_branch TEXT,
                creator_user_id TEXT,
                creator_account_id TEXT,
                model TEXT,
                reasoning_effort TEXT,
                name TEXT,
                preview TEXT DEFAULT ''
            )
            """
        )
        for thread in threads:
            connection.execute(
                "INSERT INTO threads (id, rollout_path, created_at, updated_at, source, model_provider, cwd, title, tokens_used, archived, creator_user_id, creator_account_id, model, reasoning_effort, name, preview) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread["id"],
                    thread["rollout_path"],
                    thread.get("created_at", 1),
                    thread.get("updated_at", int(time.time())),
                    thread.get("source", "vscode"),
                    thread.get("model_provider", "openai"),
                    thread.get("cwd", "/tmp/project"),
                    thread.get("title", "Test thread"),
                    thread.get("tokens_used", 1234),
                    thread.get("archived", 0),
                    thread.get("creator_user_id", "user-a"),
                    thread.get("creator_account_id", "acct-a"),
                    thread.get("model", "gpt-test"),
                    thread.get("reasoning_effort", "high"),
                    thread.get("name", None),
                    thread.get("preview", "preview"),
                ),
            )
        connection.commit()
    finally:
        connection.close()


def write_active_turn(paths: RelayPaths, thread_id: str, turn_id: str) -> None:
    connection = sqlite3.connect(paths.thread_history_db)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS thread_turns ("
            "thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, status TEXT NOT NULL, "
            "started_at INTEGER, completed_at INTEGER, PRIMARY KEY(thread_id, turn_id))"
        )
        connection.execute(
            "INSERT OR REPLACE INTO thread_turns (thread_id, turn_id, status, started_at, completed_at) "
            "VALUES (?, ?, 'inProgress', ?, NULL)",
            (thread_id, turn_id, int(time.time() * 1000)),
        )
        connection.commit()
    finally:
        connection.close()


def write_rollout(path: Path, *, account_id: str = "acct-a") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "type": "session_meta",
            "timestamp": "2026-01-01T00:00:00Z",
            "payload": {
                "id": path.stem,
                "session_id": path.stem,
                "timestamp": "2026-01-01T00:00:00Z",
                "creator_account_id": account_id,
                "creator_user_id": "user-a",
                "model_provider": "openai",
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-01-01T00:00:01Z",
            "payload": {
                "type": "reasoning",
                "id": "rs_1",
                "encrypted_content": "A" * 1000,
                "summary": [],
            },
        },
        {
            "type": "token_usage_record",
            "timestamp": "2026-01-01T00:00:02Z",
            "payload": {
                "turn_id": "turn-1",
                "usage": {
                    "input_tokens": 10000,
                    "cached_input_tokens": 9000,
                    "output_tokens": 100,
                    "reasoning_output_tokens": 20,
                },
            },
        },
        {
            "type": "compacted",
            "timestamp": "2026-01-01T00:00:03Z",
            "payload": {
                "window_number": 1,
                "message": "summary",
                "replacement_history": [
                    {"type": "message", "role": "user", "content": []},
                    {"type": "compaction", "encrypted_content": "summary"},
                ],
            },
        },
        {
            "type": "token_usage_record",
            "timestamp": "2026-01-01T01:00:00Z",
            "payload": {
                "turn_id": "turn-2",
                "usage": {
                    "input_tokens": 20000,
                    "cached_input_tokens": 0,
                    "output_tokens": 200,
                    "reasoning_output_tokens": 30,
                },
            },
        },
        {
            "type": "token_usage_record",
            "timestamp": "2026-01-01T01:00:10Z",
            "payload": {
                "turn_id": "turn-2",
                "usage": {
                    "input_tokens": 22000,
                    "cached_input_tokens": 21000,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 5,
                },
            },
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
