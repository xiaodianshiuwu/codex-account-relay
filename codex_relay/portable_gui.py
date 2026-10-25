"""Small cross-platform desktop client for Codex Relay.

This is deliberately built on Tkinter from the Python standard library so the
Windows, Linux and macOS fallback clients use the same Python core and do not
require a browser, Electron, Qt or a second account implementation. The native
SwiftUI client remains the primary macOS experience.

Launch with:
    python -m codex_relay.portable_gui

Headless smoke check:
    python -m codex_relay.portable_gui --self-test
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:  # Tk is optional in headless server images.
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except ImportError:  # pragma: no cover - exercised on minimal CI images
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    filedialog = messagebox = simpledialog = None  # type: ignore[assignment]

from . import __version__
from .i18n import Translator, format_number, normalize_language, system_language
from .store import Store, default_paths
from .auto_config import load_auto_settings, save_auto_settings


PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent


def _settings_path() -> Path:
    return default_paths().relay_home / "portable-ui.json"


def _avatars_path() -> Path:
    return default_paths().relay_home / "avatars.json"


def _default_settings() -> Dict[str, Any]:
    return {
        "theme": "dark",
        "language": _system_language(),
        "quota_display": "remaining",
        "number_format": "full",
    }


def _system_language() -> str:
    # Shared with the CLI: --lang/env/settings/OS precedence lives in i18n.py.
    return system_language()


def _load_settings() -> Dict[str, Any]:
    data = _default_settings()
    path = _settings_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            data.update(value)
    except (OSError, ValueError):
        pass
    return data


def _save_settings(settings: Dict[str, Any]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# Set by PortableApp so child CLI output (errors, doctor messages) follows the
# language chosen in the GUI, not just the OS locale.
_ACTIVE_LANGUAGE: Optional[str] = None


def _cli_command(args: List[str]) -> List[str]:
    if getattr(sys, "frozen", False):
        # PyInstaller packages this module as the GUI executable. Run the
        # shared CLI from a child instance instead of assuming `python -m`.
        return [sys.executable, "--headless-cli", *args]
    return [sys.executable, "-m", "codex_relay", *args]


def _run_cli(args: List[str], timeout: float = 180.0) -> Tuple[int, str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(REPO_ROOT))
    language = _ACTIVE_LANGUAGE or normalize_language(_load_settings().get("language"))
    if language:
        env["CODEX_RELAY_LANG"] = language
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            _cli_command(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            **kwargs,
        )
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def _cli_json(args: List[str], timeout: float = 180.0, allow_codes: Tuple[int, ...] = (0,)) -> Any:
    code, out, err = _run_cli(args, timeout=timeout)
    tr = Translator(_ACTIVE_LANGUAGE or _load_settings().get("language"))
    if code not in allow_codes:
        raise RuntimeError((err or out or tr.tf("CLI 退出码 %d", code)).strip())
    try:
        return json.loads(out)
    except ValueError as exc:
        raise RuntimeError(tr.t("CLI 未返回 JSON：{output}", output=out[:300])) from exc


class Localizer(Translator):
    """Backward-compatible name for the shared catalog loader.

    Loading ``i18n/cli.json`` as an overlay keeps CLI/Tk sentences such as
    "读取会话…" available even before the generated ``.strings`` files are
    rebuilt.
    """

    def __init__(self, language: str):
        super().__init__(language)


def _format_time(value: Any, localizer: Optional[Translator] = None) -> str:
    """Render an ISO timestamp with the active language's date pattern."""
    if not value:
        return "-"
    if isinstance(value, (int, float)):
        return (localizer or Translator()).dt(value)
    text = str(value).replace("Z", "+00:00")
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(text)
    except ValueError:
        return str(value)
    return (localizer or Translator()).dt(parsed.astimezone())


def _format_number(value: Optional[int], mode: str, language: Optional[str] = None) -> str:
    """Format a number; compact units follow the language (K/M/B vs 万/亿)."""
    if language:
        return format_number(value, compact=(mode == "compact"), language=language)
    # Legacy two-argument behavior (unit tests and callers without a locale).
    if value is None:
        return "-"
    if mode != "compact":
        return f"{value:,}"
    absolute = abs(value)
    sign = "-" if value < 0 else ""
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if absolute >= size:
            scaled = absolute / size
            text = f"{scaled:.0f}" if scaled >= 100 else f"{scaled:.1f}" if scaled >= 10 else f"{scaled:.2f}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return sign + text + suffix
    return f"{value:,}"


def _window_used(usage: Optional[Dict[str, Any]], key: str) -> Optional[float]:
    if not isinstance(usage, dict):
        return None
    value = (usage.get(key) or {}).get("used_percent") if isinstance(usage.get(key), dict) else None
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return None


def _shown_percent(used: Optional[float], mode: str) -> Optional[float]:
    if used is None:
        return None
    return max(0.0, 100.0 - used) if mode == "remaining" else used


class PortableApp:
    def __init__(self, root: "tk.Tk"):
        self.root = root
        self.settings = _load_settings()
        self.localizer = Localizer(self.settings.get("language", "zh-Hans"))
        self.settings["language"] = self.localizer.language
        global _ACTIVE_LANGUAGE
        _ACTIVE_LANGUAGE = self.localizer.language
        self._generation = 0
        self.store = Store(default_paths())
        self.auto_settings = load_auto_settings()
        self.accounts: List[Dict[str, Any]] = []
        self.sessions: List[Dict[str, Any]] = []
        self.status: Dict[str, Any] = {}
        self.doctor: Dict[str, Any] = {}
        self.selected_account: Optional[Dict[str, Any]] = None
        self.selected_session: Optional[str] = None
        self.busy = False
        self._build_style()
        self._build_ui()
        self.show_page("overview")
        self.refresh_overview()
        self.root.after(60_000, self._auto_refresh_accounts)

    def t(self, key: str) -> str:
        return self.localizer(key)

    def tf(self, key: str, *args: Any) -> str:
        return self.localizer.format(key, *args)

    def status_text(self, status: Any) -> str:
        mapping = {
            "ok": "可用",
            "needs_relogin": "需要重新登录",
            "exhausted": "额度已耗尽",
            "error": "异常",
        }
        return self.t(mapping.get(str(status or "ok"), str(status or "-")))

    def level_text(self, level: Any) -> str:
        return self.t({"pass": "通过", "warn": "警告", "fail": "失败"}.get(str(level), str(level)))

    def doctor_check_name(self, check: Any) -> str:
        names = {
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
        key = str(check)
        if key.startswith("profile:"):
            return self.tf("账号：%@", key.split(":", 1)[1])
        return self.t(names.get(key, key))

    def _palette(self) -> Dict[str, str]:
        theme = self.settings.get("theme")
        if theme == "system":
            theme = "dark" if _system_is_dark() else "light"
        if theme == "light":
            return {
                "bg": "#f3f3ee", "panel": "#fdfdfa", "panel2": "#ecece6",
                "text": "#16191a", "muted": "#586166", "line": "#c7c7c0",
                "accent": "#0077a3", "amber": "#b27300", "green": "#238a5b", "red": "#b33232",
            }
        return {
            "bg": "#080a0b", "panel": "#111416", "panel2": "#171a1c",
            "text": "#e8e6df", "muted": "#9ba6ab", "line": "#2a3033",
            "accent": "#18d1ff", "amber": "#ffd429", "green": "#7ae582", "red": "#ff5a5f",
        }

    def _build_style(self) -> None:
        p = self._palette()
        self.root.configure(bg=p["bg"])
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=p["bg"])
        style.configure("Panel.TFrame", background=p["panel"])
        style.configure("TLabel", background=p["bg"], foreground=p["text"])
        style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
        style.configure("Title.TLabel", background=p["bg"], foreground=p["text"], font=("Helvetica", 20, "bold"))
        style.configure("Panel.TLabel", background=p["panel"], foreground=p["text"])
        style.configure("PanelMuted.TLabel", background=p["panel"], foreground=p["muted"])
        style.configure("TButton", padding=(10, 6))
        style.configure("Accent.TButton", foreground=p["bg"], background=p["accent"])
        style.map("Accent.TButton", background=[("active", p["accent"])])
        style.configure("Treeview", background=p["panel"], fieldbackground=p["panel"], foreground=p["text"], rowheight=28)
        style.map("Treeview", background=[("selected", p["panel2"])], foreground=[("selected", p["text"])])
        style.configure("TNotebook", background=p["bg"])
        style.configure("TEntry", fieldbackground=p["panel2"], foreground=p["text"])

    def _build_ui(self) -> None:
        self.root.title(f"Codex Relay {__version__}")
        self.root.geometry("1120x760")
        self.root.minsize(900, 620)
        p = self._palette()

        self.sidebar = tk.Frame(self.root, width=220, bg=p["panel"])
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        tk.Label(self.sidebar, text="CODEX RELAY", bg=p["panel"], fg=p["text"],
                 font=("Helvetica", 16, "bold"), anchor="w").pack(fill="x", padx=16, pady=(18, 2))
        tk.Label(self.sidebar, text=self.t("// 账号与会话接力"), bg=p["panel"], fg=p["accent"],
                 font=("Menlo", 9), anchor="w").pack(fill="x", padx=16, pady=(0, 14))
        self.nav_buttons: Dict[str, tk.Button] = {}
        for key, label in [
            ("overview", "概览"), ("accounts", "账号"), ("sessions", "会话"),
            ("diagnostics", "诊断"), ("settings", "设置"),
        ]:
            button = tk.Button(
                self.sidebar, text=f"  {self.t(label)}", anchor="w", bd=0,
                bg=p["panel"], fg=p["muted"], activebackground=p["panel2"],
                activeforeground=p["text"], font=("Helvetica", 12), padx=12, pady=9,
                command=lambda k=key: self.show_page(k),
            )
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = button
        self.sidebar_footer = tk.Label(self.sidebar, text="", bg=p["panel"], fg=p["muted"],
                                       font=("Menlo", 8), justify="left", anchor="sw")
        self.sidebar_footer.pack(side="bottom", fill="x", padx=16, pady=14)

        self.main = tk.Frame(self.root, bg=p["bg"])
        self.main.pack(side="left", fill="both", expand=True)
        self.status_label = tk.Label(self.main, text="", bg=p["panel2"], fg=p["muted"],
                                     font=("Menlo", 9), anchor="w", padx=12, pady=6)
        self.status_label.pack(side="bottom", fill="x")
        self.content = tk.Frame(self.main, bg=p["bg"])
        self.content.pack(fill="both", expand=True, padx=20, pady=16)

        self.pages: Dict[str, tk.Frame] = {}
        self.pages["overview"] = self._make_overview()
        self.pages["accounts"] = self._make_accounts()
        self.pages["sessions"] = self._make_sessions()
        self.pages["diagnostics"] = self._make_diagnostics()
        self.pages["settings"] = self._make_settings()

    def _clear_content(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()

    def show_page(self, name: str) -> None:
        for existing in self.pages.values():
            try:
                existing.pack_forget()
            except tk.TclError:
                pass
        page = self.pages[name]
        page.pack(fill="both", expand=True)
        for key, button in self.nav_buttons.items():
            p = self._palette()
            button.configure(bg=p["panel2"] if key == name else p["panel"],
                             fg=p["text"] if key == name else p["muted"])
        if name == "overview":
            self.refresh_overview()
        elif name == "accounts":
            self.refresh_accounts()
        elif name == "sessions":
            self.refresh_sessions()
        elif name == "diagnostics":
            self.refresh_doctor()

    def _label(self, parent: "tk.Misc", key: str, muted: bool = False, panel: bool = False,
               font: Optional[Tuple[Any, ...]] = None) -> "tk.Label":
        p = self._palette()
        bg = p["panel"] if panel else p["bg"]
        fg = p["muted"] if muted else p["text"]
        return tk.Label(parent, text=self.t(key), bg=bg, fg=fg, font=font, anchor="w", justify="left")

    def _make_overview(self) -> tk.Frame:
        page = tk.Frame(self.content, bg=self._palette()["bg"])
        p = self._palette()
        self._label(page, "概览", font=("Helvetica", 24, "bold")).pack(fill="x")
        self._label(page, "账号、额度与原会话接力状态", muted=True).pack(fill="x", pady=(0, 12))

        card = tk.Frame(page, bg=p["panel"], highlightthickness=1, highlightbackground=p["line"])
        card.pack(fill="x", pady=6)
        self.overview_account = tk.Label(card, text="-", bg=p["panel"], fg=p["text"],
                                        font=("Helvetica", 17, "bold"), anchor="w", padx=16, pady=12)
        self.overview_account.pack(fill="x")
        self.overview_usage = tk.Label(card, text="-", bg=p["panel"], fg=p["muted"],
                                      font=("Menlo", 11), anchor="w", padx=16, pady=9)
        self.overview_usage.pack(fill="x")
        self.overview_meta = tk.Label(card, text="-", bg=p["panel"], fg=p["muted"],
                                     font=("Menlo", 9), anchor="w", padx=16, pady=9)
        self.overview_meta.pack(fill="x")

        actions = tk.Frame(page, bg=p["bg"])
        actions.pack(fill="x", pady=12)
        self._button(actions, "刷新", self.refresh_overview).pack(side="left", padx=(0, 8))
        self._button(actions, "刷新额度", self.refresh_usage).pack(side="left", padx=(0, 8))
        self._button(actions, "打开账号管理", lambda: self.show_page("accounts")).pack(side="left")

        self.recent = tk.Text(page, height=10, bg=p["panel"], fg=p["text"], insertbackground=p["text"],
                              relief="flat", highlightthickness=1, highlightbackground=p["line"],
                              font=("Menlo", 10), padx=12, pady=10)
        self.recent.pack(fill="both", expand=True, pady=(4, 0))
        self.recent.configure(state="disabled")
        return page

    def _make_accounts(self) -> tk.Frame:
        page = tk.Frame(self.content, bg=self._palette()["bg"])
        p = self._palette()
        header = tk.Frame(page, bg=p["bg"])
        header.pack(fill="x")
        self._label(header, "账号", font=("Helvetica", 22, "bold")).pack(side="left")
        self._button(header, "刷新", self.refresh_accounts).pack(side="right")
        body = tk.Frame(page, bg=p["bg"])
        body.pack(fill="both", expand=True, pady=10)
        left = tk.Frame(body, bg=p["panel"], width=330, highlightthickness=1, highlightbackground=p["line"])
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.account_list = tk.Listbox(left, bg=p["panel"], fg=p["text"], selectbackground=p["panel2"],
                                       selectforeground=p["text"], relief="flat", font=("Helvetica", 12),
                                       activestyle="none")
        self.account_list.pack(fill="both", expand=True, padx=8, pady=8)
        self.account_list.bind("<<ListboxSelect>>", lambda _: self.select_account())
        detail = tk.Frame(body, bg=p["bg"])
        detail.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self.account_avatar = tk.Label(detail, text="", bg=p["panel"], fg=p["accent"],
                                       font=("Helvetica", 28, "bold"), width=4, height=2,
                                       relief="flat", highlightthickness=1, highlightbackground=p["line"])
        self.account_avatar.pack(anchor="w", pady=(0, 8))
        self.avatar_photo = None
        self.account_detail = tk.Text(detail, bg=p["panel"], fg=p["text"], insertbackground=p["text"],
                                      relief="flat", highlightthickness=1, highlightbackground=p["line"],
                                      font=("Menlo", 10), padx=14, pady=12, height=18)
        self.account_detail.pack(fill="both", expand=True)
        buttons = tk.Frame(detail, bg=p["bg"])
        buttons.pack(fill="x", pady=10)
        self._button(buttons, "切换账号并恢复", self.confirm_switch).pack(side="left", padx=(0, 8))
        self._button(buttons, "重命名", self.rename_account).pack(side="left", padx=(0, 8))
        self._button(buttons, "添加账号", self.add_account).pack(side="left", padx=(0, 8))
        self._button(buttons, "更换头像", self.change_avatar).pack(side="left", padx=(0, 8))
        self._button(buttons, "恢复默认头像", self.reset_avatar).pack(side="left", padx=(0, 8))
        self._button(buttons, "仅重启 ChatGPT", self.confirm_restart).pack(side="left")
        return page

    def _make_sessions(self) -> tk.Frame:
        page = tk.Frame(self.content, bg=self._palette()["bg"])
        p = self._palette()
        header = tk.Frame(page, bg=p["bg"])
        header.pack(fill="x")
        self._label(header, "会话", font=("Helvetica", 22, "bold")).pack(side="left")
        self._button(header, "刷新", self.refresh_sessions).pack(side="right")
        body = tk.Frame(page, bg=p["bg"])
        body.pack(fill="both", expand=True, pady=10)
        left = tk.Frame(body, bg=p["panel"], width=330, highlightthickness=1, highlightbackground=p["line"])
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.session_list = tk.Listbox(left, bg=p["panel"], fg=p["text"], selectbackground=p["panel2"],
                                       selectforeground=p["text"], relief="flat", font=("Helvetica", 11),
                                       activestyle="none")
        self.session_list.pack(fill="both", expand=True, padx=8, pady=8)
        self.session_list.bind("<<ListboxSelect>>", lambda _: self.inspect_selected_session())
        self.session_detail = tk.Text(body, bg=p["panel"], fg=p["text"], insertbackground=p["text"],
                                      relief="flat", highlightthickness=1, highlightbackground=p["line"],
                                      font=("Menlo", 10), padx=14, pady=12)
        self.session_detail.pack(side="left", fill="both", expand=True, padx=(16, 0))
        return page

    def _make_diagnostics(self) -> tk.Frame:
        page = tk.Frame(self.content, bg=self._palette()["bg"])
        p = self._palette()
        header = tk.Frame(page, bg=p["bg"])
        header.pack(fill="x")
        self._label(header, "诊断", font=("Helvetica", 22, "bold")).pack(side="left")
        self._button(header, "运行诊断", self.refresh_doctor).pack(side="right")
        self.doctor_text = tk.Text(page, bg=p["panel"], fg=p["text"], insertbackground=p["text"],
                                   relief="flat", highlightthickness=1, highlightbackground=p["line"],
                                   font=("Menlo", 10), padx=14, pady=12)
        self.doctor_text.pack(fill="both", expand=True, pady=10)
        return page

    def _make_settings(self) -> tk.Frame:
        page = tk.Frame(self.content, bg=self._palette()["bg"])
        p = self._palette()
        self._label(page, "设置", font=("Helvetica", 22, "bold")).pack(fill="x")
        self._label(page, "跨平台客户端设置会保存在 ~/.codex-relay/portable-ui.json。", muted=True).pack(fill="x", pady=(0, 10))
        self.quota_var = tk.StringVar(value=self.settings.get("quota_display", "remaining"))
        self.number_var = tk.StringVar(value=self.settings.get("number_format", "full"))
        self.theme_var = tk.StringVar(value=self.settings.get("theme", "dark"))
        self.language_var = tk.StringVar(value=self.settings.get("language", "zh-Hans"))
        for title, variable, options in [
            ("额度显示", self.quota_var, [("remaining", "剩余额度"), ("used", "已使用")]),
            ("数字格式", self.number_var, [("full", "完整数值"), ("compact", "简写 K/M/B")]),
            ("主题", self.theme_var, [("system", "跟随系统"), ("dark", "深色"), ("light", "浅色")]),
            ("语言", self.language_var, [("zh-Hans", "简体中文"), ("zh-Hant", "繁體中文"), ("en", "English"), ("ja", "日本語")]),
        ]:
            card = tk.Frame(page, bg=p["panel"], highlightthickness=1, highlightbackground=p["line"])
            card.pack(fill="x", pady=5)
            tk.Label(card, text=self.t(title), bg=p["panel"], fg=p["text"],
                     font=("Helvetica", 12, "bold"), anchor="w").pack(fill="x", padx=12, pady=(9, 3))
            line = tk.Frame(card, bg=p["panel"])
            line.pack(fill="x", padx=12, pady=(0, 9))
            for value, label in options:
                tk.Radiobutton(line, text=self.t(label), value=value, variable=variable,
                               bg=p["panel"], fg=p["text"], selectcolor=p["panel2"],
                               activebackground=p["panel"], activeforeground=p["text"],
                               font=("Helvetica", 10), command=self.apply_settings).pack(side="left", padx=(0, 14))
        auto = self.auto_settings
        self.auto_enabled_var = tk.BooleanVar(value=bool(auto.get("enabled")))
        self.auto_prompt_var = tk.StringVar(value=str(auto.get("prompt") or self.t("继续")))
        self.auto_remaining_var = tk.StringVar(value=str(auto.get("remaining_threshold", 10)))
        auto_card = tk.Frame(page, bg=p["panel"], highlightthickness=1, highlightbackground=p["line"])
        auto_card.pack(fill="x", pady=5)
        tk.Label(auto_card, text=self.t("自动续跑"), bg=p["panel"], fg=p["text"],
                 font=("Helvetica", 12, "bold"), anchor="w").pack(fill="x", padx=12, pady=(9, 3))
        tk.Checkbutton(auto_card, text=self.t("启用自动续跑"), variable=self.auto_enabled_var,
                       bg=p["panel"], fg=p["text"], selectcolor=p["panel2"],
                       activebackground=p["panel"], activeforeground=p["text"],
                       font=("Helvetica", 10), anchor="w").pack(fill="x", padx=12)
        prompt_line = tk.Frame(auto_card, bg=p["panel"])
        prompt_line.pack(fill="x", padx=12, pady=(6, 3))
        tk.Label(prompt_line, text=self.t("续跑提示词"), bg=p["panel"], fg=p["muted"],
                 font=("Helvetica", 10), anchor="w").pack(side="left")
        tk.Entry(prompt_line, textvariable=self.auto_prompt_var, bg=p["panel2"], fg=p["text"],
                 insertbackground=p["text"], relief="flat", font=("Helvetica", 10)).pack(
                     side="left", fill="x", expand=True, padx=(8, 0))
        remaining_line = tk.Frame(auto_card, bg=p["panel"])
        remaining_line.pack(fill="x", padx=12, pady=(3, 3))
        tk.Label(remaining_line, text=self.t("剩余额度阈值（%）"), bg=p["panel"], fg=p["muted"],
                 font=("Helvetica", 10), anchor="w").pack(side="left")
        tk.Entry(remaining_line, textvariable=self.auto_remaining_var, width=8, bg=p["panel2"], fg=p["text"],
                 insertbackground=p["text"], relief="flat", font=("Helvetica", 10)).pack(side="left", padx=(8, 0))
        tk.Label(auto_card, text=self.t("由 codex-relay auto 守护使用；切换会重启 ChatGPT，需显式确认。"),
                 bg=p["panel"], fg=p["muted"], font=("Helvetica", 9), anchor="w",
                 wraplength=700, justify="left").pack(fill="x", padx=12, pady=(2, 0))
        self._button(auto_card, "保存自动续跑设置", self.save_auto_settings_ui).pack(anchor="w", padx=12, pady=8)

        self._button(page, "立即刷新用量", self.refresh_usage).pack(anchor="w", pady=8)
        self._button(page, "打开凭据仓库", self.open_store).pack(anchor="w")
        return page

    def _button(self, parent: "tk.Misc", key: str, command: Callable[[], None]) -> "tk.Button":
        p = self._palette()
        return tk.Button(parent, text=self.t(key), command=command, bd=0,
                         bg=p["panel2"], fg=p["text"], activebackground=p["accent"],
                         activeforeground=p["bg"], font=("Helvetica", 10), padx=10, pady=6)

    def apply_settings(self) -> None:
        self.settings["quota_display"] = self.quota_var.get()
        self.settings["number_format"] = self.number_var.get()
        self.settings["theme"] = self.theme_var.get()
        language = normalize_language(self.language_var.get()) or "zh-Hans"
        self.settings["language"] = language
        self.localizer.load(language)
        _save_settings(self.settings)
        # Rebuild every page, nav button and dialog source so the new language
        # applies immediately.  Bumping the generation makes callbacks from
        # in-flight worker threads no-ops instead of touching destroyed widgets.
        self._generation += 1
        self.busy = False
        global _ACTIVE_LANGUAGE
        _ACTIVE_LANGUAGE = self.localizer.language
        for child in self.root.winfo_children():
            child.destroy()
        self._build_style()
        self._build_ui()
        self.show_page("settings")
        if hasattr(self, "status_label"):
            self.status_label.configure(text=self.t("就绪"), fg=self._palette()["muted"])

    def save_auto_settings_ui(self) -> None:
        try:
            remaining = float(self.auto_remaining_var.get().strip())
        except ValueError:
            messagebox.showerror("Codex Relay", self.t("剩余额度阈值必须是数字"))
            return
        if not 0 <= remaining <= 100:
            messagebox.showerror("Codex Relay", self.t("剩余额度阈值必须在 0 到 100 之间"))
            return
        prompt = self.auto_prompt_var.get().strip()
        save_auto_settings(
            {
                "enabled": bool(self.auto_enabled_var.get()),
                "prompt": prompt,
                "remaining_threshold": remaining,
            }
        )
        self.auto_settings = load_auto_settings()
        self.status_label.configure(text=self.t("自动续跑设置已保存"), fg=self._palette()["accent"])

    def open_store(self) -> None:
        path = self.store.paths.relay_home
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror("Codex Relay", f"{self.t('打开目录失败')}: {exc}")

    def set_busy(self, label: Optional[str] = None) -> None:
        label = label or self.t("处理中…")
        self.busy = True
        self.status_label.configure(text=label, fg=self._palette()["amber"])

    def clear_busy(self) -> None:
        self.busy = False
        self.status_label.configure(text=self.t("就绪"), fg=self._palette()["muted"])

    def _is_current(self, generation: int) -> bool:
        return generation == self._generation and hasattr(self, "status_label")

    def async_call(self, label: str, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        if self.busy:
            return
        self.set_busy(label)
        generation = self._generation

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - shown to the user
                message = str(exc)
                self.root.after(0, lambda m=message: self.fail(m, generation))
                return
            self.root.after(0, lambda: self.finish(result, done, generation))

        threading.Thread(target=runner, daemon=True).start()

    def finish(self, result: Any, done: Callable[[Any], None], generation: Optional[int] = None) -> None:
        if generation is not None and not self._is_current(generation):
            return
        self.clear_busy()
        done(result)

    def fail(self, message: str, generation: Optional[int] = None) -> None:
        if generation is not None and not self._is_current(generation):
            return
        self.clear_busy()
        messagebox.showerror("Codex Relay", message)

    def refresh_overview(self) -> None:
        def work() -> Tuple[Any, Any, Any]:
            accounts = _cli_json(["list", "--json"], timeout=30)
            status = _cli_json(["status", "--json"], timeout=30)
            sessions = _cli_json(["sessions", "--json", "--limit", "6"], timeout=30)
            return accounts, status, sessions
        def done(result: Tuple[Any, Any, Any]) -> None:
            accounts, status, sessions = result
            self.accounts = accounts.get("profiles", [])
            self.sessions = sessions.get("threads", [])
            self.status = status
            active = next((a for a in self.accounts if a.get("active")), None)
            if active:
                self.overview_account.configure(text=active.get("display_name") or active.get("name") or active.get("alias") or "-")
                usage = active.get("usage") or {}
                self.overview_usage.configure(text=self.usage_text(usage))
            else:
                self.overview_account.configure(text=self.t("未登录 Codex"))
                self.overview_usage.configure(text="-")
            count = _format_number(len(self.sessions), "full", self.localizer.language)
            self.overview_meta.configure(text=f"{self.t('最近会话')}: {count} · {_format_time(status.get('checked_at'), self.localizer)}")
            self.recent.configure(state="normal")
            self.recent.delete("1.0", "end")
            for session in self.sessions:
                title = self.localizer.external(session.get("display_title") or session.get("title") or session.get("id"))
                self.recent.insert("end", f"{title}\n")
                updated = _format_time(session.get("updated_at") or session.get("updated_local"), self.localizer)
                self.recent.insert("end", f"  {session.get('creator_alias') or '-'} · {updated}\n")
            self.recent.configure(state="disabled")
            self.sidebar_footer.configure(text=f"{self.t('当前账号')}: {self.overview_account.cget('text')}\nPY {sys.version.split()[0]}")
        self.async_call(self.t("读取概览…"), work, done)

    def usage_text(self, usage: Dict[str, Any]) -> str:
        mode = self.settings.get("quota_display", "remaining")
        def one(key: str, label: str) -> str:
            used = _window_used(usage, key)
            shown = _shown_percent(used, mode)
            if shown is None:
                return f"{label}: -"
            percent = self.localizer.pct(shown, 0)
            return f"{label}: {percent} ({self.t('剩余') if mode == 'remaining' else self.t('已用')})"
        return "  ·  ".join([one("primary", self.t("5 小时")), one("secondary", self.t("1 周"))])

    def _set_accounts(self, accounts: List[Dict[str, Any]]) -> None:
        """Replace the account list while preserving the selected account."""
        previous_id = (self.selected_account or {}).get("id")
        previous_alias = (self.selected_account or {}).get("alias")
        self.accounts = accounts
        self.account_list.delete(0, "end")
        for account in self.accounts:
            name = account.get("display_name") or account.get("name") or account.get("alias") or "-"
            self.account_list.insert("end", f"{name}  ·  {account.get('email') or '-'}")
        if not self.accounts:
            self.selected_account = None
            return
        selected_index = 0
        for index, account in enumerate(self.accounts):
            if previous_id and account.get("id") == previous_id:
                selected_index = index
                break
            if not previous_id and previous_alias and account.get("alias") == previous_alias:
                selected_index = index
                break
        self.account_list.selection_clear(0, "end")
        self.account_list.selection_set(selected_index)
        self.select_account()

    def refresh_accounts(self) -> None:
        def work() -> Any:
            # Refresh every profile, not just the selected one. The previous
            # implementation read the cached list, so an inactive account's
            # reset quota stayed stale until it was clicked.
            return _cli_json(["list", "--refresh", "--json"], timeout=180)
        def done(result: Any) -> None:
            self._set_accounts(result.get("profiles", []))
        self.async_call(self.t("读取账号…"), work, done)

    def _auto_refresh_accounts(self) -> None:
        if not self.busy:
            self.refresh_accounts()
        self.root.after(60_000, self._auto_refresh_accounts)

    def select_account(self) -> None:
        selection = self.account_list.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self.accounts):
            return
        self.selected_account = self.accounts[index]
        account = self.selected_account
        usage = account.get("usage") or {}
        text = (
            f"{self.t('账号信息')}\n\n"
            f"{self.t('显示名称')}: {account.get('display_name') or account.get('name') or '-'}\n"
            f"{self.t('内部别名')}: {account.get('alias') or '-'}\n"
            f"{self.t('邮箱')}: {account.get('email') or '-'}\n"
            f"{self.t('计划')}: {account.get('plan') or '-'}\n"
            f"{self.t('状态')}: {self.status_text(account.get('status'))}\n"
            f"{self.t('账号 ID')}: {account.get('account_id') or '-'}\n"
            f"{self.t('最近校验')}: {_format_time(account.get('last_verified_at'), self.localizer)}\n\n"
            f"{self.t('额度')}: {self.usage_text(usage)}"
        )
        self.update_avatar_label(account)
        self.account_detail.configure(state="normal")
        self.account_detail.delete("1.0", "end")
        self.account_detail.insert("end", text)
        self.account_detail.configure(state="disabled")

    def update_avatar_label(self, account: Dict[str, Any]) -> None:
        if not hasattr(self, "account_avatar"):
            return
        self.avatar_photo = None
        initial = (account.get("display_name") or account.get("name") or account.get("alias") or "?")[0]
        path = ""
        try:
            values = json.loads(_avatars_path().read_text(encoding="utf-8")) if _avatars_path().exists() else {}
            path = values.get(str(account.get("account_id") or account.get("id")), "")
        except (OSError, ValueError):
            path = ""
        if path and Path(path).exists():
            try:
                image = tk.PhotoImage(file=path)
                if image.width() > 72 or image.height() > 72:
                    factor = max(1, int(max(image.width(), image.height()) / 72))
                    image = image.subsample(factor, factor)
                self.avatar_photo = image
                self.account_avatar.configure(image=image, text="", width=72, height=72)
                return
            except (tk.TclError, OSError):
                pass
        self.account_avatar.configure(image="", text=initial, width=4, height=2)

    def confirm_switch(self) -> None:
        account = self.selected_account
        if not account:
            return
        code, out, err = _run_cli(["version", "--json"], timeout=20)
        desktop_installed = False
        if code == 0:
            try:
                desktop_installed = bool(json.loads(out).get("desktop", {}).get("path"))
            except ValueError:
                pass
        if desktop_installed:
            message = self.t("切换到该账号并重启 ChatGPT，随后恢复最近会话？")
            confirm_text = self.t("我确认现在退出并重启 ChatGPT")
        else:
            message = self.t("切换将替换 auth.json 并供新的 CLI 使用；不会重启 ChatGPT。")
            confirm_text = self.t("我确认切换账号")
        if not ConfirmDialog(self.root, self.t("切换账号"), message, self.t, confirm_text).ask():
            return
        args = ["switch", account.get("alias"), "--thread", "last", "--json"]
        if desktop_installed:
            args.extend(["--desktop", "--allow-chatgpt-restart"])
        else:
            args.append("--none")
        self.async_call(self.t("切换账号…"), lambda: _run_cli(args, timeout=180), lambda result: self.after_switch(result))

    def after_switch(self, result: Tuple[int, str, str]) -> None:
        code, out, err = result
        if code != 0:
            self.fail(err or out or self.tf("CLI 退出码 %d", code))
            return
        messagebox.showinfo("Codex Relay", self.t("切换完成"))
        self.refresh_accounts()

    def confirm_restart(self) -> None:
        code, out, err = _run_cli(["version", "--json"], timeout=20)
        desktop_installed = False
        if code == 0:
            try:
                desktop_installed = bool(json.loads(out).get("desktop", {}).get("path"))
            except ValueError:
                pass
        if not desktop_installed:
            messagebox.showinfo("Codex Relay", self.t("未检测到桌面 App；请使用 CLI 或切换账号。"))
            return
        if not ConfirmDialog(
            self.root,
            self.t("仅重启 ChatGPT"),
            self.t("仅重启 ChatGPT 并恢复最近会话，不切换账号？"),
            self.t,
            self.t("我确认现在退出并重启 ChatGPT"),
        ).ask():
            return
        self.async_call(
            self.t("重启 ChatGPT…"),
            lambda: _run_cli(["restart", "--thread", "last", "--allow-chatgpt-restart", "--json"], timeout=180),
            self.after_restart,
        )

    def after_restart(self, result: Tuple[int, str, str]) -> None:
        code, out, err = result
        if code != 0:
            self.fail(err or out or self.tf("CLI 退出码 %d", code))
            return
        messagebox.showinfo("Codex Relay", self.t("重启完成"))

    def rename_account(self) -> None:
        account = self.selected_account
        if not account:
            return
        name = simpledialog.askstring("Codex Relay", self.t("显示名称"), initialvalue=account.get("display_name") or account.get("name") or "")
        if not name:
            return
        self.async_call(
            self.t("保存名称…"),
            lambda: _run_cli(["label", account.get("alias"), name, "--json"], timeout=30),
            lambda result: self.refresh_accounts() if result[0] == 0 else self.fail(result[2] or result[1]),
        )

    def add_account(self) -> None:
        alias = simpledialog.askstring("Codex Relay", self.t("账号别名（可留空自动生成）"), initialvalue="")
        if alias is None:
            return
        alias = alias.strip() or f"account{len(self.accounts) + 1}"
        self.async_call(
            self.t("启动登录…"),
            lambda: _run_cli(["add", alias], timeout=660),
            lambda result: self.refresh_accounts() if result[0] == 0 else self.fail(result[2] or result[1]),
        )

    def change_avatar(self) -> None:
        account = self.selected_account
        if not account:
            return
        path = filedialog.askopenfilename(
            title=self.t("选择账号头像"),
            filetypes=[(self.t("图片文件"), "*.png *.jpg *.jpeg *.gif"), (self.t("所有文件"), "*.*")],
        )
        if not path:
            return
        avatars = _avatars_path()
        try:
            data = json.loads(avatars.read_text(encoding="utf-8")) if avatars.exists() else {}
        except ValueError:
            data = {}
        target_dir = self.store.paths.relay_home / "avatars"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{account.get('account_id') or account.get('id')}-{os.getpid()}{Path(path).suffix.lower()}"
        try:
            shutil.copy2(path, target)
            data[str(account.get("account_id") or account.get("id"))] = str(target)
            avatars.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.update_avatar_label(account)
            messagebox.showinfo("Codex Relay", self.t("头像已更新"))
        except OSError as exc:
            messagebox.showerror("Codex Relay", f"{self.t('头像更新失败')}: {exc}")

    def reset_avatar(self) -> None:
        account = self.selected_account
        if not account:
            return
        key = str(account.get("account_id") or account.get("id"))
        path = _avatars_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            data = {}
        old = data.pop(key, None)
        try:
            if old:
                Path(old).unlink(missing_ok=True)
            if data:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                path.unlink(missing_ok=True)
            self.update_avatar_label(account)
        except OSError as exc:
            messagebox.showerror("Codex Relay", f"{self.t('头像更新失败')}: {exc}")

    def refresh_sessions(self) -> None:
        def work() -> Any:
            return _cli_json(["sessions", "--json", "--limit", "80"], timeout=60)
        def done(result: Any) -> None:
            self.sessions = result.get("threads", [])
            self.session_list.delete(0, "end")
            for session in self.sessions:
                title = self.localizer.external(session.get("display_title") or session.get("title") or session.get("id"))
                self.session_list.insert("end", title)
        self.async_call(self.t("读取会话…"), work, done)

    def inspect_selected_session(self) -> None:
        selection = self.session_list.curselection()
        if not selection:
            return
        session = self.sessions[selection[0]]
        thread_id = session.get("id")
        self.async_call(
            self.t("分析会话缓存…"),
            lambda: _cli_json(["inspect", thread_id, "--json"], timeout=180),
            lambda result: self.show_inspection(result),
        )

    def show_inspection(self, result: Dict[str, Any]) -> None:
        metrics = result.get("metrics") or {}
        totals = metrics.get("totals") or {}
        mode = self.settings.get("number_format", "full")
        language = self.localizer.language

        def num(value: Any) -> str:
            return _format_number(value, mode, language)

        lines = [
            f"{self.t('会话信息')}",
            f"{self.t('Thread ID')}: {result.get('thread', {}).get('id', '-')}",
            f"{self.t('累计 Token')}: {num(totals.get('total_tokens'))}",
            f"{self.t('输入')}: {num(totals.get('input_tokens'))}",
            f"{self.t('缓存')}: {num(totals.get('cached_input_tokens'))} ({self.localizer.pct(totals.get('cache_ratio', 0), 1)})",
            f"{self.t('输出')}: {num(totals.get('output_tokens'))}",
            f"{self.t('推理')}: {num(totals.get('reasoning_output_tokens'))}",
            f"{self.t('加密推理项')}: {num(metrics.get('reasoning_encrypted_items', 0))}",
            f"{self.t('压缩检查点')}: {num(len(metrics.get('compactions') or []))}",
        ]
        self.session_detail.delete("1.0", "end")
        self.session_detail.insert("end", "\n".join(lines))

    def refresh_usage(self) -> None:
        def work() -> Any:
            # `usage --all` refreshes every profile. `usage` alone targets only
            # the active account and was the source of the stale-list bug.
            return _cli_json(["usage", "--all", "--json"], timeout=240)

        def done(result: Any) -> None:
            if not result:
                return
            self._set_accounts(result.get("profiles", []))
            self.refresh_overview()

        self.async_call(self.t("刷新额度…"), work, done)

    def refresh_doctor(self) -> None:
        # `doctor --json` exits 1 when a check fails but still prints the
        # report; accept both codes so the diagnostics page can show it.
        self.async_call(
            self.t("运行诊断…"),
            lambda: _cli_json(["doctor", "--json"], timeout=240, allow_codes=(0, 1)),
            self.show_doctor,
        )

    def show_doctor(self, result: Dict[str, Any]) -> None:
        self.doctor = result
        self.doctor_text.delete("1.0", "end")
        for check in result.get("checks", []):
            name = check.get("title") or self.doctor_check_name(check.get("check"))
            self.doctor_text.insert(
                "end",
                f"[{self.level_text(check.get('level'))}] {name}: {check.get('message')}\n",
            )
        self.doctor_text.insert("end", f"\n{self.tf('%d 项失败 · %d 项警告', result.get('failures', 0), result.get('warnings', 0))}\n")


class ConfirmDialog:
    def __init__(self, parent: "tk.Misc", title: str, message: str, translate: Callable[[str], str], confirm_text: Optional[str] = None):
        self.accepted = False
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.transient(parent)
        self.top.grab_set()
        p = _palette_static()
        tk.Label(self.top, text=message, bg=p["bg"], fg=p["text"], wraplength=420,
                 justify="left", padx=18, pady=18).pack(fill="x")
        self.var = tk.BooleanVar(value=False)
        tk.Checkbutton(self.top, text=confirm_text or (translate("我确认现在退出并重启 ChatGPT") if translate else "I confirm"),
                       variable=self.var, bg=p["bg"], fg=p["text"], selectcolor=p["panel2"]).pack(anchor="w", padx=18)
        buttons = tk.Frame(self.top, bg=p["bg"])
        buttons.pack(fill="x", padx=18, pady=14)
        tk.Button(buttons, text=translate("取消") if translate else "Cancel", command=self.top.destroy).pack(side="right")
        tk.Button(buttons, text=translate("确认并继续") if translate else "Continue",
                  command=self._accept, state="disabled").pack(side="right", padx=8)
        self.button = buttons.winfo_children()[1]
        self.var.trace_add("write", lambda *_: self.button.configure(state="normal" if self.var.get() else "disabled"))
        self.top.wait_window()

    def _accept(self) -> None:
        self.accepted = True
        self.top.destroy()

    def ask(self) -> bool:
        return self.accepted


def _system_is_dark() -> bool:
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=3,
            )
            return result.returncode == 0 and "dark" in result.stdout.lower()
        except (OSError, subprocess.TimeoutExpired):
            return False
    if os.name == "nt":
        try:
            import winreg  # type: ignore[import-not-found]
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            try:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(value) == 0
            finally:
                winreg.CloseKey(key)
        except (OSError, ValueError, ImportError):
            return False
    value = (os.environ.get("GTK_THEME", "") + os.environ.get("COLORFGBG", "")).lower()
    return "dark" in value


def _palette_static() -> Dict[str, str]:
    return {
        "bg": "#111416", "panel": "#171a1c", "panel2": "#202629",
        "text": "#e8e6df", "muted": "#9ba6ab", "accent": "#18d1ff",
    }


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--headless-cli"]:
        from .cli import main as cli_main
        return cli_main(argv[1:])
    if "--self-test" in argv:
        settings = _load_settings()
        localizer = Localizer(settings.get("language", "zh-Hans"))
        code, out, err = _run_cli(["version", "--json"], timeout=30)
        print(f"portable-gui {__version__} language={settings.get('language')} theme={settings.get('theme')}")
        print(f"cli_exit={code} cli_json={out[:200].strip()}")
        print(f"localized={localizer('设置')}")
        return 0 if code == 0 else 1
    startup_localizer = Localizer(_load_settings().get("language", "zh-Hans"))
    if tk is None:
        print(startup_localizer("Tkinter 不可用"), file=sys.stderr)
        return 2
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(startup_localizer.format("无法打开图形界面（无桌面环境或缺少显示服务）：%@", exc), file=sys.stderr)
        print(startup_localizer("请使用纯 CLI：codex-relay status / list / sessions / usage / switch"), file=sys.stderr)
        return 2
    PortableApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
