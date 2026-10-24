from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .jwt import extract_identity
from .util import RelayError, atomic_write_bytes, chmod_private, read_bytes


def read_auth(path: Path) -> Dict[str, Any]:
    try:
        raw = read_bytes(path)
    except FileNotFoundError as exc:
        raise RelayError(f"找不到认证文件：{path}") from exc
    except OSError as exc:
        raise RelayError(f"无法读取认证文件：{path}（{exc}）") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RelayError(f"认证文件不是有效 JSON：{path}") from exc
    if not isinstance(value, dict):
        raise RelayError(f"认证文件结构异常：{path}")
    return value


def validate_auth(auth: Dict[str, Any], *, require_refresh: bool = True) -> Dict[str, Any]:
    if not isinstance(auth, dict):
        raise RelayError("认证数据必须是 JSON 对象")
    mode = auth.get("auth_mode")
    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        if auth.get("OPENAI_API_KEY"):
            raise RelayError(
                "当前认证是 API Key 模式；本工具当前只支持 ChatGPT 账号接力，请先使用 ChatGPT 登录。"
            )
        raise RelayError("认证文件缺少 tokens，无法作为 ChatGPT 账号使用")
    for key in ("access_token", "id_token"):
        if not isinstance(tokens.get(key), str) or not tokens[key].strip():
            raise RelayError(f"认证文件缺少 {key}")
    if require_refresh and (not isinstance(tokens.get("refresh_token"), str) or not tokens["refresh_token"].strip()):
        raise RelayError(
            "认证文件缺少 refresh_token，无法在切换后长期续期；请用 codex login 重新登录该账号。"
        )
    if mode not in (None, "chatgpt", "chatgptAuthTokens"):
        raise RelayError(f"不支持的认证模式：{mode}")
    identity = extract_identity(auth)
    if not identity.get("account_id"):
        raise RelayError("无法从 JWT 中解析 chatgpt_account_id，认证文件可能不完整")
    return identity


def write_auth(path: Path, auth: Dict[str, Any]) -> None:
    validate_auth(auth)
    payload = json.dumps(auth, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    atomic_write_bytes(path, payload, mode=0o600)
    chmod_private(path)


def auth_file_mode(path: Path) -> Optional[int]:
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return None


def auth_summary(auth: Dict[str, Any]) -> Dict[str, Any]:
    identity = extract_identity(auth)
    return {
        "auth_mode": auth.get("auth_mode"),
        "account_id": identity.get("account_id"),
        "user_id": identity.get("user_id"),
        "email": identity.get("email"),
        "name": identity.get("name"),
        "plan": identity.get("plan"),
        "last_refresh": auth.get("last_refresh"),
        "access_expires_at": identity.get("access_expires_at"),
        "access_expired": identity.get("access_expired"),
        "has_refresh_token": bool((auth.get("tokens") or {}).get("refresh_token")),
        "has_api_key": bool(auth.get("OPENAI_API_KEY")),
    }


def auths_equivalent(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
    if a is None or b is None:
        return a is b
    return a.get("tokens") == b.get("tokens") and a.get("last_refresh") == b.get("last_refresh")
