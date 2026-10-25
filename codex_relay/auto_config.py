"""Auto-continue/switch settings shared by the CLI and desktop clients.

The file lives next to the account state and contains no credentials. It is
intentionally separate from Codex's own configuration so uninstalling Relay
does not alter the user's Codex setup.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .config import relay_home

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "prompt": "",
    "remaining_threshold": 10.0,
    "interval": 60.0,
    "strategy": "best",
}


def auto_path() -> Path:
    return relay_home() / "auto.json"


def load_auto_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    target = Path(path) if path else auto_path()
    result = dict(DEFAULTS)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(data, dict):
        return result
    if isinstance(data.get("enabled"), bool):
        result["enabled"] = data["enabled"]
    if isinstance(data.get("prompt"), str):
        result["prompt"] = data["prompt"]
    for key in ("remaining_threshold", "interval"):
        try:
            value = float(data.get(key))
        except (TypeError, ValueError):
            continue
        if key == "remaining_threshold":
            result[key] = max(0.0, min(100.0, value))
        elif value > 0:
            result[key] = value
    if data.get("strategy") in ("next", "best"):
        result["strategy"] = data["strategy"]
    return result


def save_auto_settings(values: Dict[str, Any], path: Optional[Path] = None) -> Path:
    target = Path(path) if path else auto_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    current = load_auto_settings(target)
    current.update({key: values[key] for key in DEFAULTS if key in values})
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, target)
    return target
