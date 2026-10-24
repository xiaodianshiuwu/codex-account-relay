from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .util import RelayError


OPENAI_AUTH_CLAIM = "https://api.openai.com/auth"
OPENAI_PROFILE_CLAIM = "https://api.openai.com/profile"


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except Exception as exc:  # noqa: BLE001 - user-facing parse error
        raise RelayError("无法解析 JWT 载荷：base64 解码失败") from exc


def decode_jwt_payload(token: Optional[str]) -> Dict[str, Any]:
    if not token or not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _auth_claims(payload: Dict[str, Any]) -> Dict[str, Any]:
    claims = payload.get(OPENAI_AUTH_CLAIM)
    return claims if isinstance(claims, dict) else {}


def _profile_claims(payload: Dict[str, Any]) -> Dict[str, Any]:
    claims = payload.get(OPENAI_PROFILE_CLAIM)
    return claims if isinstance(claims, dict) else {}


def _first_nonempty(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_identity(auth: Dict[str, Any]) -> Dict[str, Any]:
    """Extract display and routing identity from an auth.json payload.

    No signature verification is performed: these values are local metadata used
    for account matching and display. Authentication itself remains Codex's job.
    """
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    access = decode_jwt_payload(tokens.get("access_token"))
    id_token = decode_jwt_payload(tokens.get("id_token"))
    access_auth = _auth_claims(access)
    id_auth = _auth_claims(id_token)
    access_profile = _profile_claims(access)
    id_profile = _profile_claims(id_token)

    account_id = _first_nonempty(
        id_auth.get("chatgpt_account_id"),
        access_auth.get("chatgpt_account_id"),
        tokens.get("account_id"),
    )
    user_id = _first_nonempty(
        id_auth.get("chatgpt_user_id"),
        access_auth.get("chatgpt_user_id"),
        id_auth.get("user_id"),
        access_auth.get("user_id"),
    )
    email = _first_nonempty(
        id_profile.get("email"),
        access_profile.get("email"),
        id_token.get("email"),
        access.get("email"),
        id_auth.get("email"),
    )
    name = _first_nonempty(
        id_profile.get("name"),
        access_profile.get("name"),
        id_token.get("name"),
        access.get("name"),
    )
    plan = _first_nonempty(
        id_auth.get("chatgpt_plan_type"),
        access_auth.get("chatgpt_plan_type"),
        id_token.get("chatgpt_plan_type"),
    )
    subscription_until = _first_nonempty(
        id_auth.get("chatgpt_subscription_active_until"),
    )
    organizations = id_auth.get("organizations")
    if not isinstance(organizations, list):
        organizations = []

    access_exp = None
    if isinstance(access.get("exp"), (int, float)):
        try:
            access_exp = datetime.fromtimestamp(float(access["exp"]), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            access_exp = None

    return {
        "account_id": account_id,
        "user_id": user_id,
        "email": email,
        "name": name,
        "plan": plan,
        "subscription_until": subscription_until,
        "organizations": organizations,
        "access_expires_at": access_exp.isoformat().replace("+00:00", "Z") if access_exp else None,
        "access_expired": bool(access_exp and access_exp.timestamp() <= datetime.now(tz=timezone.utc).timestamp()),
        "auth_mode": auth.get("auth_mode"),
        "last_refresh": auth.get("last_refresh"),
    }


def display_account(identity: Dict[str, Any]) -> str:
    email = identity.get("email") or "(unknown email)"
    plan = identity.get("plan")
    return f"{email} [{plan}]" if plan else email
