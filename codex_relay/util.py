from __future__ import annotations

import errno
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

try:  # POSIX only
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows only
    import msvcrt  # type: ignore[import-not-found]
except ImportError:  # POSIX
    msvcrt = None  # type: ignore[assignment]
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .config import configured_codex_binary


class RelayError(RuntimeError):
    """An expected, user-facing error."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def epoch_seconds() -> int:
    return int(time.time())


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    return path


def chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write bytes atomically without ever exposing a partially written secret."""
    ensure_dir(path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
        # Directory fsync is POSIX-only; Windows does not expose O_DIRECTORY.
        if hasattr(os, "O_DIRECTORY"):
            try:
                dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def atomic_write_json(path: Path, value: Any, mode: int = 0o600) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    atomic_write_bytes(path, payload + b"\n", mode=mode)


def sqlite_uri(path: Path, mode: str = "ro") -> str:
    """Build a SQLite URI that works on POSIX and Windows paths."""
    return f"{path.resolve().as_uri()}?mode={mode}"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read()


def safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def short_text(text: str, limit: int = 80) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def redact(value: Any) -> Any:
    """Best-effort redaction for diagnostics. Never use this to store auth."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in ("token", "secret", "password", "api_key", "apikey", "authorization")):
                result[key] = "<redacted>"
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and looks_like_jwt(value):
        return "<redacted-jwt>"
    return value


def looks_like_jwt(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$", value))


def which_codex() -> Optional[str]:
    explicit = os.environ.get("CODEX_RELAY_CODEX")
    if explicit and Path(explicit).exists():
        return explicit
    configured = configured_codex_binary()
    if configured and Path(configured).expanduser().exists():
        return str(Path(configured).expanduser())
    found = shutil.which("codex")
    if found:
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "codex",
        Path.home() / ".codex" / "bin" / "codex",
        Path("/opt/homebrew/bin/codex"),
        Path("/usr/local/bin/codex"),
    ]
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.extend(
                [
                    Path(local) / "Programs" / "Codex" / "codex.exe",
                    Path(local) / "Programs" / "codex" / "codex.exe",
                    Path(local) / "Microsoft" / "WindowsApps" / "codex.exe",
                ]
            )
        candidates.extend([Path.home() / "AppData" / "Local" / "Programs" / "Codex" / "codex.exe"])
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def executable_command(args: Iterable[str]) -> list:
    """Return an argv that Windows CreateProcess can actually execute.

    Python's subprocess cannot directly execute .cmd/.bat files on Windows;
    Codex is normally an .exe, but package-manager shims may be .cmd files.
    """
    command = [str(item) for item in args]
    if sys.platform == "win32" and command:
        suffix = Path(command[0]).suffix.lower()
        if suffix in (".cmd", ".bat"):
            return ["cmd", "/c", *command]
    return command


def run_capture(args: Iterable[str], timeout: float = 15.0, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(
            executable_command(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        raise RelayError(f"命令超时（{timeout:.0f}s）：{' '.join(args)}") from exc
    except OSError as exc:
        raise RelayError(f"无法执行命令：{' '.join(args)}（{exc}）") from exc


def version_of(command: str, timeout: float = 10.0) -> Optional[str]:
    try:
        result = run_capture([command, "--version"], timeout=timeout)
    except (OSError, RelayError):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0].strip() if text else None


@contextmanager
def file_lock(path: Path, timeout: float = 15.0) -> Iterator[None]:
    ensure_dir(path.parent, 0o700)
    handle = path.open("a+b")
    deadline = time.monotonic() + timeout
    locked = False
    try:
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                elif msvcrt is not None:
                    handle.seek(0)
                    if handle.read(1) == b"":
                        handle.seek(0)
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    # Last-resort platforms: process-local operation lock only.
                    pass
                locked = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, 13):
                    raise
                if time.monotonic() >= deadline:
                    raise RelayError(
                        f"另一个 codex-relay 操作正在进行（锁文件：{path}）。稍后重试。"
                    ) from exc
                time.sleep(0.1)
        yield
    finally:
        if locked:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:
                    handle.seek(0)
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
            finally:
                handle.close()
        else:
            handle.close()


def local_time(epoch_seconds_value: Optional[int]) -> str:
    if epoch_seconds_value is None:
        return "-"
    try:
        return datetime.fromtimestamp(epoch_seconds_value).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "-"


def parse_epoch_maybe_ms(value: Optional[int]) -> Optional[int]:
    """Normalize a timestamp that may be seconds, milliseconds or microseconds."""
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    if value > 10_000_000_000_000:  # microseconds
        return value // 1_000_000
    if value > 10_000_000_000:  # milliseconds
        return value // 1_000
    return value


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
