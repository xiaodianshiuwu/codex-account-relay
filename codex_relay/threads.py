from __future__ import annotations

import collections
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from .store import RelayPaths
from .util import RelayError, human_bytes, local_time, parse_epoch_maybe_ms, short_text, sqlite_uri


@dataclass
class ThreadInfo:
    id: str
    rollout_path: str
    created_at: Optional[int]
    updated_at: Optional[int]
    cwd: str
    title: str
    preview: str
    source: str
    model: Optional[str]
    tokens_used: int
    archived: bool
    git_branch: Optional[str]
    creator_account_id: Optional[str]
    creator_user_id: Optional[str]
    reasoning_effort: Optional[str]
    name: Optional[str]

    @property
    def display_title(self) -> str:
        return self.name or self.title or self.preview or "(未命名会话)"

    @property
    def updated_epoch(self) -> Optional[int]:
        return parse_epoch_maybe_ms(self.updated_at)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "rollout_path": self.rollout_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "updated_local": local_time(self.updated_epoch),
            "cwd": self.cwd,
            "title": self.display_title,
            "source": self.source,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "tokens_used": self.tokens_used,
            "archived": self.archived,
            "git_branch": self.git_branch,
            "creator_account_id": self.creator_account_id,
            "creator_user_id": self.creator_user_id,
        }


@dataclass
class TurnUsage:
    turn_id: str
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    requests: int = 0
    first_input: int = 0
    first_cached: int = 0
    last_input: int = 0
    last_cached: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_total: int = 0
    input_total: int = 0

    @property
    def first_cache_ratio(self) -> float:
        return (100.0 * self.first_cached / self.first_input) if self.first_input else 0.0

    @property
    def last_cache_ratio(self) -> float:
        return (100.0 * self.last_cached / self.last_input) if self.last_input else 0.0

    @property
    def total_cache_ratio(self) -> float:
        return (100.0 * self.cached_total / self.input_total) if self.input_total else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "first_timestamp": self.first_ts,
            "last_timestamp": self.last_ts,
            "requests": self.requests,
            "first_input_tokens": self.first_input,
            "first_cached_tokens": self.first_cached,
            "first_cache_ratio": round(self.first_cache_ratio, 2),
            "last_input_tokens": self.last_input,
            "last_cached_tokens": self.last_cached,
            "last_cache_ratio": round(self.last_cache_ratio, 2),
            "input_tokens_total": self.input_total,
            "cached_tokens_total": self.cached_total,
            "total_cache_ratio": round(self.total_cache_ratio, 2),
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_tokens,
        }


@dataclass
class CompactionInfo:
    timestamp: Optional[str]
    window_number: Optional[int]
    item_count: int
    item_types: Dict[str, int]
    serialized_bytes: int
    has_encrypted_reasoning: bool
    has_compaction_item: bool
    summary_chars: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "window_number": self.window_number,
            "item_count": self.item_count,
            "item_types": self.item_types,
            "serialized_bytes": self.serialized_bytes,
            "has_encrypted_reasoning": self.has_encrypted_reasoning,
            "has_compaction_item": self.has_compaction_item,
            "summary_chars": self.summary_chars,
        }


@dataclass
class RolloutMetrics:
    path: str
    size_bytes: int
    session_id: Optional[str] = None
    creator_account_id: Optional[str] = None
    creator_user_id: Optional[str] = None
    created_at: Optional[str] = None
    model_provider: Optional[str] = None
    record_types: Dict[str, int] = field(default_factory=dict)
    response_item_types: Dict[str, int] = field(default_factory=dict)
    reasoning_items: int = 0
    reasoning_encrypted_items: int = 0
    reasoning_encrypted_bytes: int = 0
    compactions: List[CompactionInfo] = field(default_factory=list)
    turns: List[TurnUsage] = field(default_factory=list)
    malformed_lines: int = 0

    @property
    def latest_turn(self) -> Optional[TurnUsage]:
        return self.turns[-1] if self.turns else None

    def totals(self) -> Dict[str, Any]:
        input_tokens = sum(turn.input_total for turn in self.turns)
        cached_tokens = sum(turn.cached_total for turn in self.turns)
        output_tokens = sum(turn.output_tokens for turn in self.turns)
        reasoning_tokens = sum(turn.reasoning_tokens for turn in self.turns)
        requests = sum(turn.requests for turn in self.turns)
        return {
            "turns": len(self.turns),
            "requests": requests,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": reasoning_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cache_ratio": round(100.0 * cached_tokens / input_tokens, 2) if input_tokens else 0.0,
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "size_human": human_bytes(self.size_bytes),
            "session_id": self.session_id,
            "creator_account_id": self.creator_account_id,
            "creator_user_id": self.creator_user_id,
            "created_at": self.created_at,
            "model_provider": self.model_provider,
            "record_types": self.record_types,
            "response_item_types": self.response_item_types,
            "reasoning_items": self.reasoning_items,
            "reasoning_encrypted_items": self.reasoning_encrypted_items,
            "reasoning_encrypted_bytes": self.reasoning_encrypted_bytes,
            "compactions": [c.as_dict() for c in self.compactions],
            "turns": [t.as_dict() for t in self.turns],
            "totals": self.totals(),
            "malformed_lines": self.malformed_lines,
        }


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise RelayError(f"数据库不存在：{path}")
    uri = sqlite_uri(path)
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> set:
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.DatabaseError:
        return set()
    return {str(row[1]) for row in rows}


def list_threads(
    paths: RelayPaths,
    *,
    limit: int = 20,
    cwd: Optional[str] = None,
    account_id: Optional[str] = None,
    search: Optional[str] = None,
    include_archived: bool = False,
) -> List[ThreadInfo]:
    connection = _connect_readonly(paths.state_db)
    try:
        columns = _table_columns(connection, "threads")
        if not columns:
            raise RelayError("state 数据库缺少 threads 表")
        select = [
            "id",
            "rollout_path",
            "created_at",
            "updated_at",
            "cwd",
            "title",
            "preview" if "preview" in columns else "'' AS preview",
            "source",
            "model" if "model" in columns else "NULL AS model",
            "tokens_used",
            "archived",
            "git_branch" if "git_branch" in columns else "NULL AS git_branch",
            "creator_account_id" if "creator_account_id" in columns else "NULL AS creator_account_id",
            "creator_user_id" if "creator_user_id" in columns else "NULL AS creator_user_id",
            "reasoning_effort" if "reasoning_effort" in columns else "NULL AS reasoning_effort",
            "name" if "name" in columns else "NULL AS name",
        ]
        sql = f"SELECT {', '.join(select)} FROM threads WHERE 1=1"
        params: List[Any] = []
        if not include_archived:
            sql += " AND archived = 0"
        if cwd:
            sql += " AND cwd = ?"
            params.append(cwd)
        if account_id:
            sql += " AND creator_account_id = ?"
            params.append(account_id)
        if search:
            searchable = [name for name in ("title", "preview", "name") if name in columns]
            sql += " AND (" + " OR ".join(name + " LIKE ?" for name in searchable) + ")"
            params.extend([f"%{search}%"] * len(searchable))
        sql += " ORDER BY updated_at DESC"
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        rows = connection.execute(sql, params).fetchall()
        return [
            ThreadInfo(
                id=str(row["id"]),
                rollout_path=str(row["rollout_path"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                cwd=str(row["cwd"] or ""),
                title=str(row["title"] or ""),
                preview=str(row["preview"] or ""),
                source=str(row["source"] or ""),
                model=row["model"],
                tokens_used=int(row["tokens_used"] or 0),
                archived=bool(row["archived"]),
                git_branch=row["git_branch"],
                creator_account_id=row["creator_account_id"],
                creator_user_id=row["creator_user_id"],
                reasoning_effort=row["reasoning_effort"],
                name=row["name"],
            )
            for row in rows
        ]
    finally:
        connection.close()


def get_thread(paths: RelayPaths, thread_id: str) -> Optional[ThreadInfo]:
    connection = _connect_readonly(paths.state_db)
    try:
        columns = _table_columns(connection, "threads")
        if not columns:
            return None
        row = connection.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if row is None:
            return None
        keys = set(row.keys())
        return ThreadInfo(
            id=str(row["id"]),
            rollout_path=str(row["rollout_path"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            cwd=str(row["cwd"] or ""),
            title=str(row["title"] or ""),
            preview=str(row["preview"] or "") if "preview" in keys else "",
            source=str(row["source"] or ""),
            model=row["model"] if "model" in keys else None,
            tokens_used=int(row["tokens_used"] or 0),
            archived=bool(row["archived"]),
            git_branch=row["git_branch"] if "git_branch" in keys else None,
            creator_account_id=row["creator_account_id"] if "creator_account_id" in keys else None,
            creator_user_id=row["creator_user_id"] if "creator_user_id" in keys else None,
            reasoning_effort=row["reasoning_effort"] if "reasoning_effort" in keys else None,
            name=row["name"] if "name" in keys else None,
        )
    finally:
        connection.close()


def most_recent_thread(paths: RelayPaths, cwd: Optional[str] = None) -> Optional[ThreadInfo]:
    threads = list_threads(paths, limit=1, cwd=cwd, include_archived=False)
    if threads:
        return threads[0]
    if cwd:
        threads = list_threads(paths, limit=1, include_archived=False)
        if threads:
            return threads[0]
    return None


def active_turn_status(paths: RelayPaths) -> List[Dict[str, Any]]:
    if not paths.thread_history_db.exists():
        return []
    connection = _connect_readonly(paths.thread_history_db)
    try:
        columns = _table_columns(connection, "thread_turns")
        if not columns:
            return []
        rows = connection.execute(
            "SELECT thread_id, turn_id, status, started_at, completed_at "
            "FROM thread_turns WHERE status IN ('inProgress', 'in_progress', 'running') "
            "ORDER BY started_at DESC"
        ).fetchall()
        result = []
        for row in rows:
            started = parse_epoch_maybe_ms(row["started_at"])
            result.append(
                {
                    "thread_id": row["thread_id"],
                    "turn_id": row["turn_id"],
                    "status": row["status"],
                    "started_at": started,
                    "started_local": local_time(started),
                    "completed_at": row["completed_at"],
                }
            )
        return result
    finally:
        connection.close()


def rollout_path_for(paths: RelayPaths, thread: ThreadInfo) -> Path:
    path = Path(thread.rollout_path)
    if not path.is_absolute():
        path = paths.codex_home / path
    return path


def rollout_activity(path: Path, tail_lines: int = 500) -> Dict[str, Any]:
    """Inspect the rollout tail for an unmatched tool call.

    A long-running shell tool may not append anything for minutes while the
    turn is definitely still active. A trailing call without its output is a
    stronger signal than mtime alone.
    """
    if not path.exists() or path.suffix == ".zst":
        return {"available": False, "pending_tool_calls": 0, "last_type": None}
    tail: Deque[str] = collections.deque(maxlen=tail_lines)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            tail.append(line)
    pending: Dict[str, str] = {}
    last_type = None
    last_timestamp = None
    for line in tail:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        last_type = record.get("type")
        last_timestamp = record.get("timestamp")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if record.get("type") == "response_item":
            item_type = payload.get("type")
            call_id = payload.get("call_id") or payload.get("id")
            if item_type in ("custom_tool_call", "function_call", "local_shell_call") and call_id:
                pending[str(call_id)] = str(item_type)
            elif item_type in (
                "custom_tool_call_output",
                "function_call_output",
                "local_shell_call_output",
            ):
                if call_id:
                    pending.pop(str(call_id), None)
        elif record.get("type") == "event_msg":
            event_type = payload.get("type")
            if event_type in ("turn_complete", "task_complete", "turn_aborted"):
                pending.clear()
    return {
        "available": True,
        "pending_tool_calls": len(pending),
        "pending_types": sorted(set(pending.values())),
        "last_type": last_type,
        "last_timestamp": last_timestamp,
    }


def _read_lines(path: Path) -> Iterable[str]:
    if path.suffix == ".zst":
        raise RelayError(
            f"该会话已压缩为 .zst，当前只读分析器未内置 zstd 解压：{path.name}。"
        )
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            yield line


def rollout_metrics(path: Path, *, max_turns: Optional[int] = None) -> RolloutMetrics:
    if not path.exists():
        raise RelayError(f"rollout 文件不存在：{path}")
    metrics = RolloutMetrics(path=str(path), size_bytes=path.stat().st_size)
    turns: Dict[str, TurnUsage] = {}
    turn_order: List[str] = []
    for line in _read_lines(path):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            metrics.malformed_lines += 1
            continue
        if not isinstance(record, dict):
            metrics.malformed_lines += 1
            continue
        rtype = str(record.get("type") or "")
        metrics.record_types[rtype] = metrics.record_types.get(rtype, 0) + 1
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if rtype == "session_meta":
            metrics.session_id = payload.get("id") or payload.get("session_id")
            metrics.creator_account_id = payload.get("creator_account_id")
            metrics.creator_user_id = payload.get("creator_user_id")
            metrics.created_at = payload.get("timestamp")
            metrics.model_provider = payload.get("model_provider")
        elif rtype == "response_item":
            item_type = str(payload.get("type") or "")
            metrics.response_item_types[item_type] = metrics.response_item_types.get(item_type, 0) + 1
            if item_type == "reasoning":
                metrics.reasoning_items += 1
                encrypted = payload.get("encrypted_content")
                if isinstance(encrypted, str) and encrypted:
                    metrics.reasoning_encrypted_items += 1
                    metrics.reasoning_encrypted_bytes += len(encrypted)
        elif rtype == "compacted":
            history = payload.get("replacement_history")
            if not isinstance(history, list):
                history = []
            item_types: Dict[str, int] = {}
            has_reasoning = False
            has_compaction = False
            for item in history:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "unknown")
                item_types[item_type] = item_types.get(item_type, 0) + 1
                if item_type == "reasoning" and item.get("encrypted_content"):
                    has_reasoning = True
                if item_type in ("compaction", "context_compaction"):
                    has_compaction = True
            summary = payload.get("message")
            metrics.compactions.append(
                CompactionInfo(
                    timestamp=record.get("timestamp"),
                    window_number=payload.get("window_number"),
                    item_count=len(history),
                    item_types=item_types,
                    serialized_bytes=len(json.dumps(history, ensure_ascii=False)),
                    has_encrypted_reasoning=has_reasoning,
                    has_compaction_item=has_compaction,
                    summary_chars=len(summary) if isinstance(summary, str) else 0,
                )
            )
        elif rtype == "token_usage_record":
            turn_id = str(payload.get("turn_id") or payload.get("root_turn_id") or "unknown")
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            if turn_id not in turns:
                turns[turn_id] = TurnUsage(turn_id=turn_id)
                turn_order.append(turn_id)
            turn = turns[turn_id]
            turn.requests += 1
            timestamp = record.get("timestamp")
            if turn.first_ts is None:
                turn.first_ts = timestamp
            turn.last_ts = timestamp
            input_tokens = int(usage.get("input_tokens") or 0)
            cached_tokens = int(usage.get("cached_input_tokens") or 0)
            turn.input_total += input_tokens
            turn.cached_total += cached_tokens
            turn.output_tokens += int(usage.get("output_tokens") or 0)
            turn.reasoning_tokens += int(usage.get("reasoning_output_tokens") or 0)
            if turn.requests == 1:
                turn.first_input = input_tokens
                turn.first_cached = cached_tokens
            turn.last_input = input_tokens
            turn.last_cached = cached_tokens
    metrics.turns = [turns[tid] for tid in turn_order]
    if max_turns is not None:
        metrics.turns = metrics.turns[-max_turns:]
    return metrics


def handoff_metrics(
    metrics: RolloutMetrics, boundary_iso: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Compare the last turn before a switch with the first turn after it."""
    if not metrics.turns:
        return None
    if not boundary_iso:
        return None
    before = None
    after = None
    for turn in metrics.turns:
        if turn.first_ts and turn.first_ts < boundary_iso:
            before = turn
        elif turn.first_ts and turn.first_ts >= boundary_iso:
            after = turn
            break
    if before is None or after is None:
        return None
    return {
        "boundary": boundary_iso,
        "before": before.as_dict(),
        "after": after.as_dict(),
        "cache_drop_points": round(before.last_cache_ratio - after.first_cache_ratio, 2),
        "input_delta": after.first_input - before.last_input,
        "recovered_after_requests": after.requests,
        "recovered_cache_ratio": round(after.last_cache_ratio, 2),
    }
