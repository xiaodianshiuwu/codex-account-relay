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
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)
    return target


def configured_codex_binary() -> Optional[str]:
    value = load_config().get("codex_binary")
    return str(value) if isinstance(value, str) and value.strip() else None


def configured_codex_home() -> Optional[str]:
    value = load_config().get("codex_home")
    return str(value) if isinstance(value, str) and value.strip() else None
