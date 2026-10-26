"""Validated settings shared by the CLI and native app."""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Any, Dict, Optional
from .config import relay_home
from .util import atomic_write_json, file_lock, RelayError

DEFAULTS: Dict[str, Any] = {
    "enabled": False, "prompt": "", "remaining_threshold": 10.0,
    "interval": 60.0, "strategy": "best", "allow_chatgpt_restart": False,
    "thread_id": "",
}

def auto_path() -> Path:
    return relay_home() / "auto.json"

def validate(values: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(DEFAULTS, **values)
    for key in ("enabled", "allow_chatgpt_restart"):
        if not isinstance(result[key], bool):
            raise RelayError(f"{key} must be a boolean")
    for key in ("remaining_threshold", "interval"):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise RelayError(f"{key} must be a finite number")
    if not 0 <= result["remaining_threshold"] <= 100 or result["interval"] <= 0:
        raise RelayError("remaining_threshold must be 0–100; interval must be positive")
    if result["strategy"] not in ("next", "best"):
        raise RelayError("strategy must be next or best")
    for key in ("prompt", "thread_id"):
        if not isinstance(result[key], str) or "\0" in result[key]:
            raise RelayError(f"{key} must be text without NUL")
    if result["thread_id"]:
        import uuid
        try: result["thread_id"] = str(uuid.UUID(result["thread_id"]))
        except ValueError as exc: raise RelayError("thread_id must be a full UUID") from exc
    return result

def load_auto_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    target = Path(path) if path else auto_path()
    if not target.exists(): return dict(DEFAULTS)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict): return dict(DEFAULTS)
        return validate(data)
    except (OSError, ValueError, RelayError):
        # Invalid automation configuration must never enable actions.
        return dict(DEFAULTS)

def save_auto_settings(values: Dict[str, Any], path: Optional[Path] = None) -> Path:
    target = Path(path) if path else auto_path()
    with file_lock(target.with_suffix(".lock")):
        current = load_auto_settings(target)
        current.update(values)
        atomic_write_json(target, validate(current), mode=0o600)
    return target
