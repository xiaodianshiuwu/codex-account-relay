from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__, codex_env
from .config import load_config, save_config
from .appserver import login_in_isolated_home
from .authfile import auth_summary, validate_auth
from .doctor import check_title, run_doctor
from .i18n import LANGUAGES, Translator, display_width, pad_display, resolve_language, truncate_display
from .reporting import inspect_thread, report_handoff
from .store import Store, default_paths
from .switch import SwitchOptions, switch
from .threads import get_thread, list_threads, most_recent_thread
from .usage import idle_check, max_used_percent, refresh_usage, usage_line, usage_text
from .util import RelayError, executable_command, human_bytes, now_iso, parse_iso


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOTHING = 2
EXIT_BLOCKED = 3

_TRANSLATOR: Optional[Translator] = None


def translator() -> Translator:
    global _TRANSLATOR
    if _TRANSLATOR is None:
        _TRANSLATOR = Translator()
    return _TRANSLATOR


def t(key: str, **kwargs: Any) -> str:
    return translator().t(key, **kwargs)


def tf(key: str, *args: Any) -> str:
    return translator().tf(key, *args)


def tx(message: Any) -> str:
    """Translate a message that originates in a module with legacy literals."""
    return translator().external(message)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _token_count(value: Any) -> str:
    return translator().n(value)


def _fmt_iso(value: Any) -> str:
    if isinstance(value, (int, float)):
        try:
            return translator().dt(datetime.fromtimestamp(value).astimezone(), seconds=True)
        except (OverflowError, OSError, ValueError):
            return str(value)
    dt = parse_iso(value)
    if not dt:
        return str(value) if value not in (None, "") else "-"
    return translator().dt(dt.astimezone(), seconds=True)


def _short(value: Any, limit: int = 80) -> str:
    return truncate_display(str(value or ""), limit)


def _pct_value(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _print_table(headers: List[str], rows: List[List[str]]) -> None:
    if not rows:
        return
    widths = [display_width(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], display_width(str(cell)))
    print("  ".join(pad_display(h, widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(pad_display(str(cell), widths[i]) for i, cell in enumerate(row)))


def _active_account_id(store: Store) -> Optional[str]:
    profile = store.current_profile()
    return str(profile.get("account_id")) if profile else None


def _mode_from_args(args: argparse.Namespace, default: str = "desktop") -> str:
    modes = []
    if getattr(args, "desktop", False):
        modes.append("desktop")
    if getattr(args, "cli", False):
        modes.append("cli")
    if getattr(args, "none", False):
        modes.append("none")
    if len(modes) > 1:
        raise RelayError(t("--desktop、--cli、--none 只能选择一个"))
    if modes:
        return modes[0]
    desktop = codex_env.discover_desktop()
    if default == "desktop" and desktop.installed:
        return "desktop"
    return "none"


# --------------------------------------------------------------------- status
def cmd_status(args: argparse.Namespace, store: Store) -> int:
    auth = store.current_auth()
    summary = auth_summary(auth) if auth else None
    current = store.current_profile()
    processes = codex_env.codex_processes()
    desktop_processes = [p for p in processes if p.role.startswith("desktop")]
    cli_processes = [p for p in processes if p.role in ("cli", "cli-app-server")]
    daemon = codex_env.daemon_running()
    last_switch = store.state.get("last_switch")
    last_thread = store.state.get("last_thread_id")
    data = {
        "checked_at": now_iso(),
        "codex_home": str(store.paths.codex_home),
        "relay_home": str(store.paths.relay_home),
        "auth_path": str(store.paths.auth_path),
        "auth_present": bool(auth),
        "auth": summary,
        "current_profile": store.profile_summary(current) if current else None,
        "state_active_profile_id": store.state.get("active_profile_id"),
        "desktop_running": codex_env.app_running(),
        "desktop_processes": codex_env.describe_processes(desktop_processes),
        "cli_processes": codex_env.describe_processes(cli_processes),
        "daemon_running": daemon,
        "daemon_version": codex_env.daemon_version() if daemon else None,
        "last_switch": last_switch,
        "last_thread_id": last_thread,
    }
    if args.json:
        _print_json(data)
        return EXIT_OK
    if not summary:
        print(t("当前未登录 Codex。"))
    else:
        active = (
            t("  (账号仓库: {alias})", alias=current.get("alias"))
            if current
            else t("  (未纳入账号仓库)")
        )
        print(t("当前账号: {email}  [{plan}]", email=summary.get("email"), plan=summary.get("plan")) + active)
        print(t("账号 ID: {account_id}", account_id=summary.get("account_id")))
        print(t(
            "认证模式: {mode}   access 到期: {date}",
            mode=summary.get("auth_mode"),
            date=_fmt_iso(summary.get("access_expires_at")),
        ))
        print(t("Refresh token: {value}", value=t("有") if summary.get("has_refresh_token") else t("无")))
        if current and current.get("usage"):
            print(t("用量: {usage}", usage=usage_text(current.get("usage"), translator())))
    print(t("CODEX_HOME: {path}", path=store.paths.codex_home))
    print(t("桌面 App: {status}", status=t("运行中") if data["desktop_running"] else t("未运行")))
    if desktop_processes:
        items = ", ".join(f"{translator().role(p.role)}:{p.pid}" for p in desktop_processes)
        print(t("  进程: {items}", items=items))
    if cli_processes:
        items = ", ".join(f"{translator().role(p.role)}:{p.pid}" for p in cli_processes)
        print(t("CLI 进程: {items}", items=items))
    print(t("共享 daemon: {status}", status=t("运行中") if daemon else t("未运行")))
    if last_switch:
        print(
            t(
                "上次切换: {at} · {from} → {to} · {mode}",
                at=_fmt_iso(last_switch.get("at")),
                **{
                    "from": (last_switch.get("from") or {}).get("alias") or "?",
                    "to": (last_switch.get("to") or {}).get("alias") or "?",
                    "mode": translator().mode(last_switch.get("mode")),
                },
            )
        )
    if last_thread:
        print(t("最近会话: {thread}", thread=last_thread))
    return EXIT_OK


# ----------------------------------------------------------------------- list
def _refresh_profiles(store: Store, profiles: List[Dict[str, Any]], timeout: float = 90.0) -> None:
    for profile in profiles:
        alias = profile.get("alias")
        print(t("刷新用量: {alias} ...", alias=alias), file=sys.stderr)
        try:
            refresh_usage(store, profile, timeout=timeout)
        except RelayError as exc:
            print(f"  {alias}: {tx(str(exc))}", file=sys.stderr)


def cmd_list(args: argparse.Namespace, store: Store) -> int:
    profiles = store.profiles()
    if args.refresh:
        _refresh_profiles(store, profiles, timeout=args.timeout)
    active_id = _active_account_id(store)
    data = []
    rows = []
    for profile in profiles:
        item = store.profile_summary(profile)
        item["active"] = profile.get("account_id") == active_id
        item["usage_text"] = usage_line(profile, translator())
        data.append(item)
        rows.append(
            [
                "*" if item["active"] else " ",
                str(profile.get("alias")),
                _short(str(profile.get("email") or "-"), 34),
                str(profile.get("plan") or "-"),
                translator().status(profile.get("status") or "ok"),
                usage_text(profile.get("usage"), translator()),
                _fmt_iso(profile.get("last_verified_at")),
            ]
        )
    if args.json:
        _print_json({"profiles": data, "active_account_id": active_id})
        return EXIT_OK
    if not rows:
        print(t("还没有保存任何账号。使用 `codex-relay add --capture <别名>` 捕获当前账号，"))
        print(t("或使用 `codex-relay add <别名>` 登录另一个账号。"))
        return EXIT_OK
    _print_table(
        ["", t("别名"), t("邮箱"), t("计划"), t("状态"), t("用量"), t("校验时间")],
        rows,
    )
    if not args.refresh:
        print("\n" + t("提示：`codex-relay list --refresh` 会通过隔离的官方 app-server 拉取最新用量。"))
    return EXIT_OK


# ------------------------------------------------------------------------ add
def cmd_add(args: argparse.Namespace, store: Store) -> int:
    alias = args.alias.strip()
    if not alias:
        raise RelayError(t("请提供账号别名"))
    from .store import ALIAS_RE

    if not ALIAS_RE.match(alias):
        raise RelayError(t("别名只能包含字母、数字、点、下划线和短横线，长度 1-32，且必须以字母或数字开头"))
    configured_store = codex_env.configured_auth_store(store.paths.config_path)
    if configured_store == "keyring":
        raise RelayError(
            t("当前 config.toml 设置 cli_auth_credentials_store=keyring；"
              "本工具需要 auth.json。请改为 file（或 auto 且确认回退到 file）后重试。")
        )
    if args.use and codex_env.discover_desktop().installed:
        _confirm_restart_permission(args, "添加后切换")
        args.allow_chatgpt_restart = True
    if args.capture:
        auth = store.current_auth()
        if not auth:
            raise RelayError(t("当前没有 auth.json，无法捕获；请先登录或使用 add <别名>"))
        identity = validate_auth(auth)
        profile = store.add_profile(alias, auth)
        store.state["active_profile_id"] = profile["id"]
        store.save_state()
        result = {"ok": True, "captured": True, "profile": store.profile_summary(profile)}
    else:
        # appserver.py owns the login flow but still prints two Chinese
        # banners; print the localized versions here and swallow its copy.
        print(t("正在使用隔离的 CODEX_HOME 登录；不会触碰当前活动账号。"))
        print(t("浏览器中请选择要添加的那个账号。"))
        with contextlib.redirect_stdout(io.StringIO()):
            auth = login_in_isolated_home(
                store.paths,
                device_auth=args.device,
                timeout=args.timeout,
            )
        identity = validate_auth(auth)
        profile = store.add_profile(alias, auth)
        result = {"ok": True, "captured": False, "profile": store.profile_summary(profile)}
    if args.json:
        _print_json(result)
    else:
        print(t(
            "已保存账号: {alias} · {email} [{plan}]",
            alias=profile.get("alias"),
            email=identity.get("email"),
            plan=identity.get("plan"),
        ))
        print(t("账号 ID: {account_id}", account_id=identity.get("account_id")))
        if not args.capture:
            print(t("该账号的凭据已与当前活动账号隔离保存。"))
    if args.use:
        mode = "desktop" if codex_env.discover_desktop().installed else "none"
        if mode == "desktop":
            _confirm_restart_permission(args, "添加后切换账号")
        return _do_switch(
            store,
            alias,
            mode,
            "last",
            force=False,
            verify=True,
            json_output=args.json,
            allow_chatgpt_restart=args.allow_chatgpt_restart,
        )
    return EXIT_OK


def cmd_remove(args: argparse.Namespace, store: Store) -> int:
    profile = store.resolve(args.alias)
    if not args.yes:
        if not sys.stdin.isatty():
            raise RelayError(t("删除账号需要 --yes 确认"))
        answer = input(t("删除账号 {alias} ({email})? [y/N] ", alias=profile.get("alias"), email=profile.get("email")))
        if answer.strip().lower() not in ("y", "yes"):
            print(t("已取消"))
            return EXIT_OK
    removed = store.remove_profile(args.alias)
    print(t("已删除账号 {alias}", alias=removed.get("alias")))
    return EXIT_OK


def cmd_rename(args: argparse.Namespace, store: Store) -> int:
    profile = store.rename_profile(args.alias, args.new_alias)
    print(t("已重命名: {alias}", alias=profile.get("alias")))
    return EXIT_OK


def cmd_label(args: argparse.Namespace, store: Store) -> int:
    profile = store.resolve(args.alias)
    value = (args.display_name or "").strip()
    if not value:
        raise RelayError(t("显示名称不能为空"))
    if len(value) > 64:
        raise RelayError(t("显示名称不能超过 64 个字符"))
    for other in store.profiles():
        if other.get("id") == profile.get("id"):
            continue
        other_name = str(other.get("display_name") or other.get("name") or other.get("alias") or "")
        if other_name.casefold() == value.casefold():
            raise RelayError(t("显示名称已被其他账号使用：{name}", name=value))
    profile["display_name"] = value
    profile["updated_at"] = now_iso()
    store.save_state()
    if args.json:
        _print_json({"ok": True, "profile": store.profile_summary(profile)})
    else:
        print(t("已设置显示名称: {name}", name=value))
    return EXIT_OK


def cmd_import(args: argparse.Namespace, store: Store) -> int:
    path = Path(args.file).expanduser()
    from .authfile import read_auth

    auth = read_auth(path)
    identity = validate_auth(auth)
    profile = store.add_profile(args.alias, auth)
    if args.json:
        _print_json({"ok": True, "profile": store.profile_summary(profile)})
    else:
        print(t(
            "已导入账号: {alias} · {email} [{plan}]",
            alias=profile.get("alias"),
            email=identity.get("email"),
            plan=identity.get("plan"),
        ))
        print(t("源文件仍可能包含凭据；确认不再需要后请自行安全删除。"))
    return EXIT_OK


def cmd_sync(args: argparse.Namespace, store: Store) -> int:
    profile = store.snapshot_current(adopt=True)
    if not profile:
        raise RelayError(t("当前没有 auth.json，无法同步"))
    store.state["active_profile_id"] = profile["id"]
    store.save_state()
    summary = store.profile_summary(profile)
    if args.json:
        _print_json({"ok": True, "profile": summary})
    else:
        print(t(
            "已同步当前 auth.json → {alias} ({email}, refresh {time})",
            alias=profile.get("alias"),
            email=profile.get("email"),
            time=profile.get("last_refresh"),
        ))
    return EXIT_OK


def cmd_export(args: argparse.Namespace, store: Store) -> int:
    profile = store.resolve(args.alias)
    current = store.current_profile()
    if current and current.get("id") == profile.get("id"):
        snap = store.snapshot_current(adopt=True)
        if snap:
            profile = snap
    auth = store.read_secret(str(profile["id"]))
    validate_auth(auth)
    path = Path(args.file).expanduser()
    if path.exists() and not args.yes:
        if not sys.stdin.isatty():
            raise RelayError(t("目标文件已存在；使用 --yes 覆盖"))
        answer = input(t("覆盖 {path}? [y/N] ", path=path))
        if answer.strip().lower() not in ("y", "yes"):
            print(t("已取消"))
            return EXIT_OK
    payload = json.dumps(auth, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    from .util import atomic_write_bytes

    atomic_write_bytes(path, payload, mode=0o600)
    print(t("已导出 {alias} 到 {path}（0600，包含 refresh token）", alias=profile.get("alias"), path=path))
    return EXIT_OK


# --------------------------------------------------------------------- switch
def _progress_emitter():
    def emit(stage: str, message: str) -> None:
        print(
            json.dumps(
                {"event": "progress", "stage": stage, "message": tx(message)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )

    return emit


def _confirm_restart_permission(args: argparse.Namespace, action: str) -> bool:
    if getattr(args, "allow_chatgpt_restart", False):
        return True
    action_text = t(action)
    if getattr(args, "json", False) or not sys.stdin.isatty():
        raise RelayError(t(
            "{action}会退出并重启 ChatGPT，已拒绝擅自执行。"
            "请加 --allow-chatgpt-restart；建议先 --dry-run 查看计划。",
            action=action_text,
        ))
    answer = input(t("{action}会退出并重启 ChatGPT，确认继续？[y/N] ", action=action_text))
    if answer.strip().lower() not in ("y", "yes"):
        raise RelayError(t("已取消（未触碰 ChatGPT）"))
    args.allow_chatgpt_restart = True
    return True


def _do_switch(
    store: Store,
    target: str,
    mode: str,
    thread: Optional[str],
    *,
    force: bool,
    verify: bool,
    json_output: bool,
    allow_chatgpt_restart: bool = False,
    dry_run: bool = False,
    progress=None,
) -> int:
    result = switch(
        store,
        SwitchOptions(
            target=target,
            mode=mode,
            thread=thread,
            force=force,
            verify=verify,
            restart=True,
            allow_chatgpt_restart=allow_chatgpt_restart,
            dry_run=dry_run,
        ),
        progress=progress,
    )
    if json_output:
        _print_json(result)
    else:
        if not result.get("changed"):
            print(t("账号已经是 {alias}，无需切换。", alias=result.get("account", {}).get("alias")))
        else:
            print(t(
                "已切换: {from} → {to} ({email})",
                **{
                    "from": result.get("from", {}).get("alias") or "?",
                    "to": result.get("to", {}).get("alias") or "?",
                    "email": result.get("to", {}).get("email"),
                },
            ))
        print(t(
            "模式: {mode} · 耗时 {ms} ms",
            mode=translator().mode(result.get("mode")),
            ms=result.get("duration_ms"),
        ))
        if result.get("thread_id"):
            print(t("会话: {thread_id}", thread_id=result.get("thread_id")))
        if result.get("app_server_pid"):
            print(t("新 app-server PID: {pid}", pid=result.get("app_server_pid")))
        for note in result.get("notes") or []:
            print(f"· {tx(note)}")
        print(t("备份: {path}", path=result.get("backup")))
    return EXIT_OK


def cmd_switch(args: argparse.Namespace, store: Store) -> int:
    mode = _mode_from_args(args)
    if mode == "desktop" and not args.dry_run:
        _confirm_restart_permission(args, "切换账号")
    return _do_switch(
        store,
        args.alias,
        mode,
        args.thread,
        force=args.force,
        verify=not args.no_verify,
        json_output=args.json,
        allow_chatgpt_restart=args.allow_chatgpt_restart,
        dry_run=args.dry_run,
        progress=_progress_emitter() if args.progress else None,
    )


def cmd_restart(args: argparse.Namespace, store: Store) -> int:
    """Restart the desktop app without changing credentials (restart drill)."""
    started = time.monotonic()
    emit = _progress_emitter() if getattr(args, "progress", False) else None

    def progress(stage: str, message: str) -> None:
        if emit:
            emit(stage, message)

    thread_id = None
    if args.thread and args.thread != "none":
        if args.thread == "last":
            thread_id = resolve_thread_id(store)
        else:
            thread = get_thread(store.paths, args.thread)
            if not thread:
                raise RelayError(t("找不到会话：{id}", id=args.thread))
            thread_id = thread.id
    desktop = codex_env.discover_desktop()
    if not desktop.installed:
        raise RelayError(t("未安装桌面 App"))
    if args.dry_run:
        result = {
            "ok": True,
            "dry_run": True,
            "thread_id": thread_id,
            "desktop_running": codex_env.app_running(),
            "would_quit": bool(codex_env.desktop_bundle_processes()),
            "would_launch": True,
        }
        if args.json:
            _print_json(result)
        else:
            print(t(
                "重启演练：将退出并重启 ChatGPT，恢复会话 {thread}。",
                thread=thread_id or t("不指定"),
            ))
            print(t("未触碰任何进程或凭据。实际执行请加 --allow-chatgpt-restart。"))
        return EXIT_OK
    _confirm_restart_permission(args, "重启")
    if not args.force:
        progress("idle", t("正在确认没有执行中的轮次"))
        idle = idle_check(store, window_seconds=45.0)
        if not idle["idle"]:
            raise RelayError(t("检测到 Codex 正在执行一轮任务，拒绝重启。等待完成或使用 --force。"))
    progress("quit", t("正在安全退出 ChatGPT App"))
    with codex_env.process_control_allowed(True):
        notes = codex_env.quit_desktop(timeout=20.0, force=args.force)
    progress("quit-done", t("ChatGPT App 已退出"))
    launch_started = datetime.now()
    launch_epoch = time.time()
    progress("launch", t("正在启动 ChatGPT App"))
    with codex_env.process_control_allowed(True):
        codex_env.launch_desktop(desktop)
    server = codex_env.wait_for_desktop_app_server(timeout=45.0, not_before=launch_started)
    if server is None:
        raise RelayError(t("App 已启动，但 app-server 未在 45 秒内就绪"))
    progress("server", t("Codex app-server 已就绪"))
    if thread_id:
        progress("renderer", t("正在等待桌面界面就绪"))
        if not codex_env.wait_for_desktop_renderer(launch_epoch, timeout=30.0):
            notes.append(t("未检测到 renderer RPC 就绪信号，仍尝试打开深链接"))
        progress("resume", t("正在恢复原会话"))
        with codex_env.process_control_allowed(True):
            codex_env.open_thread_deeplink(thread_id, repeat=2)
        store.state["last_thread_id"] = thread_id
    store.journal(
        {
            "event": "restart",
            "ok": True,
            "thread_id": thread_id,
            "app_server_pid": server.pid,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    )
    store.save_state()
    progress("done", t("重启完成"))
    result = {
        "ok": True,
        "thread_id": thread_id,
        "app_server_pid": server.pid,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "notes": notes,
    }
    if args.json:
        _print_json(result)
    else:
        print(t(
            "已重启 ChatGPT App（{ms} ms），app-server PID {pid}",
            ms=result["duration_ms"],
            pid=server.pid,
        ))
        if thread_id:
            print(t("已打开会话 {thread_id}", thread_id=thread_id))
        for note in notes:
            print(f"· {tx(note)}")
    return EXIT_OK


def resolve_thread_id(store: Store) -> Optional[str]:
    last = store.state.get("last_thread_id")
    if last and get_thread(store.paths, str(last)):
        return str(last)
    recent = most_recent_thread(store.paths, cwd=os.getcwd()) or most_recent_thread(store.paths)
    return recent.id if recent else None


# --------------------------------------------------------------------- resume
def cmd_resume(args: argparse.Namespace, store: Store) -> int:
    if args.last and args.thread:
        raise RelayError(t("--last 与显式会话 ID 不能同时使用"))
    thread = None
    if args.thread:
        thread = get_thread(store.paths, args.thread)
        if not thread:
            raise RelayError(t("找不到会话：{id}", id=args.thread))
    else:
        thread = most_recent_thread(store.paths, cwd=os.getcwd()) or most_recent_thread(store.paths)
    if not thread:
        raise RelayError(t("没有找到可恢复的会话"))

    mode = _mode_from_args(args)
    current = store.current_profile()
    if args.account:
        target = store.resolve(args.account)
        current_account = current.get("account_id") if current else None
        if target.get("account_id") != current_account:
            if args.no_switch:
                raise RelayError(t(
                    "当前账号是 {current}，与目标账号 {target} 不同；去掉 --no-switch 以执行切换。",
                    current=current.get("alias") if current else t("未管理账号"),
                    target=target.get("alias"),
                ))
            if mode == "desktop":
                _confirm_restart_permission(args, "恢复会话")
            code = _do_switch(
                store,
                str(target.get("alias")),
                mode,
                thread.id,
                force=False,
                verify=True,
                json_output=args.json,
                allow_chatgpt_restart=args.allow_chatgpt_restart,
            )
            if code != EXIT_OK:
                return code
            if mode == "desktop":
                return EXIT_OK
        else:
            store.state["last_thread_id"] = thread.id
            store.save_state()
            if mode == "desktop":
                _confirm_restart_permission(args, "恢复会话")
                with codex_env.process_control_allowed(True):
                    codex_env.open_thread_deeplink(thread.id)
                if args.json:
                    _print_json({"ok": True, "thread_id": thread.id, "mode": "desktop"})
                else:
                    print(t("已在桌面 App 打开会话 {thread_id}", thread_id=thread.id))
                return EXIT_OK

    if mode == "desktop":
        _confirm_restart_permission(args, "恢复会话")
        with codex_env.process_control_allowed(True):
            codex_env.open_thread_deeplink(thread.id)
        store.state["last_thread_id"] = thread.id
        store.save_state()
        if args.json:
            _print_json({"ok": True, "thread_id": thread.id, "mode": "desktop", "cwd": thread.cwd})
        else:
            print(t("已在桌面 App 打开会话 {thread_id}", thread_id=thread.id))
            print(t("目录: {cwd}", cwd=thread.cwd))
        return EXIT_OK

    codex = codex_env.codex_binary_info().get("path")
    if not codex:
        raise RelayError(t("找不到 codex CLI"))
    command = [codex, "--no-daemon", "resume", thread.id]
    if args.print_command:
        print(" ".join(command))
        return EXIT_OK
    env = dict(os.environ)
    env["CODEX_HOME"] = str(store.paths.codex_home)
    print(t(
        "恢复会话 {thread_id}（--no-daemon，账号 {alias}）",
        thread_id=thread.id,
        alias=current.get("alias") if current else t("当前账号"),
    ))
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        if os.name == "nt":
            # os.execvpe cannot replace the process with a .cmd shim and is
            # awkward on Windows terminals; run it as a normal child instead.
            result = subprocess.run(executable_command(command), env=env, check=False)
            return int(result.returncode)
        os.execvpe(codex, command, env)
    except OSError as exc:
        raise RelayError(t("启动 codex resume 失败：{error}", error=exc)) from exc
    return EXIT_OK


# ------------------------------------------------------------------- sessions
def cmd_sessions(args: argparse.Namespace, store: Store) -> int:
    threads = list_threads(
        store.paths,
        limit=args.limit,
        cwd=args.cwd,
        account_id=args.account_id,
        search=args.search,
        include_archived=args.archived,
    )
    profiles = {p.get("account_id"): p for p in store.profiles()}
    rows = []
    data = []
    for thread in threads:
        profile = profiles.get(thread.creator_account_id)
        creator = profile.get("alias") if profile else (thread.creator_account_id or "-")
        item = thread.as_dict()
        item["creator_alias"] = creator
        data.append(item)
        rows.append(
            [
                _fmt_iso(item.get("updated_at") or item.get("updated_local")),
                thread.id,
                str(creator),
                _token_count(thread.tokens_used),
                _short(thread.cwd, 36),
                _short(tx(thread.display_title), 52),
            ]
        )
    if args.json:
        _print_json({"threads": data})
        return EXIT_OK
    _print_table(
        [t("更新时间"), t("会话 ID"), t("创建账号"), t("Tokens"), t("目录"), t("标题")],
        rows,
    )
    return EXIT_OK


# --------------------------------------------------------------------- inspect
def cmd_inspect(args: argparse.Namespace, store: Store) -> int:
    result = inspect_thread(store, args.thread)
    if args.json:
        _print_json(result)
        return EXIT_OK
    thread = result["thread"]
    metrics = result["metrics"]
    print(t("会话: {id}", id=thread['id']))
    print(t("标题: {title}", title=_short(tx(str(thread.get("title") or "")), 100)))
    print(t("目录: {cwd}", cwd=thread.get("cwd")))
    print(t("创建者: {creator}", creator=thread.get("creator_account_id") or "-"))
    print(t(
        "更新时间: {time} · 大小 {size}",
        time=_fmt_iso(thread.get("updated_at") or thread.get("updated_local")),
        size=metrics.get("size_human"),
    ))
    print(t(
        "记录: response_item={response_item} reasoning={reasoning} encrypted={encrypted} ({size})",
        response_item=metrics.get("response_item_types"),
        reasoning=metrics.get("reasoning_items"),
        encrypted=metrics.get("reasoning_encrypted_items"),
        size=human_bytes(int(metrics.get("reasoning_encrypted_bytes") or 0)),
    ))
    totals = metrics.get("totals") or {}
    print(t(
        "Token 汇总: 输入 {input} · 缓存 Token {cached} ({ratio}%) · 输出 {output} · "
        "推理 Token {reasoning} · 合计 {total} · {requests} 请求",
        input=_token_count(totals.get("input_tokens")),
        cached=_token_count(totals.get("cached_input_tokens")),
        ratio=_pct_value(totals.get("cache_ratio", 0), 1),
        output=_token_count(totals.get("output_tokens")),
        reasoning=_token_count(totals.get("reasoning_output_tokens")),
        total=_token_count(totals.get("total_tokens")),
        requests=totals.get("requests", 0),
    ))
    print(t("压缩检查点: {count}", count=len(metrics.get("compactions") or [])))
    for compaction in metrics.get("compactions") or []:
        print(t(
            "  window {window} · {time} · {count} 项 · {types} · 含加密推理={flag}",
            window=compaction.get("window_number"),
            time=_fmt_iso(compaction.get("timestamp")),
            count=compaction.get("item_count"),
            types=compaction.get("item_types"),
            flag=compaction.get("has_encrypted_reasoning"),
        ))
    turns = metrics.get("turns") or []
    rows = []
    for index, turn in enumerate(turns[-12:], start=max(1, len(turns) - 11)):
        rows.append(
            [
                str(index),
                str(turn.get("requests")),
                _token_count(turn.get("first_input_tokens")),
                translator().pct(turn.get("first_cache_ratio"), 1),
                _token_count(turn.get("last_input_tokens")),
                translator().pct(turn.get("last_cache_ratio"), 1),
                translator().pct(turn.get("total_cache_ratio"), 1),
                _fmt_iso(turn.get("first_timestamp")),
            ]
        )
    print("\n" + t("轮次（最后 12 轮）:"))
    _print_table(
        [t("#"), t("请求"), t("首输入"), t("首缓存"), t("末输入"), t("末缓存"), t("总缓存"), t("开始")],
        rows,
    )
    return EXIT_OK


def _format_handoff(report: Dict[str, Any]) -> str:
    """Render the `report` command with localized labels.

    ``report_handoff`` returns structured data; only the presentation lives
    here so JSON output and the underlying metrics stay untouched.
    """
    thread = report.get("thread") or {}
    lines = [
        t("会话: {id}  {title}", id=thread.get("id"), title=_short(tx(str(thread.get("title") or "")), 60)),
        t("边界: {boundary}", boundary=report.get("boundary") or t("未指定")),
    ]
    handoff = report.get("handoff")
    if not handoff:
        lines.append(t("没有找到边界两侧的完整轮次；可用 --since 指定切换时间。"))
        latest = report.get("latest_turn")
        if latest:
            lines.append(t(
                "最近轮次: {requests} 请求 · 首轮输入 {input} · 首轮缓存 {cached} ({ratio}%) · "
                "末轮缓存 {last_cached} ({last_ratio}%)",
                requests=latest.get("requests"),
                input=latest.get("first_input_tokens"),
                cached=latest.get("first_cached_tokens"),
                ratio=_pct_value(latest.get("first_cache_ratio"), 1),
                last_cached=latest.get("last_cached_tokens"),
                last_ratio=_pct_value(latest.get("last_cache_ratio"), 1),
            ))
        return "\n".join(lines)
    before = handoff.get("before") or {}
    after = handoff.get("after") or {}
    lines.extend(
        [
            t("切换前最后一轮:"),
            t(
                "  输入 {input} · 缓存 {cached} ({ratio}%) · 请求 {requests}",
                input=before.get("last_input_tokens"),
                cached=before.get("last_cached_tokens"),
                ratio=_pct_value(before.get("last_cache_ratio"), 1),
                requests=before.get("requests"),
            ),
            t("切换后第一轮:"),
            t(
                "  首请求输入 {input} · 缓存 {cached} ({ratio}%)",
                input=after.get("first_input_tokens"),
                cached=after.get("first_cached_tokens"),
                ratio=_pct_value(after.get("first_cache_ratio"), 1),
            ),
            t(
                "  同轮恢复后缓存 {cached} ({ratio}%) · 请求 {requests}",
                cached=after.get("last_cached_tokens"),
                ratio=_pct_value(after.get("last_cache_ratio"), 1),
                requests=after.get("requests"),
            ),
            t("缓存命中率下降: {points} 个百分点", points=handoff.get("cache_drop_points")),
            t("首请求输入增量: {delta} tokens", delta=handoff.get("input_delta")),
            t(
                "加密推理项: {count} 个 · {size}",
                count=report.get("reasoning_encrypted_items"),
                size=human_bytes(int(report.get("reasoning_encrypted_bytes") or 0)),
            ),
        ]
    )
    compactions = report.get("compactions") or []
    if compactions:
        latest = compactions[-1]
        lines.append(t(
            "最近压缩: window {window} · {count} 项 · 含加密推理={flag}",
            window=latest.get("window_number"),
            count=latest.get("item_count"),
            flag=latest.get("has_encrypted_reasoning"),
        ))
    return "\n".join(lines)


def cmd_report(args: argparse.Namespace, store: Store) -> int:
    report = report_handoff(store, args.thread, since=args.since)
    if args.json:
        _print_json(report)
    else:
        print(_format_handoff(report))
    return EXIT_OK


# ----------------------------------------------------------------------- usage
def cmd_usage(args: argparse.Namespace, store: Store) -> int:
    if args.all:
        profiles = store.profiles()
    elif args.alias:
        profiles = [store.resolve(args.alias)]
    else:
        current = store.current_profile()
        if not current:
            raise RelayError(t("当前账号未纳入账号仓库；请先 add --capture"))
        profiles = [current]
    if not profiles:
        raise RelayError(t("还没有保存任何账号"))
    results = []
    for profile in profiles:
        if not args.cached:
            print(t("刷新 {alias} ...", alias=profile.get("alias")), file=sys.stderr)
            try:
                refresh_usage(store, profile, timeout=args.timeout)
            except RelayError as exc:
                print(f"  {profile.get('alias')}: {tx(str(exc))}", file=sys.stderr)
        results.append(store.profile_summary(profile))
    if args.json:
        _print_json({"profiles": results})
        return EXIT_OK
    rows = []
    for profile in profiles:
        usage = profile.get("usage")
        rows.append(
            [
                profile.get("alias"),
                _short(str(profile.get("email") or "-"), 32),
                str(profile.get("plan") or "-"),
                translator().status(profile.get("status") or "ok"),
                usage_text(usage, translator()),
                _fmt_iso(profile.get("last_verified_at")),
            ]
        )
    _print_table(
        [t("别名"), t("邮箱"), t("计划"), t("状态"), t("用量"), t("校验时间")],
        rows,
    )
    return EXIT_OK


# ------------------------------------------------------------------------ auto
def _auto_once(store: Store, args: argparse.Namespace) -> int:
    current = store.current_profile()
    if not current:
        raise RelayError(t("当前账号未纳入账号仓库；请先 add --capture"))
    refresh_usage(store, current, timeout=args.timeout)
    used = max_used_percent(current.get("usage"))
    exhausted = bool((current.get("usage") or {}).get("rate_limit_reached_type"))
    if used < args.threshold and not exhausted:
        result = {
            "action": "none",
            "reason": "below-threshold",
            "active": current.get("alias"),
            "max_used_percent": used,
            "threshold": args.threshold,
        }
        if args.json:
            _print_json(result)
        else:
            print(t(
                "{alias} 用量 {percent}% 未达到阈值 {threshold}%",
                alias=current.get("alias"),
                percent=_pct_value(used, 0),
                threshold=args.threshold,
            ))
        return EXIT_NOTHING

    candidates = [
        p
        for p in store.profiles()
        if p.get("id") != current.get("id") and p.get("status") != "needs_relogin"
    ]
    if not candidates:
        result = {"action": "none", "reason": "no-candidate", "active": current.get("alias")}
        if args.json:
            _print_json(result)
        else:
            print(t("没有其他可用账号"))
        return EXIT_NOTHING

    # Check every candidate before switching: a second exhausted account is
    # worse than staying put. `next` keeps declaration order among usable ones.
    usable = []
    for candidate in candidates:
        try:
            refresh_usage(store, candidate, timeout=args.timeout)
        except RelayError as exc:
            print(t("跳过 {alias}: {error}", alias=candidate.get("alias"), error=tx(str(exc))), file=sys.stderr)
            continue
        usage = candidate.get("usage") or {}
        if max_used_percent(usage) >= args.threshold or usage.get("rate_limit_reached_type"):
            print(
                t(
                    "跳过 {alias}: 已用 {percent}%",
                    alias=candidate.get("alias"),
                    percent=_pct_value(max_used_percent(usage), 0),
                ),
                file=sys.stderr,
            )
            continue
        usable.append(candidate)
    if not usable:
        result = {"action": "none", "reason": "no-usable-candidate", "active": current.get("alias")}
        if args.json:
            _print_json(result)
        else:
            print(t("没有仍有额度的其他账号"))
        return EXIT_NOTHING
    if args.strategy == "best":
        usable.sort(key=lambda p: max_used_percent(p.get("usage")))
    target = usable[0]
    idle = idle_check(store)
    plan = {
        "action": "switch",
        "from": current.get("alias"),
        "to": target.get("alias"),
        "used_percent": used,
        "threshold": args.threshold,
        "idle": idle,
        "dry_run": args.dry_run,
    }
    if not idle["idle"] and not args.force:
        plan["action"] = "blocked"
        plan["reason"] = "active-turn"
        if args.json:
            _print_json(plan)
        else:
            print(t("检测到可能正在执行的轮次，未切换："))
            for item in idle.get("active") or []:
                print(f"  {item}")
            print(t("如确认空闲，使用 --force。"))
        return EXIT_BLOCKED
    if args.dry_run:
        if args.json:
            _print_json(plan)
        else:
            print(t("计划切换: {from} → {to}（dry-run）", **{"from": current.get("alias"), "to": target.get("alias")}))
        return EXIT_OK
    if not args.allow_chatgpt_restart or not args.yes:
        plan["action"] = "blocked"
        plan["reason"] = "process-control-disabled"
        if args.json:
            _print_json(plan)
        else:
            print(t("自动接力会重启 ChatGPT，已默认禁用。"))
            print(t("确认要自动重启请显式加 --allow-chatgpt-restart --yes。"))
        return EXIT_BLOCKED
    switch(
        store,
        SwitchOptions(
            target=str(target.get("alias")),
            mode="desktop",
            thread="last",
            force=False,
            verify=True,
            restart=True,
            allow_chatgpt_restart=True,
        ),
    )
    plan["action"] = "switched"
    if args.json:
        _print_json(plan)
    else:
        print(t("已自动切换: {from} → {to}", **{"from": current.get("alias"), "to": target.get("alias")}))
    return EXIT_OK


def cmd_auto(args: argparse.Namespace, store: Store) -> int:
    if args.once:
        return _auto_once(store, args)
    if not args.allow_chatgpt_restart or not args.yes:
        raise RelayError(t(
            "自动接力守护会重启 ChatGPT，已默认禁用。"
            "请先使用 `auto --once --dry-run` 查看建议；"
            "确认要长期自动重启再加 --allow-chatgpt-restart --yes。"
        ))
    print(t(
        "自动接力守护已启动（阈值 {threshold}%，间隔 {interval}s）。Ctrl-C 退出。",
        threshold=args.threshold,
        interval=args.interval,
    ))
    while True:
        try:
            code = _auto_once(store, args)
        except RelayError as exc:
            print(t("检查失败: {error}", error=tx(str(exc))), file=sys.stderr)
            code = EXIT_ERROR
        if code == EXIT_OK:
            time.sleep(args.interval)
        else:
            time.sleep(args.interval)
    return EXIT_OK


# ---------------------------------------------------------------------- doctor
def cmd_doctor(args: argparse.Namespace, store: Store) -> int:
    result = run_doctor(store, language=translator().language)
    if args.json:
        _print_json(result)
    else:
        symbols = {"pass": "✓", "warn": "⚠", "fail": "✗"}
        for check in result["checks"]:
            title = check_title(translator(), str(check.get("check") or ""))
            print(f"{symbols.get(check['level'], '?')} {title}: {check['message']}")
        print("\n" + t(
            "结果: {failures} 个失败 · {warnings} 个警告 · {state}",
            failures=result["failures"],
            warnings=result["warnings"],
            state=t("可正常使用") if result["ok"] else t("需要处理"),
        ))
    return EXIT_OK if result["ok"] else EXIT_ERROR


# ----------------------------------------------------------------------- misc
def cmd_logs(args: argparse.Namespace, store: Store) -> int:
    events = store.journal_tail(limit=args.limit)
    if args.json:
        _print_json({"events": events})
        return EXIT_OK
    if not events:
        print(t("暂无切换记录"))
        return EXIT_OK
    for event in events:
        print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return EXIT_OK


def cmd_setup(args: argparse.Namespace, store: Store) -> int:
    """Detect and optionally persist a manual Codex installation.

    Automatic discovery remains the default.  This command is the supported
    fallback for a Codex binary or data directory outside PATH/常规安装位置.
    """
    config = load_config()
    if args.reset:
        config.pop("codex_binary", None)
        config.pop("codex_home", None)
        save_config(config)
        print(t("已清除手动 Codex 配置，将重新使用自动发现。"))
        return EXIT_OK

    codex_arg = args.codex
    home_arg = args.codex_home
    detected = codex_arg or codex_env.which_codex()
    if codex_arg:
        path = Path(codex_arg).expanduser()
        if not path.is_file():
            raise RelayError(t("指定的 Codex CLI 不存在或不是文件：{path}", path=path))
        codex_arg = str(path.resolve())
        detected = codex_arg
    if home_arg:
        home_path = Path(home_arg).expanduser()
        if not home_path.exists():
            raise RelayError(t("指定的 Codex 数据目录不存在：{path}", path=home_path))
        home_arg = str(home_path.resolve())

    if detected:
        version = codex_env.version_of(str(detected)) if hasattr(codex_env, "version_of") else None
        if not version:
            # `version_of` lives in util; keep this command useful even when a
            # platform-specific probe cannot report a version.
            from .util import version_of
            version = version_of(str(detected))
        if codex_arg:
            config["codex_binary"] = codex_arg
        if home_arg:
            config["codex_home"] = home_arg
        if codex_arg or home_arg:
            save_config(config)
        result = {
            "detected": str(detected),
            "version": version,
            "codex_home": home_arg or config.get("codex_home"),
            "saved": bool(codex_arg or home_arg),
        }
        if args.json:
            _print_json(result)
        else:
            print(t("已找到 Codex CLI：{path}", path=detected))
            if version:
                print(t("版本：{version}", version=version))
            if home_arg or config.get("codex_home"):
                print(t("Codex 数据目录：{path}", path=home_arg or config.get("codex_home")))
            if not codex_arg and not home_arg:
                print(t("自动发现可用；如需固定自定义路径，请运行 codex-relay setup --codex <路径> --codex-home <目录>。"))
        return EXIT_OK

    print(t("未找到 Codex CLI。请先安装 Codex，或用 --codex <路径> 指定可执行文件。"), file=sys.stderr)
    print(t("安装说明：https://github.com/openai/codex"), file=sys.stderr)
    return EXIT_ERROR


def cmd_version(args: argparse.Namespace, store: Store) -> int:
    binary = codex_env.codex_binary_info()
    desktop = codex_env.discover_desktop()
    data = {
        "relay_version": __version__,
        "python": sys.version.split()[0],
        "codex_cli": binary,
        "desktop": {
            "path": str(desktop.path) if desktop.path else None,
            "version": desktop.version,
            "embedded_cli": str(desktop.embedded_cli) if desktop.embedded_cli else None,
            "embedded_cli_version": desktop.embedded_cli_version,
        },
        "codex_home": str(store.paths.codex_home),
        "relay_home": str(store.paths.relay_home),
    }
    if args.json:
        _print_json(data)
    else:
        print(t("codex-relay {version} · Python {python}", version=__version__, python=data["python"]))
        print(t("codex CLI: {version} · {path}", version=binary.get("version"), path=binary.get("path")))
        if desktop.installed:
            print(t(
                "桌面 App: {name} {version} · {path}",
                name=desktop.display_name,
                version=desktop.version,
                path=desktop.path,
            ))
            print(t("内置 CLI: {version}", version=desktop.embedded_cli_version))
    return EXIT_OK


# ---------------------------------------------------------------------- parser
class LocalizedArgumentParser(argparse.ArgumentParser):
    """ArgumentParser with a translated skeleton and error messages."""

    def __init__(self, *args: Any, translator: Optional[Translator] = None, **kwargs: Any) -> None:
        self._tr = translator or Translator()
        super().__init__(*args, **kwargs)
        self._positionals.title = self._tr.t("位置参数")
        self._optionals.title = self._tr.t("选项")

    def format_usage(self) -> str:
        text = super().format_usage()
        prefix = self._tr.t("用法") + ": "
        if text.startswith("usage: "):
            return prefix + text[len("usage: "):]
        return text

    def format_help(self) -> str:
        text = super().format_help()
        prefix = self._tr.t("用法") + ": "
        if text.startswith("usage: "):
            text = prefix + text[len("usage: "):]
        return text

    def _localize_error(self, message: str) -> str:
        tr = self._tr
        patterns = [
            (r"the following arguments are required: (.+)", lambda m: tr.t("缺少必需参数：{names}", names=m.group(1))),
            (r"unrecognized arguments: (.+)", lambda m: tr.t("无法识别的参数：{args}", args=m.group(1))),
            (
                r"argument (.+?): invalid choice: (.*) \(choose from (.+)\)",
                lambda m: tr.t(
                    "参数 {argument}：无效选项 “{value}”（可选值：{choices}）",
                    argument=m.group(1),
                    value=m.group(2),
                    choices=m.group(3),
                ),
            ),
            (
                r"argument (.+?): invalid (?:int|float) value: (.*)",
                lambda m: tr.t("参数 {argument}：无效数值 “{value}”", argument=m.group(1), value=m.group(2)),
            ),
            (
                r"argument (.+?): expected one argument",
                lambda m: tr.t("参数 {argument}：需要一个值", argument=m.group(1)),
            ),
            (r"invalid choice: (.*) \(choose from (.+)\)", lambda m: tr.t("无效选项 “{value}”（可选值：{choices}）", value=m.group(1), choices=m.group(2))),
        ]
        for pattern, render in patterns:
            match = re.match(pattern, message)
            if match:
                return render(match)
        return message

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(2, f"{self._tr.t('错误')}: {self._localize_error(message)}\n")


def _add_help(parser: argparse.ArgumentParser, tr: Translator) -> None:
    parser.add_argument("-h", "--help", action="help", help=tr.t("显示帮助并退出"))


def _add_lang(parser: argparse.ArgumentParser, tr: Translator, *, suppress: bool = False) -> None:
    kwargs: Dict[str, Any] = {
        "choices": list(LANGUAGES),
        "help": tr.t("界面语言（zh-Hans、zh-Hant、en、ja），默认跟随系统语言"),
    }
    if suppress:
        kwargs["default"] = argparse.SUPPRESS
    parser.add_argument("--lang", **kwargs)


def build_parser(translator_: Optional[Translator] = None) -> argparse.ArgumentParser:
    tr = translator_ or translator()
    parser = LocalizedArgumentParser(
        prog="codex-relay",
        description=tr.t("Codex 多账号接力：原会话恢复、上下文/推理状态继承、缓存与用量诊断。"),
        add_help=False,
        translator=tr,
    )
    _add_help(parser, tr)
    parser.add_argument("--version", action="store_true", help=tr.t("显示版本"))
    _add_lang(parser, tr)
    sub = parser.add_subparsers(
        dest="command",
        title=tr.t("命令"),
        metavar="<command>",
        parser_class=lambda **kwargs: LocalizedArgumentParser(translator=tr, **kwargs),
    )

    status = sub.add_parser("status", help=tr.t("查看当前账号、进程和上次切换"), add_help=False, description=tr.t("查看当前账号、进程和上次切换"))
    _add_help(status, tr)
    _add_lang(status, tr, suppress=True)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    listing = sub.add_parser("list", help=tr.t("列出已保存账号"), add_help=False, description=tr.t("列出已保存账号"))
    _add_help(listing, tr)
    _add_lang(listing, tr, suppress=True)
    listing.add_argument("--refresh", action="store_true", help=tr.t("通过隔离 app-server 拉取最新用量"))
    listing.add_argument("--timeout", type=float, default=90.0)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_list)

    add = sub.add_parser("add", help=tr.t("添加账号（隔离 CODEX_HOME 登录，或捕获当前账号）"), add_help=False, description=tr.t("添加账号（隔离 CODEX_HOME 登录，或捕获当前账号）"))
    _add_help(add, tr)
    _add_lang(add, tr, suppress=True)
    add.add_argument("alias")
    add.add_argument("--capture", action="store_true", help=tr.t("捕获当前 auth.json，不打开浏览器"))
    add.add_argument("--device", action="store_true", help=tr.t("使用 device code 登录"))
    add.add_argument("--use", action="store_true", help=tr.t("保存后立即切换并恢复最近会话"))
    add.add_argument(
        "--allow-chatgpt-restart",
        action="store_true",
        help=tr.t("与 --use 搭配，显式允许退出/重启 ChatGPT"),
    )
    add.add_argument("--timeout", type=float, default=600.0)
    add.add_argument("--json", action="store_true")
    add.set_defaults(func=cmd_add)

    remove = sub.add_parser("remove", help=tr.t("删除已保存账号"), add_help=False, description=tr.t("删除已保存账号"))
    _add_help(remove, tr)
    _add_lang(remove, tr, suppress=True)
    remove.add_argument("alias")
    remove.add_argument("--yes", action="store_true")
    remove.set_defaults(func=cmd_remove)

    rename = sub.add_parser("rename", help=tr.t("重命名账号别名"), add_help=False, description=tr.t("重命名账号别名"))
    _add_help(rename, tr)
    _add_lang(rename, tr, suppress=True)
    rename.add_argument("alias")
    rename.add_argument("new_alias")
    rename.set_defaults(func=cmd_rename)

    label = sub.add_parser("label", help=tr.t("设置账号显示名称"), add_help=False, description=tr.t("设置账号显示名称"))
    _add_help(label, tr)
    _add_lang(label, tr, suppress=True)
    label.add_argument("alias")
    label.add_argument("display_name")
    label.add_argument("--json", action="store_true")
    label.set_defaults(func=cmd_label)

    sync_parser = sub.add_parser("sync", help=tr.t("把当前 auth.json 的最新 token 写回活动账号仓库"), add_help=False, description=tr.t("把当前 auth.json 的最新 token 写回活动账号仓库"))
    _add_help(sync_parser, tr)
    _add_lang(sync_parser, tr, suppress=True)
    sync_parser.add_argument("--json", action="store_true")
    sync_parser.set_defaults(func=cmd_sync)

    import_parser = sub.add_parser("import", help=tr.t("从 auth.json 文件导入账号"), add_help=False, description=tr.t("从 auth.json 文件导入账号"))
    _add_help(import_parser, tr)
    _add_lang(import_parser, tr, suppress=True)
    import_parser.add_argument("alias")
    import_parser.add_argument("file")
    import_parser.add_argument("--json", action="store_true")
    import_parser.set_defaults(func=cmd_import)

    export_parser = sub.add_parser("export", help=tr.t("导出账号 auth.json（包含凭据，谨慎使用）"), add_help=False, description=tr.t("导出账号 auth.json（包含凭据，谨慎使用）"))
    _add_help(export_parser, tr)
    _add_lang(export_parser, tr, suppress=True)
    export_parser.add_argument("alias")
    export_parser.add_argument("file")
    export_parser.add_argument("--yes", action="store_true")
    export_parser.set_defaults(func=cmd_export)

    switch_parser = sub.add_parser("switch", help=tr.t("切换账号并（桌面模式）重启 App、恢复原会话"), add_help=False, description=tr.t("切换账号并（桌面模式）重启 App、恢复原会话"))
    _add_help(switch_parser, tr)
    _add_lang(switch_parser, tr, suppress=True)
    switch_parser.add_argument("alias")
    switch_parser.add_argument("--desktop", action="store_true", help=tr.t("安全重启 ChatGPT App 并恢复会话"))
    switch_parser.add_argument("--cli", action="store_true", help=tr.t("只替换凭据，供新 CLI 进程使用"))
    switch_parser.add_argument("--none", action="store_true", help=tr.t("只替换 auth.json，不启动前端"))
    switch_parser.add_argument("--thread", default="last", help=tr.t("要恢复的会话 ID，或 last/none"))
    switch_parser.add_argument(
        "--force",
        action="store_true",
        help=tr.t("允许中断活动轮次、存在其他 Codex 进程，或目标账号额度已耗尽时继续"),
    )
    switch_parser.add_argument("--no-verify", action="store_true", help=tr.t("跳过切换前的官方额度校验"))
    switch_parser.add_argument(
        "--allow-chatgpt-restart",
        action="store_true",
        help=tr.t("显式允许退出/重启 ChatGPT（非交互/自动化必须提供）"),
    )
    switch_parser.add_argument("--dry-run", action="store_true", help=tr.t("只输出计划，不改凭据、不碰进程"))
    switch_parser.add_argument("--progress", action="store_true", help=tr.t("向 stderr 输出 JSON 进度事件（GUI 使用）"))
    switch_parser.add_argument("--json", action="store_true")
    switch_parser.set_defaults(func=cmd_switch)

    restart = sub.add_parser("restart", help=tr.t("只重启桌面 App 并恢复会话（不切换账号）"), add_help=False, description=tr.t("只重启桌面 App 并恢复会话（不切换账号）"))
    _add_help(restart, tr)
    _add_lang(restart, tr, suppress=True)
    restart.add_argument("--thread", default="last", help=tr.t("会话 ID，或 last/none"))
    restart.add_argument("--force", action="store_true")
    restart.add_argument(
        "--allow-chatgpt-restart",
        action="store_true",
        help=tr.t("显式允许退出/重启 ChatGPT（非交互/自动化必须提供）"),
    )
    restart.add_argument("--dry-run", action="store_true", help=tr.t("只输出计划，不碰进程"))
    restart.add_argument("--progress", action="store_true", help=tr.t("向 stderr 输出 JSON 进度事件（GUI 使用）"))
    restart.add_argument("--json", action="store_true")
    restart.set_defaults(func=cmd_restart)

    resume = sub.add_parser("resume", help=tr.t("恢复原会话（桌面深链接或 codex --no-daemon resume）"), add_help=False, description=tr.t("恢复原会话（桌面深链接或 codex --no-daemon resume）"))
    _add_help(resume, tr)
    _add_lang(resume, tr, suppress=True)
    resume.add_argument("thread", nargs="?")
    resume.add_argument("--last", action="store_true", help=tr.t("最近会话"))
    resume.add_argument("--account", help=tr.t("先切换到这个账号"))
    resume.add_argument("--desktop", action="store_true")
    resume.add_argument("--cli", action="store_true")
    resume.add_argument("--no-switch", action="store_true")
    resume.add_argument(
        "--allow-chatgpt-restart",
        action="store_true",
        help=tr.t("显式允许因恢复会话而退出/重启 ChatGPT"),
    )
    resume.add_argument("--print", dest="print_command", action="store_true", help=tr.t("只打印 CLI 命令"))
    resume.add_argument("--json", action="store_true")
    resume.set_defaults(func=cmd_resume)

    sessions = sub.add_parser("sessions", help=tr.t("列出最近会话及创建账号"), add_help=False, description=tr.t("列出最近会话及创建账号"))
    _add_help(sessions, tr)
    _add_lang(sessions, tr, suppress=True)
    sessions.add_argument("--limit", type=int, default=20)
    sessions.add_argument("--cwd")
    sessions.add_argument("--account-id")
    sessions.add_argument("--search")
    sessions.add_argument("--archived", action="store_true")
    sessions.add_argument("--json", action="store_true")
    sessions.set_defaults(func=cmd_sessions)

    inspect = sub.add_parser("inspect", help=tr.t("分析 rollout：推理项、压缩、缓存命中"), add_help=False, description=tr.t("分析 rollout：推理项、压缩、缓存命中"))
    _add_help(inspect, tr)
    _add_lang(inspect, tr, suppress=True)
    inspect.add_argument("thread")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_inspect)

    report = sub.add_parser("report", help=tr.t("对比切换前后首轮缓存与输入 token"), add_help=False, description=tr.t("对比切换前后首轮缓存与输入 token"))
    _add_help(report, tr)
    _add_lang(report, tr, suppress=True)
    report.add_argument("thread", nargs="?")
    report.add_argument("--since", help=tr.t("切换边界（ISO 时间）"))
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=cmd_report)

    usage = sub.add_parser("usage", help=tr.t("查询账号额度（官方 app-server）"), add_help=False, description=tr.t("查询账号额度（官方 app-server）"))
    _add_help(usage, tr)
    _add_lang(usage, tr, suppress=True)
    usage.add_argument("alias", nargs="?")
    usage.add_argument("--all", action="store_true")
    usage.add_argument("--cached", action="store_true", help=tr.t("只读本地缓存"))
    usage.add_argument("--timeout", type=float, default=90.0)
    usage.add_argument("--json", action="store_true")
    usage.set_defaults(func=cmd_usage)

    auto = sub.add_parser("auto", help=tr.t("额度阈值自动接力（可 --once 用于 turn 边界）"), add_help=False, description=tr.t("额度阈值自动接力（可 --once 用于 turn 边界）"))
    _add_help(auto, tr)
    _add_lang(auto, tr, suppress=True)
    auto.add_argument("--once", action="store_true")
    auto.add_argument("--threshold", type=float, default=95.0)
    auto.add_argument("--strategy", choices=["next", "best"], default="next")
    auto.add_argument("--interval", type=float, default=60.0)
    auto.add_argument("--timeout", type=float, default=90.0)
    auto.add_argument("--dry-run", action="store_true")
    auto.add_argument("--force", action="store_true")
    auto.add_argument(
        "--allow-chatgpt-restart",
        action="store_true",
        help=tr.t("显式允许自动接力时退出/重启 ChatGPT（默认仅建议，不执行）"),
    )
    auto.add_argument("--yes", action="store_true", help=tr.t("与 --allow-chatgpt-restart 搭配，确认自动化"))
    auto.add_argument("--json", action="store_true")
    auto.set_defaults(func=cmd_auto)

    setup = sub.add_parser("setup", help=tr.t("检测并保存 Codex CLI/数据目录"), add_help=False, description=tr.t("检测并保存 Codex CLI/数据目录"))
    _add_help(setup, tr)
    _add_lang(setup, tr, suppress=True)
    setup.add_argument("--codex", help=tr.t("手动指定 codex 可执行文件路径"))
    setup.add_argument("--codex-home", help=tr.t("手动指定 Codex 数据目录（默认 ~/.codex）"))
    setup.add_argument("--reset", action="store_true", help=tr.t("清除手动配置，恢复自动发现"))
    setup.add_argument("--json", action="store_true")
    setup.set_defaults(func=cmd_setup)

    doctor = sub.add_parser("doctor", help=tr.t("诊断安装、认证、进程、数据库、网络"), add_help=False, description=tr.t("诊断安装、认证、进程、数据库、网络"))
    _add_help(doctor, tr)
    _add_lang(doctor, tr, suppress=True)
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    logs = sub.add_parser("logs", help=tr.t("查看切换审计记录"), add_help=False, description=tr.t("查看切换审计记录"))
    _add_help(logs, tr)
    _add_lang(logs, tr, suppress=True)
    logs.add_argument("--limit", type=int, default=20)
    logs.add_argument("--json", action="store_true")
    logs.set_defaults(func=cmd_logs)

    version = sub.add_parser("version", help=tr.t("显示版本"), add_help=False, description=tr.t("显示版本"))
    _add_help(version, tr)
    _add_lang(version, tr, suppress=True)
    version.add_argument("--json", action="store_true")
    version.set_defaults(func=cmd_version)

    return parser


def _configure_stdio() -> None:
    """Use UTF-8 and consistent newline handling on Windows/Linux terminals."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _extract_lang(argv: List[str]) -> Optional[str]:
    value = None
    for index, arg in enumerate(argv):
        if arg == "--lang" and index + 1 < len(argv):
            value = argv[index + 1]
        elif arg.startswith("--lang="):
            value = arg.split("=", 1)[1]
    return value


def main(argv: Optional[List[str]] = None) -> int:
    _configure_stdio()
    global _TRANSLATOR
    args_list = list(sys.argv[1:] if argv is None else argv)
    language = resolve_language(_extract_lang(args_list))
    _TRANSLATOR = Translator(language)
    parser = build_parser(_TRANSLATOR)
    args = parser.parse_args(args_list)
    if getattr(args, "version", False):
        args.command = "version"
        args.json = False
        args.func = cmd_version
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_OK
    store = Store(default_paths())
    try:
        store.ensure_layout()
        return int(args.func(args, store))
    except RelayError as exc:
        print(f"codex-relay: {tx(str(exc))}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\n" + t("已中断"), file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Normal for `codex-relay ... | head` and for a closed terminal.
        try:
            sys.stdout.close()
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
