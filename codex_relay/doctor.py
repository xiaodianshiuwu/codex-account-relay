from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import codex_env
from .authfile import auth_file_mode, auth_summary, read_auth, validate_auth
from .i18n import Translator
from .store import Store
from .threads import list_threads
from .util import RelayError, human_bytes, now_iso, parse_iso, short_text, sqlite_uri


CHECK_TITLES = {
    "platform": "运行平台",
    "relay_home": "Relay 数据目录",
    "relay_home_permissions": "Relay 数据目录权限",
    "codex_cli": "Codex CLI",
    "desktop_app": "ChatGPT 桌面 App",
    "desktop_embedded_cli": "内置 Codex CLI",
    "auth_storage_mode": "凭据存储方式",
    "auth": "账号认证",
    "auth_permissions": "认证文件权限",
    "access_token": "Access token",
    "refresh_token": "Refresh token",
    "active_profile": "活动账号",
    "state_file": "状态文件",
    "state_permissions": "状态文件权限",
    "profiles": "账号仓库",
    "state_db": "状态数据库",
    "thread_history_db": "会话数据库",
    "threads": "最近会话",
    "latest_thread": "最新会话",
    "desktop_processes": "桌面进程",
    "cli_processes": "CLI 进程",
    "app_server_daemon": "共享 daemon",
    "network_auth": "认证网络",
    "network_backend": "后端网络",
    "backups": "备份",
    "last_event": "最近事件",
    "temp_dirs": "残留临时目录",
    "temp_cleanup": "临时目录清理",
}


def _mode(path: Path) -> Optional[str]:
    # Windows ACLs are not represented by POSIX mode bits; don't report a
    # misleading 0666 warning there.
    if sys.platform == "win32":
        return None
    try:
        return oct(stat.S_IMODE(path.stat().st_mode))
    except OSError:
        return None


def _check(check: str, level: str, message: str, **extra: Any) -> Dict[str, Any]:
    # The JSON schema (check/level/message) is public API used by the SwiftUI
    # and Tk clients; localized names are derived on display instead of adding
    # a field.
    item = {"check": check, "level": level, "message": message}
    item.update(extra)
    return item


def check_title(tr: Translator, check: str) -> str:
    if check.startswith("profile:"):
        return tr.t("账号：{alias}", alias=check.split(":", 1)[1])
    key = CHECK_TITLES.get(check)
    return tr.t(key) if key else check


def _sqlite_quick_check(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(sqlite_uri(path), uri=True, timeout=10)
        try:
            row = connection.execute("PRAGMA quick_check").fetchone()
            return str(row[0]) if row else None
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        return f"error: {exc}"


def _network_check(url: str, timeout: float = 6.0) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "codex-relay/0.1 (+local diagnostics)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"url": url, "ok": True, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"url": url, "ok": True, "status": exc.code, "note": "HTTP error is still reachable"}
    except Exception as exc:  # noqa: BLE001 - diagnostics
        return {"url": url, "ok": False, "error": str(exc)[:200]}


def run_doctor(store: Store, language: Optional[str] = None) -> Dict[str, Any]:
    tr = Translator(language)
    checks: List[Dict[str, Any]] = []
    paths = store.paths
    store.ensure_layout()

    def add(check: str, level: str, message: str, **extra: Any) -> None:
        checks.append(_check(check, level, message, **extra))

    add("platform", "pass", f"{sys.platform} / Python {sys.version.split()[0]}")
    add(
        "relay_home",
        "pass" if paths.relay_home.exists() else "fail",
        f"{paths.relay_home} ({tr.t('模式')} {_mode(paths.relay_home)})",
    )
    if _mode(paths.relay_home) not in (None, "0o700"):
        add("relay_home_permissions", "warn", tr.t("建议将 ~/.codex-relay 设为 0700"))

    binary = codex_env.codex_binary_info()
    if binary.get("path"):
        add("codex_cli", "pass", f"{binary.get('version')} · {binary.get('path')}")
    else:
        add("codex_cli", "fail", tr.t("找不到 codex CLI"))

    desktop = codex_env.discover_desktop()
    if desktop.installed:
        add("desktop_app", "pass", f"{desktop.display_name} {desktop.version} · {desktop.path}")
        if codex_env.IS_MACOS:
            embedded_version = desktop.embedded_cli_version
            if embedded_version:
                add("desktop_embedded_cli", "pass", embedded_version)
            else:
                add("desktop_embedded_cli", "warn", tr.t("未找到桌面内置 codex CLI"))
        else:
            add(
                "desktop_embedded_cli",
                "pass",
                tr.t("桌面内置 CLI 检查仅适用于 macOS；Windows/Linux 使用独立 codex CLI"),
            )
    else:
        add("desktop_app", "warn", tr.t("未安装 ChatGPT/Codex 桌面 App"))

    configured_store = codex_env.configured_auth_store(paths.config_path)
    if configured_store in ("keyring", "auto"):
        add(
            "auth_storage_mode",
            "warn" if configured_store == "auto" else "fail",
            tr.t(
                "config.toml 设置 cli_auth_credentials_store={mode}；"
                "本工具当前按 auth.json（file backend）工作，请改为 file",
                mode=configured_store,
            ),
        )
    else:
        add("auth_storage_mode", "pass", tr.t("file backend（auth.json）"))

    auth_mode = None
    if paths.auth_path.exists():
        mode = _mode(paths.auth_path)
        try:
            auth = read_auth(paths.auth_path)
            identity = validate_auth(auth, require_refresh=False)
            summary = auth_summary(auth)
            auth_mode = summary.get("auth_mode")
            add(
                "auth",
                "pass",
                f"{summary.get('email')} [{summary.get('plan')}] · {tr.t('模式')} {auth_mode} · "
                f"access {tr.dt(summary.get('access_expires_at'))}",
            )
            if mode not in (None, "0o600"):
                add("auth_permissions", "warn", tr.t("auth.json 权限为 {mode}，建议 0600", mode=mode))
            if summary.get("access_expired"):
                add("access_token", "warn", tr.t("access token 已过期；下次启动会尝试刷新"))
            if not summary.get("has_refresh_token"):
                add("refresh_token", "fail", tr.t("缺少 refresh_token，切换后无法续期"))
            current = store.current_profile()
            if current:
                add("active_profile", "pass", tr.t("匹配账号 {alias}", alias=current.get("alias")))
            else:
                add(
                    "active_profile",
                    "warn",
                    tr.t("当前 auth.json 尚未纳入 relay 管理；切换时会自动捕获"),
                )
        except RelayError as exc:
            add("auth", "fail", tr.external(str(exc)))
    else:
        add("auth", "warn", tr.t("auth.json 不存在（未登录）"))

    state_mode = _mode(paths.state_path)
    if paths.state_path.exists():
        add("state_file", "pass", f"{paths.state_path} ({tr.t('模式')} {state_mode})")
        if state_mode not in (None, "0o600"):
            add("state_permissions", "warn", tr.t("state.json 建议 0600"))
    else:
        add("state_file", "warn", tr.t("尚未创建状态文件"))

    profiles = store.profiles()
    add("profiles", "pass" if profiles else "warn", tr.t("{count} 个已保存账号", count=len(profiles)))
    for profile in profiles:
        alias = profile.get("alias")
        try:
            secret = store.read_secret(str(profile.get("id")))
            identity = validate_auth(secret)
            if identity.get("account_id") != profile.get("account_id"):
                add(f"profile:{alias}", "fail", tr.t("凭据 account_id 与元数据不一致"))
            else:
                add(f"profile:{alias}", "pass", f"{profile.get('email')} [{profile.get('plan')}]")
        except RelayError as exc:
            add(f"profile:{alias}", "fail", tr.external(str(exc)))

    for db_name, db_path in (
        ("state_db", paths.state_db),
        ("thread_history_db", paths.thread_history_db),
    ):
        result = _sqlite_quick_check(db_path)
        if result is None:
            add(db_name, "warn", tr.t("不存在：{path}", path=db_path))
        elif result.lower() == "ok":
            add(db_name, "pass", tr.t("integrity ok · {size}", size=human_bytes(db_path.stat().st_size)))
        else:
            add(db_name, "fail", result)

    try:
        threads = list_threads(paths, limit=5)
        add("threads", "pass", tr.t("最近 {count} 条会话可读", count=len(threads)))
        if threads:
            add(
                "latest_thread",
                "pass",
                f"{threads[0].id} · {short_text(tr.external(threads[0].display_title), 60)}",
            )
    except RelayError as exc:
        add("threads", "fail", tr.external(str(exc)))

    processes = codex_env.codex_processes()
    desktop_procs = [p for p in processes if p.role.startswith("desktop")]
    cli_procs = [p for p in processes if p.role in ("cli", "cli-app-server")]
    if desktop_procs:
        add(
            "desktop_processes",
            "pass",
            ", ".join(f"{tr.role(p.role)}:{p.pid}" for p in desktop_procs),
        )
    if cli_procs:
        add(
            "cli_processes",
            "warn" if len(cli_procs) > 1 else "pass",
            ", ".join(f"{tr.role(p.role)}:{p.pid}" for p in cli_procs),
        )
    if codex_env.daemon_running():
        version = codex_env.daemon_version()
        add("app_server_daemon", "warn", tr.t("共享 daemon 正在运行 · {version}", version=version or tr.t("未知")))
    else:
        add("app_server_daemon", "pass", tr.t("未运行（不会缓存旧账号）"))

    add("network_auth", "pass" if _network_check("https://auth.openai.com")["ok"] else "warn", "auth.openai.com")
    add("network_backend", "pass" if _network_check("https://chatgpt.com")["ok"] else "warn", "chatgpt.com")

    backups = [p for p in paths.backups_dir.iterdir() if p.is_dir()] if paths.backups_dir.exists() else []
    add("backups", "pass" if backups else "warn", tr.t("{count} 个备份", count=len(backups)))

    tails = store.journal_tail(limit=1)
    if tails:
        last = tails[-1]
        level = "pass" if last.get("ok") else "warn"
        event_name = last.get("event") or "event"
        add(
            "last_event",
            level,
            tr.t(
                "{event} · {at} · {state}",
                event=event_name,
                at=tr.dt(last.get("at")),
                state=tr.t("成功") if last.get("ok") else tr.t("失败"),
            )
            + (f" · {tr.external(last.get('error'))}" if last.get("error") else ""),
        )

    temp_dirs = [p for p in paths.tmp_dir.iterdir() if p.is_dir()] if paths.tmp_dir.exists() else []
    if temp_dirs:
        add("temp_dirs", "warn", tr.t("{count} 个残留临时目录", count=len(temp_dirs)))
        if os.environ.get("CODEX_RELAY_CLEAN_TMP") == "1":
            for path in temp_dirs:
                shutil.rmtree(path, ignore_errors=True)
            add("temp_cleanup", "pass", tr.t("已清理残留临时目录"))

    failures = [c for c in checks if c["level"] == "fail"]
    warnings = [c for c in checks if c["level"] == "warn"]
    return {
        "checked_at": now_iso(),
        "ok": not failures,
        "failures": len(failures),
        "warnings": len(warnings),
        "checks": checks,
    }
