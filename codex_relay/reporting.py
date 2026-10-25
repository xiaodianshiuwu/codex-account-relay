from __future__ import annotations

from typing import Any, Dict, Optional

from .store import Store
from .threads import get_thread, handoff_metrics, rollout_metrics, rollout_path_for
from .util import RelayError, human_bytes, short_text


def inspect_thread(store: Store, thread_id: str, *, allow_missing_rollout: bool = False) -> Dict[str, Any]:
    thread = get_thread(store.paths, thread_id)
    if not thread:
        raise RelayError(f"找不到会话：{thread_id}")
    path = rollout_path_for(store.paths, thread)
    if allow_missing_rollout and not path.exists():
        return {
            "thread": thread.as_dict(),
            "metrics": None,
            "available": False,
            "reason": f"rollout 文件不存在：{path}",
        }
    metrics = rollout_metrics(path)
    return {
        "thread": thread.as_dict(),
        "metrics": metrics.as_dict(),
        "available": True,
    }


def report_handoff(
    store: Store,
    thread_id: Optional[str] = None,
    *,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    if thread_id is None:
        thread_id = store.state.get("last_thread_id")
    if not thread_id:
        raise RelayError("没有指定会话，也没有记录过最近会话")
    thread = get_thread(store.paths, thread_id)
    if not thread:
        raise RelayError(f"找不到会话：{thread_id}")
    boundary = since
    if not boundary:
        last_switch = store.state.get("last_switch") or {}
        boundary = last_switch.get("at")
    path = rollout_path_for(store.paths, thread)
    metrics = rollout_metrics(path)
    handoff = handoff_metrics(metrics, boundary) if boundary else None
    return {
        "thread": thread.as_dict(),
        "boundary": boundary,
        "handoff": handoff,
        "latest_turn": metrics.latest_turn.as_dict() if metrics.latest_turn else None,
        "reasoning_encrypted_items": metrics.reasoning_encrypted_items,
        "reasoning_encrypted_bytes": metrics.reasoning_encrypted_bytes,
        "compactions": [c.as_dict() for c in metrics.compactions],
    }


def format_handoff(report: Dict[str, Any]) -> str:
    thread = report.get("thread") or {}
    lines = [
        f"会话: {thread.get('id')}  {short_text(str(thread.get('title') or ''), 60)}",
        f"边界: {report.get('boundary') or '未指定'}",
    ]
    handoff = report.get("handoff")
    if not handoff:
        lines.append("没有找到边界两侧的完整轮次；可用 --since 指定切换时间。")
        latest = report.get("latest_turn")
        if latest:
            lines.append(
                "最近轮次: "
                f"{latest.get('requests')} 请求 · 首轮输入 {latest.get('first_input_tokens')} · "
                f"首轮缓存 {latest.get('first_cached_tokens')} ({latest.get('first_cache_ratio')}%) · "
                f"末轮缓存 {latest.get('last_cached_tokens')} ({latest.get('last_cache_ratio')}%)"
            )
        return "\n".join(lines)
    before = handoff.get("before") or {}
    after = handoff.get("after") or {}
    lines.extend(
        [
            "切换前最后一轮:",
            f"  输入 {before.get('last_input_tokens')} · 缓存 {before.get('last_cached_tokens')} "
            f"({before.get('last_cache_ratio')}%) · 请求 {before.get('requests')}",
            "切换后第一轮:",
            f"  首请求输入 {after.get('first_input_tokens')} · 缓存 {after.get('first_cached_tokens')} "
            f"({after.get('first_cache_ratio')}%)",
            f"  同轮恢复后缓存 {after.get('last_cached_tokens')} ({after.get('last_cache_ratio')}%) · "
            f"请求 {after.get('requests')}",
            f"缓存命中率下降: {handoff.get('cache_drop_points')} 个百分点",
            f"首请求输入增量: {handoff.get('input_delta')} tokens",
            f"加密推理项: {report.get('reasoning_encrypted_items')} 个 · "
            f"{human_bytes(int(report.get('reasoning_encrypted_bytes') or 0))}",
        ]
    )
    compactions = report.get("compactions") or []
    if compactions:
        latest = compactions[-1]
        lines.append(
            f"最近压缩: window {latest.get('window_number')} · {latest.get('item_count')} 项 · "
            f"含加密推理={latest.get('has_encrypted_reasoning')}"
        )
    return "\n".join(lines)
