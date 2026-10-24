from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import __version__
from .authfile import read_auth, validate_auth, write_auth
from .store import RelayPaths
from .util import RelayError, ensure_dir, executable_command, now_iso, redact, which_codex


class RpcError(RelayError):
    def __init__(self, method: str, code: Optional[int], message: str):
        super().__init__(f"{method} 失败（{code}）：{message}")
        self.method = method
        self.code = code
        self.message = message


class JsonRpcProcess:
    """Minimal JSON-RPC 2.0 client for `codex app-server --listen stdio://`."""

    def __init__(
        self,
        command: List[str],
        *,
        env: Dict[str, str],
        cwd: Optional[Path] = None,
        request_timeout: float = 60.0,
    ):
        self.command = command
        self.request_timeout = request_timeout
        self._messages: "queue.Queue[Any]" = queue.Queue()
        self._stderr_lines: List[str] = []
        self._stderr_lock = threading.Lock()
        self._closed = False
        try:
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.process = subprocess.Popen(
                executable_command(command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                cwd=str(cwd) if cwd else None,
                **kwargs,
            )
        except OSError as exc:
            raise RelayError(f"无法启动 app-server：{exc}") from exc
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                with self._stderr_lock:
                    self._stderr_lines.append(f"非 JSON 输出: {line[:300]}")
                continue
            self._messages.put(message)
        self._messages.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            line = line.rstrip()
            if not line:
                continue
            with self._stderr_lock:
                safe = redact(line)
                self._stderr_lines.append(safe[:500] if isinstance(safe, str) else line[:500])
                if len(self._stderr_lines) > 80:
                    del self._stderr_lines[:20]

    def stderr_tail(self, limit: int = 20) -> str:
        with self._stderr_lock:
            return "\n".join(self._stderr_lines[-limit:])

    def _send(self, message: Dict[str, Any]) -> None:
        if self._closed or self.process.poll() is not None:
            raise RelayError(f"app-server 已退出。stderr：\n{self.stderr_tail()}")
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RelayError(f"写入 app-server 失败：{exc}\n{self.stderr_tail()}") from exc

    def notify(self, method: str, params: Any = None) -> None:
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method: str, params: Any = None, request_id: Optional[int] = None) -> Any:
        request_id = request_id if request_id is not None else int(time.time() * 1000) % 2_000_000_000
        message: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = time.monotonic() + self.request_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RelayError(f"{method} 超时（{self.request_timeout:.0f}s）。stderr：\n{self.stderr_tail()}")
            try:
                incoming = self._messages.get(timeout=remaining)
            except queue.Empty:
                continue
            if incoming is None:
                raise RelayError(
                    f"app-server 在等待 {method} 响应时退出（code={self.process.poll()}）。stderr：\n{self.stderr_tail()}"
                )
            if not isinstance(incoming, dict):
                continue
            if incoming.get("id") == request_id:
                if "error" in incoming:
                    error = incoming.get("error") or {}
                    raise RpcError(method, error.get("code"), str(error.get("message") or error))
                return incoming.get("result")
            if "method" in incoming and "id" in incoming:
                # Server-to-client request. We do not advertise capabilities that
                # require one, so reject it explicitly instead of hanging.
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": incoming.get("id"),
                        "error": {"code": -32601, "message": "codex-relay does not handle server requests"},
                    }
                )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.process.stdin:
                self.process.stdin.close()
        except OSError:
            pass
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        for stream in (self.process.stdout, self.process.stderr):
            try:
                if stream:
                    stream.close()
            except (OSError, ValueError):
                pass


@contextmanager
def isolated_home(paths: RelayPaths, auth: Dict[str, Any], label: str = "verify") -> Iterator[Path]:
    ensure_dir(paths.tmp_dir, 0o700)
    directory = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=str(paths.tmp_dir)))
    try:
        os.chmod(directory, 0o700)
        write_auth(directory / "auth.json", auth)
        (directory / "config.toml").write_text(
            "# Isolated CODEX_HOME used by codex-relay for account verification.\n"
            'cli_auth_credentials_store = "file"\n',
            encoding="utf-8",
        )
        os.chmod(directory / "config.toml", 0o600)
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _app_server_env(codex_home: Path) -> Dict[str, str]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    env["RUST_LOG"] = env.get("CODEX_RELAY_RUST_LOG", "error")
    env["NO_COLOR"] = "1"
    env["CODEX_RELAY"] = "1"
    for key in ("OPENAI_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_API_KEY", "OPENAI_BASE_URL"):
        env.pop(key, None)
    codex = which_codex()
    if codex:
        parent = str(Path(codex).parent)
        env["PATH"] = parent + os.pathsep + env.get("PATH", "")
    return env


def _start_app_server(codex_home: Path, timeout: float = 30.0) -> JsonRpcProcess:
    codex = which_codex()
    if not codex:
        raise RelayError("找不到 codex CLI，请先安装 Codex 或设置 CODEX_RELAY_CODEX")
    process = JsonRpcProcess(
        executable_command([codex, "app-server", "--listen", "stdio://"]),
        env=_app_server_env(codex_home),
        request_timeout=timeout,
    )
    try:
        process.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex-relay",
                    "title": "Codex Relay",
                    "version": __version__,
                },
                "capabilities": {"experimentalApi": True},
            },
            request_id=1,
        )
        process.notify("initialized")
    except Exception:
        process.close()
        raise
    return process


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rate_window(value: Any) -> Optional[Dict[str, Any]]:
    value = _as_dict(value)
    if not value:
        return None
    return {
        "used_percent": value.get("usedPercent", value.get("used_percent")),
        "window_minutes": value.get("windowDurationMins", value.get("window_duration_mins")),
        "resets_at": value.get("resetsAt", value.get("resets_at")),
    }


def parse_usage_response(account_result: Any, limits_result: Any) -> Dict[str, Any]:
    account_result = _as_dict(account_result)
    limits_result = _as_dict(limits_result)
    account = _as_dict(account_result.get("account"))
    rate_limits = _as_dict(limits_result.get("rateLimits") or limits_result.get("rate_limits"))
    by_id = _as_dict(
        limits_result.get("rateLimitsByLimitId") or limits_result.get("rate_limits_by_limit_id")
    )
    if not rate_limits and by_id:
        # Prefer the codex bucket when present.
        rate_limits = _as_dict(by_id.get("codex")) or _as_dict(next(iter(by_id.values()), None))
    plan = (
        rate_limits.get("planType")
        or rate_limits.get("plan_type")
        or account.get("planType")
        or account.get("plan_type")
    )
    result = {
        "checked_at": now_iso(),
        "account": account,
        "account_id": limits_result.get("accountId") or limits_result.get("account_id"),
        "plan": plan,
        "primary": _rate_window(rate_limits.get("primary")),
        "secondary": _rate_window(rate_limits.get("secondary")),
        "rate_limit_reached_type": rate_limits.get("rateLimitReachedType")
        or rate_limits.get("rate_limit_reached_type"),
        "credits": rate_limits.get("credits"),
        "ordinary_usage_allowed": limits_result.get("ordinaryUsageAllowed")
        if "ordinaryUsageAllowed" in limits_result
        else limits_result.get("ordinary_usage_allowed"),
        "rate_limits_raw": {
            key: rate_limits.get(key)
            for key in (
                "limitId",
                "limitName",
                "planType",
                "primary",
                "secondary",
                "credits",
                "rateLimitReachedType",
            )
            if key in rate_limits
        },
    }
    return result


def fetch_usage(
    paths: RelayPaths,
    auth: Dict[str, Any],
    *,
    timeout: float = 90.0,
    force_refresh: bool = False,
    persist_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Fetch account identity and rate limits using an isolated official app-server.

    Returns (usage_snapshot, refreshed_auth). The refreshed auth is the temp
    home's auth.json after the call; callers must persist it because OAuth
    refresh tokens rotate.

    `persist_callback` is invoked inside the temp-home lifetime, before the
    directory is deleted, so a rotated refresh token is never lost even when a
    later RPC fails.
    """
    original_identity = validate_auth(auth)
    refreshed: Optional[Dict[str, Any]] = None
    account_result: Any = None
    limits_result: Any = None
    error: Optional[BaseException] = None
    with isolated_home(paths, auth) as home:
        process: Optional[JsonRpcProcess] = None
        try:
            process = _start_app_server(home, timeout=30.0)
            process.request_timeout = timeout
            account_result = process.request(
                "account/read", {"refreshToken": bool(force_refresh)}
            )
            limits_result = process.request(
                "account/rateLimits/read",
                {"excludeResetCreditDetails": True},
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised after token capture
            error = exc
        finally:
            if process is not None:
                process.close()
            try:
                candidate = read_auth(home / "auth.json")
                if candidate.get("tokens"):
                    candidate_identity = validate_auth(candidate)
                    if candidate_identity.get("account_id") != original_identity.get("account_id"):
                        if error is None:
                            error = RelayError(
                                "刷新后的凭据账号身份发生变化："
                                f"{original_identity.get('account_id')} -> "
                                f"{candidate_identity.get('account_id')}；已拒绝写回。"
                            )
                    else:
                        refreshed = candidate
            except RelayError as candidate_error:
                if error is None:
                    error = candidate_error
            if refreshed is not None and persist_callback is not None:
                try:
                    persist_callback(refreshed)
                except Exception as persist_error:  # noqa: BLE001
                    if error is None:
                        error = persist_error
    if error is not None:
        if refreshed is not None and isinstance(error, RelayError):
            setattr(error, "refreshed_auth", refreshed)
        raise error
    if refreshed is None:
        raise RelayError("app-server 调用结束但未能读取认证文件")
    usage = parse_usage_response(account_result, limits_result)
    return usage, refreshed


def verify_profile(
    paths: RelayPaths,
    auth: Dict[str, Any],
    *,
    timeout: float = 90.0,
    force_refresh: bool = False,
    persist_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    usage, refreshed = fetch_usage(
        paths,
        auth,
        timeout=timeout,
        force_refresh=force_refresh,
        persist_callback=persist_callback,
    )
    original_identity = validate_auth(auth)
    refreshed_identity = validate_auth(refreshed)
    if original_identity.get("account_id") != refreshed_identity.get("account_id"):
        raise RelayError(
            "认证校验发现账号身份发生变化："
            f"{original_identity.get('email')} -> {refreshed_identity.get('email')}"
        )
    usage["verified_account_id"] = refreshed_identity.get("account_id")
    usage["verified_email"] = refreshed_identity.get("email")
    return usage, refreshed


def login_in_isolated_home(
    paths: RelayPaths,
    *,
    device_auth: bool = False,
    timeout: float = 600.0,
) -> Dict[str, Any]:
    """Run the official login flow in a throwaway CODEX_HOME.

    The throwaway home has no existing auth.json, so Codex cannot revoke the
    active session while the user adds another account.
    """
    codex = which_codex()
    if not codex:
        raise RelayError("找不到 codex CLI，请先安装 Codex")
    ensure_dir(paths.tmp_dir, 0o700)
    directory = Path(tempfile.mkdtemp(prefix="login-", dir=str(paths.tmp_dir)))
    try:
        os.chmod(directory, 0o700)
        (directory / "config.toml").write_text(
            'cli_auth_credentials_store = "file"\n', encoding="utf-8"
        )
        os.chmod(directory / "config.toml", 0o600)
        env = _app_server_env(directory)
        command = [codex, "login"]
        if device_auth:
            command.append("--device-auth")
        print("正在使用隔离的 CODEX_HOME 登录；不会触碰当前活动账号。")
        print("浏览器中请选择要添加的那个账号。")
        try:
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                executable_command(command),
                env=env,
                timeout=timeout,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                **kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            raise RelayError(f"登录超时（{timeout:.0f}s），未保存任何凭据") from exc
        except OSError as exc:
            raise RelayError(f"无法启动 codex login：{exc}") from exc
        if result.returncode != 0:
            raise RelayError(f"codex login 失败，退出码 {result.returncode}")
        auth_path = directory / "auth.json"
        if not auth_path.exists():
            raise RelayError("登录命令成功，但隔离目录中没有生成 auth.json")
        auth = read_auth(auth_path)
        validate_auth(auth)
        return auth
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def account_read(paths: RelayPaths, auth: Dict[str, Any], timeout: float = 45.0) -> Dict[str, Any]:
    validate_auth(auth, require_refresh=False)
    with isolated_home(paths, auth, label="account") as home:
        process = _start_app_server(home, timeout=30.0)
        try:
            process.request_timeout = timeout
            result = process.request("account/read", {"refreshToken": False})
        finally:
            process.close()
    return _as_dict(result)
