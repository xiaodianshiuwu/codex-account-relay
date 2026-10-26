from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import codex_env
from .appserver import verify_profile
from .authfile import auth_summary, read_auth, validate_auth, write_auth
from .store import Store
from .threads import get_thread, most_recent_thread
from .usage import idle_check, max_used_percent
from .util import RelayError, now_iso, safe_unlink


@dataclass
class SwitchOptions:
    target: str
    mode: str = "desktop"  # desktop | cli | none
    thread: Optional[str] = "last"
    force: bool = False
    verify: bool = True
    restart: bool = True
    # Explicit, per-invocation consent for quitting/launching ChatGPT.
    allow_chatgpt_restart: bool = False
    dry_run: bool = False
    operation_guard: Optional[Callable[[], bool]] = None


def _identity_of(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not profile:
        return {}
    return {
        "alias": profile.get("alias"),
        "email": profile.get("email"),
        "account_id": profile.get("account_id"),
    }


def resolve_thread(store: Store, thread_option: Optional[str]) -> Optional[str]:
    if not thread_option or thread_option == "none":
        return None
    if thread_option == "last":
        last = store.state.get("last_thread_id")
        if last:
            thread = get_thread(store.paths, str(last))
            if thread:
                return thread.id
        recent = most_recent_thread(store.paths, cwd=os.getcwd()) or most_recent_thread(store.paths)
        return recent.id if recent else None
    thread = get_thread(store.paths, thread_option)
    if not thread:
        raise RelayError(f"找不到会话：{thread_option}")
    if thread.archived:
        raise RelayError(f"会话已归档：{thread_option}，请先 unarchive")
    return thread.id


def preflight_processes(mode: str, force: bool) -> List[str]:
    notes: List[str] = []
    from .guard_cli import default_state_dir
    from desktop_guard.store import statuses
    if any(item.get('monitoring') and item.get('hostId', 'local') == 'local' for item in statuses(default_state_dir())):
        raise RelayError("本机原聊天额度守护正在运行；请先停止守护再切换账号")
    cli = codex_env.other_codex_processes(exclude_desktop=True)
    if mode != "desktop" and codex_env.desktop_bundle_processes():
        if not force:
            raise RelayError(
                "ChatGPT 桌面 App 正在运行，它是当前 auth.json 的写入者。"
                "请使用 --desktop（安全重启并恢复会话），或先退出 App 再加 --force。"
            )
        notes.append("桌面 App 正在运行；--force 将只替换文件，运行中的 App 不会被热切换")
    if cli:
        listing = ", ".join(f"{p.pid}({p.role})" for p in cli)
        if not force:
            raise RelayError(
                "检测到其他 Codex 进程仍在使用同一 CODEX_HOME："
                f"{listing}。请先结束这些会话，或使用 --force 承担 refresh token 互覆盖风险。"
            )
        notes.append(f"其他 Codex 进程仍在运行：{listing}（--force）")
    return notes


def _snapshot_and_write(store: Store, target_profile: Dict[str, Any]) -> Dict[str, Any]:
    current = store.current_auth()
    previous_profile = store.current_profile() if current else None
    previous_summary = auth_summary(current) if current else None
    if current:
        store.snapshot_current(adopt=True)
        previous_profile = store.current_profile()
        previous_summary = auth_summary(current)
    target_auth = store.read_secret(str(target_profile["id"]))
    target_identity = validate_auth(target_auth)
    if target_identity.get("account_id") != target_profile.get("account_id"):
        raise RelayError("目标账号凭据与账号元数据不一致，拒绝切换")
    write_auth(store.paths.auth_path, target_auth)
    store.set_active(target_profile)
    return {
        "previous_profile": previous_profile,
        "previous_summary": previous_summary,
        "target_auth": target_auth,
    }


def _rollback(store: Store, backup: Optional[Path], restarted: bool) -> List[str]:
    notes: List[str] = []
    if backup is None:
        if restarted:
            try:
                codex_env.launch_desktop()
                notes.append("尚未替换 auth.json；已重新启动 ChatGPT App")
            except RelayError as exc:
                notes.append(f"重新启动 App 失败：{exc}")
        else:
            notes.append("尚未替换 auth.json，无需回滚")
        return notes
    if restarted and codex_env.app_running():
        codex_env.quit_desktop(timeout=20.0, force=False)
    backup_auth = backup / "auth.json"
    if backup_auth.exists():
        data = backup_auth.read_bytes()
        from .util import atomic_write_bytes

        atomic_write_bytes(store.paths.auth_path, data, mode=0o600)
        notes.append("已恢复切换前的 auth.json")
    else:
        safe_unlink(store.paths.auth_path)
        notes.append("切换前没有 auth.json，已移除新写入的文件")
    backup_state = backup / "state.json"
    if backup_state.exists():
        from .util import atomic_write_bytes
        atomic_write_bytes(store.paths.state_path, backup_state.read_bytes(), mode=0o600)
        store.reload()
        notes.append("已恢复切换前的 state.json")
    if restarted:
        try:
            codex_env.launch_desktop()
            notes.append("已重新启动 ChatGPT App")
        except RelayError as exc:
            notes.append(f"回滚时启动 App 失败：{exc}")
    return notes


ProgressCallback = Callable[[str, str], None]


def _progress(callback: Optional[ProgressCallback], stage: str, message: str) -> None:
    if callback is not None:
        try:
            callback(stage, message)
        except Exception:
            pass


def switch(
    store: Store,
    options: SwitchOptions,
    progress: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    started = time.monotonic()
    configured_store = codex_env.configured_auth_store(store.paths.config_path)
    if configured_store == "keyring":
        raise RelayError(
            "当前 config.toml 设置 cli_auth_credentials_store=keyring；"
            "本工具需要 auth.json。请改为 file 后重试。"
        )
    target = store.resolve(options.target)
    target_auth = store.read_secret(str(target["id"]))
    target_identity = validate_auth(target_auth)
    thread_id = resolve_thread(store, options.thread)
    _progress(progress, "resolve", f"已找到账号 {target.get('alias')}")

    active = store.current_profile()
    target_is_active = bool(active and active.get("id") == target.get("id"))
    notes: List[str] = []

    if options.mode == "desktop" and not options.dry_run and not options.allow_chatgpt_restart:
        raise RelayError(
            "为防止擅自启停 ChatGPT，桌面切换/恢复必须显式传入 --allow-chatgpt-restart。"
            "建议先运行 `codex-relay switch <账号> --dry-run` 查看计划；"
            "GUI 中必须在确认框勾选“我确认重启 ChatGPT”。"
        )

    if options.verify and not target_is_active:
        # Verify before any downtime. If the access token is expired, force a
        # refresh; otherwise let the app-server refresh only if it needs to.
        _progress(progress, "verify", "正在通过隔离 app-server 校验目标账号与额度")
        usage, refreshed = verify_profile(
            store.paths,
            target_auth,
            force_refresh=bool(target_identity.get("access_expired")),
            persist_callback=lambda auth: store.persist_profile_auth(target, auth),
        )
        target_auth = refreshed
        target["usage"] = usage
        target["last_verified_at"] = now_iso()
        target["status"] = "ok"
        store.save_state()
        if max_used_percent(usage) >= 100 or usage.get("rate_limit_reached_type"):
            message = (
                f"目标账号 {target.get('alias')} 当前额度已耗尽"
                f"（{usage.get('primary')} / {usage.get('secondary')}）。"
                "切换后可能无法发起新轮次；如仍要切换请加 --force。"
            )
            if not options.force:
                raise RelayError(message)
            notes.append(message)
        _progress(progress, "verified", "目标账号校验通过")

    _progress(progress, "preflight", "正在检查运行中的 Codex 进程")
    notes.extend(preflight_processes(options.mode, options.force))
    if target_is_active:
        if options.mode == "desktop" and thread_id and not options.dry_run:
            with codex_env.process_control_allowed(True):
                codex_env.open_thread_deeplink(thread_id)
            store.state["last_thread_id"] = thread_id
            store.save_state()
        _progress(progress, "done", "目标账号已经是当前账号")
        return {
            "ok": True,
            "changed": False,
            "reason": "dry-run" if options.dry_run else "already-active",
            "account": _identity_of(target),
            "target": _identity_of(target),
            "thread_id": thread_id,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "notes": notes,
        }

    idle_info: Optional[Dict[str, Any]] = None
    if options.mode == "desktop" and not options.force:
        _progress(progress, "idle", "正在确认没有执行中的轮次")
        idle_info = idle_check(store, window_seconds=45.0)
        if not idle_info["idle"]:
            active = idle_info.get("active") or []
            detail = "; ".join(
                f"thread={item.get('thread_id')} turn={item.get('turn_id')} "
                f"started={item.get('started_at')} mtime={item.get('rollout_mtime')}"
                for item in active
            )
            if options.dry_run:
                notes.append(f"当前有活动轮次，实际执行会被阻止：{detail}")
            else:
                raise RelayError(
                    "检测到 Codex 正在执行一轮任务，拒绝在工具调用中途重启 App。"
                    f"（{detail}）\n请等待该轮结束，或确认可中断后使用 --force；"
                    "已完成的工具结果和 rollout 仍会持久化，之后可恢复同一会话。"
                )

    if options.dry_run:
        _progress(progress, "done", "演练完成，未改动任何凭据或进程")
        return {
            "ok": True,
            "changed": False,
            "reason": "dry-run",
            "target": _identity_of(target),
            "thread_id": thread_id,
            "mode": options.mode,
            "usage": target.get("usage"),
            "idle": idle_info,
            "blocked_by_active_turn": bool(idle_info and not idle_info.get("idle")),
            "notes": notes,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    if options.mode == "desktop" and not options.restart and codex_env.app_running():
        raise RelayError("桌面 App 正在运行；--no-restart 下无法安全替换 auth.json")

    if options.operation_guard is not None and not options.operation_guard():
        raise RelayError("Automation settings changed before switching; account unchanged")
    desktop_was_running = codex_env.app_running() or bool(codex_env.desktop_bundle_processes())
    app_server_pid: Optional[int] = None
    # Safety backup before stopping the app, then a second exact backup after
    # the app has stopped (it may refresh tokens while shutting down).
    store.backup("pre-switch")
    backup: Optional[Path] = None
    restarted = False
    previous_control = os.environ.get("CODEX_RELAY_ALLOW_PROCESS_CONTROL")
    if options.allow_chatgpt_restart:
        os.environ["CODEX_RELAY_ALLOW_PROCESS_CONTROL"] = "1"
    try:
        if options.mode == "desktop" and desktop_was_running:
            _progress(progress, "quit", "正在安全退出 ChatGPT App")
            notes.extend(codex_env.quit_desktop(timeout=20.0, force=options.force))
            restarted = True
            _progress(progress, "quit-done", "ChatGPT App 已退出")
        daemon_was_running = codex_env.daemon_running()
        if daemon_was_running:
            daemon_stopped, daemon_note = codex_env.stop_daemon(timeout=20.0)
            if not daemon_stopped:
                raise RelayError(
                    "共享 app-server daemon 正在运行且无法停止；它可能缓存旧账号。"
                    f"请先手动 `codex app-server daemon stop`（{daemon_note}）"
                )
            notes.append(f"已停止共享 app-server daemon：{daemon_note}")

        _progress(progress, "backup", "正在备份凭据与状态")
        backup = store.backup("switch")
        launch_started = datetime.now()
        launch_epoch = time.time()
        _progress(progress, "write", "正在写入目标账号凭据")
        written = _snapshot_and_write(store, target)
        if options.mode == "desktop" and options.restart:
            _progress(progress, "launch", "正在启动 ChatGPT App")
            codex_env.launch_desktop()
            restarted = True
            app_server = codex_env.wait_for_desktop_app_server(
                timeout=45.0, not_before=launch_started
            )
            if app_server is None:
                raise RelayError("ChatGPT App 已启动，但 Codex app-server 未在 45 秒内就绪")
            app_server_pid = app_server.pid
            _progress(progress, "server", "Codex app-server 已就绪")
            if thread_id:
                _progress(progress, "renderer", "正在等待桌面界面就绪")
                if not codex_env.wait_for_desktop_renderer(launch_epoch, timeout=30.0):
                    notes.append("未检测到 renderer RPC 就绪信号，仍尝试打开深链接")
                _progress(progress, "resume", "正在恢复原会话")
                codex_env.open_thread_deeplink(thread_id, repeat=2)
                store.state["last_thread_id"] = thread_id

        final_auth = read_auth(store.paths.auth_path)
        final_identity = validate_auth(final_auth)
        if final_identity.get("account_id") != target.get("account_id"):
            raise RelayError("写入后 auth.json 身份与目标账号不一致")

        store.state["last_switch"] = {
            "at": now_iso(),
            "from": _identity_of(written.get("previous_profile")),
            "to": _identity_of(target),
            "mode": options.mode,
            "thread_id": thread_id,
            "backup": str(backup),
            "app_server_pid": app_server_pid,
        }
        store.save_state()
        result = {
            "ok": True,
            "changed": True,
            "from": _identity_of(written.get("previous_profile")),
            "to": _identity_of(target),
            "mode": options.mode,
            "thread_id": thread_id,
            "app_server_pid": app_server_pid,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "backup": str(backup),
            "notes": notes,
        }
        store.journal(
            {
                "event": "switch",
                "ok": True,
                "from": result["from"],
                "to": result["to"],
                "mode": options.mode,
                "thread_id": thread_id,
                "app_server_pid": app_server_pid,
                "duration_ms": result["duration_ms"],
                "backup": str(backup),
            }
        )
        _progress(progress, "done", "切换完成")
        return result
    except BaseException as exc:
        # Also unwind interrupted transactions before reporting cancellation.
        # Only relaunch if this transaction actually stopped/started the app.
        # In cli/none mode with --force, a running app must be left alone.
        try:
            rollback_notes = _rollback(store, backup, restarted=restarted)
        except Exception as rollback_error:
            rollback_notes = [f"自动回滚未完成：{rollback_error}；备份保留于 {backup}"]
        store.journal(
            {
                "event": "switch",
                "ok": False,
                "target": _identity_of(target),
                "mode": options.mode,
                "thread_id": thread_id,
                "error": str(exc)[:500],
                "rollback": rollback_notes,
            }
        )
        message = f"切换失败：{exc}"
        if rollback_notes:
            message += "\n回滚：" + "；".join(rollback_notes)
        raise RelayError(message) from exc
    finally:
        if previous_control is None:
            os.environ.pop("CODEX_RELAY_ALLOW_PROCESS_CONTROL", None)
        else:
            os.environ["CODEX_RELAY_ALLOW_PROCESS_CONTROL"] = previous_control
