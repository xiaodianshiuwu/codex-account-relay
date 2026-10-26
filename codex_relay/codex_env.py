from __future__ import annotations

import csv
import io
import json
import os
import plistlib
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .util import RelayError, run_capture, sqlite_uri, version_of, which_codex


IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def _desktop_candidates() -> List[Path]:
    if IS_MACOS:
        return [
            Path("/Applications/ChatGPT.app"),
            Path("/Applications/Codex.app"),
            Path.home() / "Applications/ChatGPT.app",
            Path.home() / "Applications/Codex.app",
        ]
    if IS_WINDOWS:
        candidates: List[Path] = []
        for env_name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            root = os.environ.get(env_name)
            if not root:
                continue
            base = Path(root)
            for name in ("ChatGPT", "Codex"):
                candidates.extend(
                    [
                        base / "Programs" / name / f"{name}.exe",
                        base / name / f"{name}.exe",
                        base / "Programs" / name / f"{name.lower()}.exe",
                    ]
                )
        # Do not resolve generic `Codex.exe` from PATH: Windows is
        # case-insensitive and that name is also used by the Codex CLI. Only
        # explicit desktop install locations above are trusted for Codex.
        for executable in ("ChatGPT.exe", "ChatGPT"):
            found = shutil.which(executable)
            if found:
                candidates.append(Path(found))
        return candidates
    # Linux does not currently ship an official ChatGPT desktop app on every
    # distribution. Support common local/AppImage-style paths when present.
    return [
        Path("/opt/ChatGPT/ChatGPT"),
        Path("/opt/chatgpt/chatgpt"),
        Path.home() / "Applications" / "ChatGPT.AppImage",
        Path("/usr/bin/chatgpt"),
    ]


def _refuse_in_test_mode(operation: str) -> None:
    """Hard guard: tests must never quit, launch, or open the real desktop app."""
    if os.environ.get("CODEX_RELAY_TEST_MODE") == "1":
        raise RelayError(
            f"test mode: refusing to {operation} the real ChatGPT desktop app. "
            "Mock the codex_env function in this test."
        )


def _require_process_control(operation: str) -> None:
    """Second hard guard: process control is opt-in for every invocation."""
    if os.environ.get("CODEX_RELAY_ALLOW_PROCESS_CONTROL") != "1":
        raise RelayError(
            f"已禁止擅自{CJK_ACTION.get(operation, operation)} ChatGPT。"
            "只有在你明确确认后，才允许执行重启/退出；"
            "CLI 请加 --allow-chatgpt-restart，GUI 请在确认框勾选。"
        )


CJK_ACTION = {
    "quit": "退出",
    "launch": "启动",
    "open": "打开",
    "stop daemon": "停止",
}


@contextmanager
def process_control_allowed(enabled: bool):
    """Scope the process-control permission to one explicit switch/restart."""
    previous = os.environ.get("CODEX_RELAY_ALLOW_PROCESS_CONTROL")
    if enabled:
        os.environ["CODEX_RELAY_ALLOW_PROCESS_CONTROL"] = "1"
    else:
        os.environ.pop("CODEX_RELAY_ALLOW_PROCESS_CONTROL", None)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CODEX_RELAY_ALLOW_PROCESS_CONTROL", None)
        else:
            os.environ["CODEX_RELAY_ALLOW_PROCESS_CONTROL"] = previous


def _child_process_env() -> Dict[str, str]:
    """Environment for GUI-launching subprocesses.

    The permission flag is intentionally process-local: if `open` or a
    LaunchServices request inherits it, the launched ChatGPT process (and its
    embedded app-server) keeps the flag for its entire lifetime. Strip both
    relay-only flags before spawning anything that can become the GUI.
    """
    env = dict(os.environ)
    env.pop("CODEX_RELAY_ALLOW_PROCESS_CONTROL", None)
    env.pop("CODEX_RELAY_TEST_MODE", None)
    return env


@dataclass
class DesktopInfo:
    path: Optional[Path]
    bundle_id: Optional[str]
    display_name: Optional[str]
    version: Optional[str]
    embedded_cli: Optional[Path]

    @property
    def installed(self) -> bool:
        return bool(self.path and self.path.exists())

    @property
    def embedded_cli_version(self) -> Optional[str]:
        if not self.embedded_cli or not self.embedded_cli.exists():
            return None
        return version_of(str(self.embedded_cli))


@dataclass
class ProcessInfo:
    pid: int
    ppid: int
    started: Optional[datetime]
    command: str
    role: str

    @property
    def short_command(self) -> str:
        return " ".join(self.command.split())


def discover_desktop() -> DesktopInfo:
    for candidate in _desktop_candidates():
        if IS_MACOS:
            info_plist = candidate / "Contents/Info.plist"
            if not info_plist.exists():
                continue
            try:
                with info_plist.open("rb") as handle:
                    plist = plistlib.load(handle)
            except (OSError, ValueError):
                plist = {}
            bundle_id = plist.get("CFBundleIdentifier")
            if bundle_id != "com.openai.codex":
                continue
            display = plist.get("CFBundleDisplayName") or plist.get("CFBundleName")
            version = plist.get("CFBundleShortVersionString")
            embedded = next((candidate / item for item in (
                "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
                "Contents/Resources/codex") if (candidate / item).is_file()), None)
            return DesktopInfo(
                path=candidate,
                bundle_id=bundle_id,
                display_name=display,
                version=version,
                embedded_cli=embedded,
            )
        if candidate.exists() and candidate.is_file():
            return DesktopInfo(
                path=candidate,
                bundle_id=None,
                display_name=candidate.stem,
                version=None,
                embedded_cli=None,
            )
    return DesktopInfo(None, None, None, None, None)


def _parse_lstart(value: str) -> Optional[datetime]:
    value = " ".join(value.split())
    for fmt in ("%a %b %d %H:%M:%S %Y", "%a %b %e %H:%M:%S %Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _role_for_command(command: str) -> str:
    # Classify by executable name rather than substrings. Commands differ
    # across macOS, Windows and Linux, especially around .exe and path slashes.
    normalized = command.replace("\\", "/") if command else ""
    first = normalized.split(None, 1)[0] if normalized else ""
    first = first.strip("'\"")
    first_lower = first.lower()
    name = Path(first).name.lower()
    lowered = normalized.lower()
    # These are helper/runtime processes owned by the desktop app or by the
    # relay itself; they are not alternate writers of auth.json. Path matching
    # must happen before the generic `codex` executable check because several
    # paths contain spaces and therefore produce a first token ending in
    # "Codex".
    if (
        "/.codex/computer-use/" in lowered
        or "/.codex/plugins/" in lowered
        or "chatgpt for chrome" in lowered
        or "/chatgpt.app/contents/frameworks/" in lowered
        or "codex framework.framework" in lowered
        or ("/codex relay" in lowered and (".app/" in lowered or "/contents/macos/" in lowered))
        or name.startswith("codexrelay")
        or "codex helper" in lowered
        or name in ("ssh", "ssh.exe")
    ):
        return "other"
    if (
        "chatgpt.exe" in lowered
        or first == "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"
        or first == "/usr/bin/chatgpt"
        or first_lower.startswith("/opt/chatgpt/")
        or first_lower.endswith("/chatgpt.appimage")
        or first_lower.endswith("/macos/chatgpt")
    ):
        return "desktop-main"
    if "/applications/codex.app/contents/macos/codex" in lowered:
        return "desktop-main"
    if "codexcli.app/contents/macos/codex" in lowered or "codex.exe" in lowered or name in ("codex.exe", "codex"):
        if " app-server daemon" in lowered:
            return "daemon"
        if " app-server" in lowered:
            return "desktop-app-server" if "codexcli.app" in lowered else "cli-app-server"
        if " exec-server" in lowered:
            return "desktop-exec-server"
        if " app-server daemon" in lowered:
            return "daemon"
        return "desktop-codex" if "codexcli.app" in lowered else "cli"
    if name in ("codex-code-mode-host", "codex-code-mode-host.exe"):
        return "helper"
    if re.search(r"(^|/)codex( |$)", first):
        return "cli"
    return "other"


def _windows_process_table() -> List[ProcessInfo]:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return []
    script = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
        "$OutputEncoding=[Text.UTF8Encoding]::new(); "
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    try:
        data = json.loads((result.stdout or "[]").lstrip("\ufeff"))
    except ValueError:
        return []
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    processes: List[ProcessInfo] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        command = str(item.get("CommandLine") or "")
        if not command:
            continue
        try:
            pid = int(item.get("ProcessId"))
            ppid = int(item.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            continue
        role = _role_for_command(command)
        if role == "other":
            continue
        processes.append(
            ProcessInfo(pid=pid, ppid=ppid, started=None, command=command, role=role)
        )
    return processes


def _windows_tasklist_processes() -> List[ProcessInfo]:
    """Fallback when PowerShell/CIM is unavailable.

    Tasklist does not expose command lines, but it is enough to detect a
    running ChatGPT desktop process so desktop switches do not start a second
    instance. Command-line based roles remain the primary source.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    processes: List[ProcessInfo] = []
    for row in csv.reader(io.StringIO(result.stdout or "")):
        if len(row) < 2:
            continue
        image = row[0].strip().lower()
        try:
            pid = int(row[1].strip())
        except ValueError:
            continue
        if image == "chatgpt.exe":
            processes.append(
                ProcessInfo(pid=pid, ppid=0, started=None, command=row[0], role="desktop-main")
            )
        elif image == "codex.exe":
            # Without a command line this could be either the CLI or a desktop
            # app. Mark it as a CLI writer so a switch refuses rather than
            # silently racing an unknown process for auth.json.
            processes.append(
                ProcessInfo(pid=pid, ppid=0, started=None, command=row[0], role="cli")
            )
    return processes


def process_table() -> List[ProcessInfo]:
    if IS_WINDOWS:
        processes = _windows_process_table()
        known_pids = {p.pid for p in processes}
        for proc in _windows_tasklist_processes():
            if proc.pid not in known_pids:
                processes.append(proc)
        return processes
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,lstart=,command="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    processes: List[ProcessInfo] = []
    for line in result.stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        pid_s, ppid_s, dow, mon, day, clock, year, command = parts
        try:
            pid = int(pid_s)
            ppid = int(ppid_s)
        except ValueError:
            continue
        started = _parse_lstart(f"{dow} {mon} {day} {clock} {year}")
        if "grep" in command and "codex" not in command:
            continue
        role = _role_for_command(command)
        if role == "other":
            continue
        processes.append(ProcessInfo(pid=pid, ppid=ppid, started=started, command=command, role=role))
    return processes


def codex_processes() -> List[ProcessInfo]:
    return [p for p in process_table() if p.role != "other"]


def desktop_main_processes() -> List[ProcessInfo]:
    return [p for p in codex_processes() if p.role == "desktop-main"]


def desktop_app_server_processes() -> List[ProcessInfo]:
    return [p for p in codex_processes() if p.role == "desktop-app-server"]


def desktop_bundle_processes() -> List[ProcessInfo]:
    return [
        p
        for p in codex_processes()
        if p.role.startswith("desktop") or p.role == "helper"
    ]


def app_running() -> bool:
    if desktop_main_processes():
        return True
    if IS_MACOS:
        try:
            result = run_capture(["lsappinfo", "find", "bundleid=com.openai.codex"], timeout=5)
            return result.returncode == 0 and bool(result.stdout.strip())
        except RelayError:
            return False
    return False


def wait_until(predicate, timeout: float, interval: Optional[float] = None) -> bool:
    # Enumerating processes through PowerShell/CIM is much slower than ps.
    # Avoid spawning a new PowerShell every 200 ms on Windows.
    if interval is None:
        interval = 1.0 if IS_WINDOWS else 0.2
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _terminate_pid(pid: int, force: bool = False) -> None:
    if IS_WINDOWS:
        args = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            args.append("/F")
        run_capture(args, timeout=10)
        return
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def quit_desktop(timeout: float = 20.0, force: bool = False) -> List[str]:
    _refuse_in_test_mode("quit")
    _require_process_control("quit")
    notes: List[str] = []
    if not desktop_bundle_processes() and not app_running():
        return notes
    if IS_MACOS:
        try:
            result = run_capture(
                ["osascript", "-e", 'tell application id "com.openai.codex" to quit'],
                timeout=10,
                env=_child_process_env(),
            )
            if result.returncode != 0:
                notes.append("AppleScript 退出失败，尝试发送终止信号")
        except RelayError as exc:
            notes.append(f"AppleScript 退出超时：{exc}")

    def gone() -> bool:
        return not desktop_bundle_processes()

    if wait_until(gone, timeout=timeout):
        return notes

    for proc in desktop_bundle_processes():
        try:
            _terminate_pid(proc.pid, force=False)
            notes.append(f"已向桌面 Codex 进程 {proc.pid} 发送终止请求")
        except (ProcessLookupError, OSError, RelayError):
            pass
    if wait_until(gone, timeout=10):
        return notes

    if not force:
        remaining = ", ".join(str(p.pid) for p in desktop_bundle_processes())
        raise RelayError(f"ChatGPT App 未能安全退出（剩余进程：{remaining}）。使用 --force 可强制结束。")
    for proc in desktop_bundle_processes():
        try:
            _terminate_pid(proc.pid, force=True)
            notes.append(f"已强制结束进程 {proc.pid}")
        except (ProcessLookupError, OSError, RelayError):
            pass
    if not wait_until(gone, timeout=5):
        raise RelayError("强制结束后仍有 Codex 进程运行")
    return notes


def launch_desktop(desktop: Optional[DesktopInfo] = None) -> None:
    _refuse_in_test_mode("launch")
    _require_process_control("launch")
    desktop = desktop or discover_desktop()
    if not desktop.installed:
        raise RelayError("未找到 ChatGPT/Codex 桌面 App")
    if IS_MACOS:
        if desktop.bundle_id:
            result = run_capture(["open", "-b", desktop.bundle_id], timeout=10, env=_child_process_env())
            if result.returncode == 0:
                return
        result = run_capture(["open", str(desktop.path)], timeout=10, env=_child_process_env())
        if result.returncode != 0:
            raise RelayError(f"启动桌面 App 失败：{result.stderr.strip() or result.stdout.strip()}")
        return
    if desktop.path is None:
        raise RelayError("未找到可启动的桌面 App")
    try:
        subprocess.Popen(
            [str(desktop.path)],
            cwd=str(desktop.path.parent),
            env=_child_process_env(),
            close_fds=True,
        )
    except OSError as exc:
        raise RelayError(f"启动桌面 App 失败：{exc}") from exc


def wait_for_desktop_app_server(
    timeout: float = 40.0,
    not_before: Optional[datetime] = None,
    stable_for: float = 2.0,
) -> Optional[ProcessInfo]:
    """Wait for a non-ephemeral desktop app-server process.

    The Electron app can start a short-lived app-server and replace it while the
    renderer initializes. Returning the first PID caused deep links to be sent
    before the UI was ready.
    """
    deadline = time.monotonic() + timeout
    candidate_pid: Optional[int] = None
    candidate_since = 0.0
    while time.monotonic() < deadline:
        servers = desktop_app_server_processes()
        if not_before is not None:
            servers = [
                proc
                for proc in servers
                if proc.started is None or proc.started >= not_before.replace(microsecond=0)
            ]
        if servers:
            latest = max(servers, key=lambda p: p.pid)
            if latest.pid != candidate_pid:
                candidate_pid = latest.pid
                candidate_since = time.monotonic()
            elif time.monotonic() - candidate_since >= stable_for:
                if any(p.pid == candidate_pid for p in desktop_app_server_processes()):
                    return latest
        else:
            candidate_pid = None
        time.sleep(1.0 if IS_WINDOWS else 0.3)
    return None


def wait_for_desktop_renderer(launch_epoch: float, timeout: float = 30.0) -> bool:
    """Wait until the renderer has issued an app-server RPC after launch.

    The desktop app logs `app-server request: config/read` / `model/list` at
    TRACE level. Those rows are a reliable readiness signal that the UI can
    process a codex:// deep link. If the log DB is unavailable, fall back to a
    short fixed delay.
    """
    log_db = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "logs_2.sqlite"
    if not log_db.exists():
        time.sleep(3.0)
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            connection = sqlite3.connect(sqlite_uri(log_db), uri=True, timeout=3)
            try:
                row = connection.execute(
                    "SELECT COUNT(*) FROM logs WHERE ts >= ? AND "
                    "(target LIKE '%message_processor%' OR feedback_log_body LIKE 'app-server request:%')",
                    (int(launch_epoch),),
                ).fetchone()
                count = int(row[0]) if row else 0
            finally:
                connection.close()
            if count > 0:
                time.sleep(0.8)
                return True
        except sqlite3.DatabaseError:
            pass
        time.sleep(0.5)
    return False


def _open_url(url: str) -> subprocess.CompletedProcess:
    if IS_MACOS:
        return run_capture(["open", url], timeout=10, env=_child_process_env())
    if IS_WINDOWS:
        return run_capture(
            ["cmd", "/c", "start", "", url],
            timeout=10,
            env=_child_process_env(),
        )
    return run_capture(["xdg-open", url], timeout=10, env=_child_process_env())


def open_thread_deeplink(thread_id: str, retries: int = 2, repeat: int = 1) -> None:
    _refuse_in_test_mode("open a deep link in")
    _require_process_control("open")
    url = f"codex://threads/{thread_id}"
    last_error = ""
    for _ in range(max(1, repeat)):
        for attempt in range(retries + 1):
            try:
                result = _open_url(url)
                if result.returncode == 0:
                    break
                last_error = result.stderr.strip() or result.stdout.strip()
            except RelayError as exc:
                last_error = str(exc)
            time.sleep(1.0 + attempt)
        else:
            raise RelayError(f"打开会话深链接失败：{last_error}")
        time.sleep(0.8)


def daemon_socket() -> Path:
    suffix = "app-server-control.sock"
    if IS_WINDOWS:
        suffix = "app-server-control.sock"
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "app-server-control" / suffix


def daemon_running() -> bool:
    return daemon_socket().exists()


def stop_daemon(timeout: float = 20.0) -> Tuple[bool, str]:
    _refuse_in_test_mode("stop the shared app-server daemon")
    if not daemon_running():
        return False, "未运行"
    codex = which_codex()
    if not codex:
        return False, "找不到 codex CLI"
    try:
        result = run_capture([codex, "app-server", "daemon", "stop"], timeout=timeout)
    except RelayError as exc:
        return False, str(exc)
    output = (result.stdout or result.stderr).strip()
    return result.returncode == 0, output or "已请求停止"


def daemon_version() -> Optional[str]:
    codex = which_codex()
    if not codex:
        return None
    try:
        result = run_capture([codex, "app-server", "daemon", "version"], timeout=10)
    except RelayError:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or result.stderr).strip() or None


def other_codex_processes(exclude_desktop: bool = True) -> List[ProcessInfo]:
    result = []
    for proc in codex_processes():
        if exclude_desktop and proc.role in (
            "desktop-main",
            "desktop-app-server",
            "desktop-exec-server",
            "desktop-codex",
            "helper",
        ):
            continue
        if proc.role in ("daemon", "helper"):
            continue
        result.append(proc)
    return result


def process_age_seconds(proc: ProcessInfo) -> Optional[float]:
    if not proc.started:
        return None
    return max(0.0, (datetime.now() - proc.started).total_seconds())


def describe_processes(processes: List[ProcessInfo]) -> List[Dict[str, object]]:
    return [
        {
            "pid": p.pid,
            "ppid": p.ppid,
            "role": p.role,
            "started": p.started.isoformat(sep=" ") if p.started else None,
            "command": p.short_command[:300],
        }
        for p in processes
    ]


def configured_auth_store(config_path: Path) -> Optional[str]:
    if not config_path.exists():
        return None
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(
        r'^\s*cli_auth_credentials_store\s*=\s*["\']([^"\']+)["\']',
        text,
        flags=re.MULTILINE,
    )
    return match.group(1).strip() if match else None


def codex_binary_info() -> Dict[str, Optional[str]]:
    codex = which_codex()
    return {
        "path": codex,
        "version": version_of(codex) if codex else None,
        "resolved": shutil.which(codex) if codex else None,
    }
