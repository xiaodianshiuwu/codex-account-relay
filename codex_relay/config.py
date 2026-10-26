"""Persistent, user-editable discovery settings for Codex Relay.

The automatic discovery path remains the default.  This module only records a
validated manual fallback (Codex executable and/or Codex data directory) so a
normal user never has to edit source code or remember shell-specific
environment variables.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def relay_home() -> Path:
    return Path(os.environ.get("CODEX_RELAY_HOME", str(Path.home() / ".codex-relay"))).expanduser()


def config_path() -> Path:
    return relay_home() / "config.json"


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or config_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    target = path or config_path()
    from .util import atomic_write_json
    atomic_write_json(target, data, mode=0o600)
    return target


def configured_codex_binary() -> Optional[str]:
    value = load_config().get("codex_binary")
    return str(value) if isinstance(value, str) and value.strip() else None


def configured_codex_home() -> Optional[str]:
    value = load_config().get("codex_home")
    return str(value) if isinstance(value, str) and value.strip() else None
