from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import codex_env
from .appserver import verify_profile
from .authfile import validate_auth
from .i18n import Translator
from .store import Store
from .threads import active_turn_status, rollout_activity, rollout_path_for, get_thread
from .util import RelayError, local_time, now_iso


AUTH_ERROR_MARKERS = (
    "401",
    "unauthorized",
    "invalid_grant",
    "refresh_token",
    "refresh token",
    "token 已失效",
    "session has ended",
    "login required",
)


def _is_auth_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in AUTH_ERROR_MARKERS)


def _window_from_http(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    seconds = value.get("limit_window_seconds")
    minutes = None
    if isinstance(seconds, (int, float)):
        minutes = int(seconds // 60)
    return {
        "used_percent": value.get("used_percent"),
        "window_minutes": minutes,
        "resets_at": value.get("reset_at"),
    }


def fetch_usage_http(
    auth: Dict[str, Any],
    *,
    timeout: float = 15.0,
    translator: Optional[Translator] = None,
) -> Dict[str, Any]:
    """Read live usage without refreshing or mutating OAuth credentials.

    This is the only safe path for the account currently owned by a running
    Codex app-server: a second process refreshing the same rotating refresh
    token would invalidate the live process.
    """
    tr = translator or Translator()
    identity = validate_auth(auth)
    tokens = auth.get("tokens") or {}
    access_token = tokens.get("access_token")
    if not access_token:
        raise RelayError(tr.t("缺少 access token"))
    if identity.get("access_expired"):
        raise RelayError(
            tr.t(
                "当前活动账号的 access token 已过期。请让 Codex App 自行刷新（切换到别的账号再切回，或重启 App）后重试；"
                "本工具不会在活动 app-server 运行时抢用 refresh token。"
            )
        )
    request = urllib.request.Request(
        "https://chatgpt.com/backend-api/wham/usage",
        headers={
            "Authorization": f"Bearer {access_token}",
            "chatgpt-account-id": str(identity.get("account_id")),
            "User-Agent": "codex-relay/0.1",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code == 401:
            raise RelayError(
                tr.t("用量接口返回 401：活动账号 token 已失效。请让 Codex App 刷新/重新登录后再试。")
            ) from exc
        raise RelayError(tr.t("用量接口 HTTP {code}：{body}", code=exc.code, body=body)) from exc
    except Exception as exc:  # noqa: BLE001 - network diagnostics
        raise RelayError(tr.t("用量接口请求失败：{error}", error=exc)) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RelayError(tr.t("用量接口返回了非 JSON 数据")) from exc
    if not isinstance(data, dict):
        raise RelayError(tr.t("用量接口返回结构异常"))
    response_account = data.get("account_id")
    if response_account and response_account != identity.get("account_id"):
        raise RelayError(
            tr.t(
                "用量接口返回的 account_id 与当前凭据不一致，已拒绝使用该结果：{expected} -> {actual}",
                expected=identity.get("account_id"),
                actual=response_account,
            )
        )
    rate_limit = data.get("rate_limit") if isinstance(data.get("rate_limit"), dict) else {}
    credits = data.get("credits") if isinstance(data.get("credits"), dict) else None
    spend_control = data.get("spend_control") if isinstance(data.get("spend_control"), dict) else None
    return {
        "checked_at": now_iso(),
        "source": "http",
        "account": {
            "type": "chatgpt",
            "email": data.get("email"),
            "planType": data.get("plan_type"),
        },
        "account_id": data.get("account_id") or identity.get("account_id"),
        "plan": data.get("plan_type") or identity.get("plan"),
        "primary": _window_from_http(rate_limit.get("primary_window")),
        "secondary": _window_from_http(rate_limit.get("secondary_window")),
        "rate_limit_reached_type": data.get("rate_limit_reached_type"),
        "credits": credits,
        "spend_control": spend_control,
        "ordinary_usage_allowed": rate_limit.get("allowed"),
        "rate_limits_raw": {
            "allowed": rate_limit.get("allowed"),
            "limit_reached": rate_limit.get("limit_reached"),
            "primary_window": rate_limit.get("primary_window"),
            "secondary_window": rate_limit.get("secondary_window"),
        },
    }


def refresh_usage(
    store: Store,
    profile: Dict[str, Any],
    *,
    force_refresh: bool = False,
    timeout: float = 90.0,
    translator: Optional[Translator] = None,
) -> Dict[str, Any]:
    tr = translator or Translator()
    active = store.current_profile()
    is_active = bool(active and active.get("id") == profile.get("id"))
    if is_active:
        # The running app-server may have refreshed and rotated auth.json since
        # the profile snapshot. Capture it before reading the profile secret.
        snap = store.snapshot_current(adopt=True)
        if snap:
            profile.update(snap)
    auth = store.read_secret(str(profile["id"]))
    identity = validate_auth(auth)
    live_processes = bool(codex_env.codex_processes())
    if is_active and live_processes and not force_refresh:
        usage = fetch_usage_http(auth, timeout=min(timeout, 20.0), translator=tr)
        profile["usage"] = usage
        profile["last_verified_at"] = now_iso()
        profile["status"] = "ok"
        profile.pop("last_error", None)
        profile.pop("last_error_at", None)
        store.save_state()
        return usage

    def persist(new_auth: Dict[str, Any]) -> None:
        store.persist_profile_auth(profile, new_auth)

    try:
        usage, refreshed = verify_profile(
            store.paths,
            auth,
            force_refresh=force_refresh or bool(identity.get("access_expired")),
            persist_callback=persist,
            timeout=timeout,
        )
    except RelayError as exc:
        refreshed = getattr(exc, "refreshed_auth", None)
        if isinstance(refreshed, dict):
            persist(refreshed)
        profile["status"] = "needs_relogin" if _is_auth_error(str(exc)) else "error"
        profile["last_error"] = str(exc)[:500]
        profile["last_error_at"] = now_iso()
        store.save_state()
        raise
    profile["usage"] = usage
    profile["last_verified_at"] = now_iso()
    profile["status"] = "ok"
    profile.pop("last_error", None)
    profile.pop("last_error_at", None)
    store.save_state()
    return usage


def max_used_percent(usage: Optional[Dict[str, Any]]) -> float:
    if not isinstance(usage, dict):
        return 0.0
    if usage.get("rate_limit_reached_type"):
        return 100.0
    values = []
    for key in ("primary", "secondary"):
        window = usage.get(key)
        if isinstance(window, dict):
            try:
                values.append(float(window.get("used_percent") or 0))
            except (TypeError, ValueError):
                pass
    return max(values) if values else 0.0


def _window_text(window: Any, tr: Translator) -> str:
    if not isinstance(window, dict):
        return "-"
    used = window.get("used_percent")
    minutes = window.get("window_minutes")
    resets = window.get("resets_at")
    label = "-"
    if isinstance(minutes, (int, float)):
        label = tr.duration(minutes)
    reset_text = ""
    if isinstance(resets, (int, float)):
        try:
            reset_text = tr.t("，{time} 重置", time=tr.dt(datetime.fromtimestamp(resets)))
        except (OverflowError, OSError, ValueError):
            reset_text = ""
    if used is None:
        return "-"
    try:
        percent = f"{float(used):.1f}"
    except (TypeError, ValueError):
        percent = "-"
    return tr.t(
        "{percent}%（{label}{reset}）",
        percent=percent,
        label=label,
        reset=reset_text,
    )


def usage_text(usage: Optional[Dict[str, Any]], translator: Optional[Translator] = None) -> str:
    if not isinstance(usage, dict):
        return "-"
    tr = translator or Translator()
    return tr.t(
        "5 小时 {primary} · 1 周 {secondary}",
        primary=_window_text(usage.get("primary"), tr),
        secondary=_window_text(usage.get("secondary"), tr),
    )


def usage_line(profile: Dict[str, Any], translator: Optional[Translator] = None) -> str:
    usage = profile.get("usage")
    tr = translator or Translator()
    if not isinstance(usage, dict):
        return tr.t("usage 未刷新")
    return usage_text(usage, tr)


def usage_dict(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "alias": profile.get("alias"),
        "email": profile.get("email"),
        "plan": profile.get("plan"),
        "status": profile.get("status"),
        "max_used_percent": round(max_used_percent(profile.get("usage")), 1),
        "usage": profile.get("usage"),
    }


def idle_check(store: Store, window_seconds: float = 180.0) -> Dict[str, Any]:
    """Return whether a Codex turn appears to be actively running.

    `thread_turns.status` can be left at `inProgress` after a crash or a UI
    restart, so it is combined with rollout mtime and the turn start time.
    """
    now = time.time()
    active = []
    for turn in active_turn_status(store.paths):
        started = turn.get("started_at")
        age = now - started if isinstance(started, (int, float)) else None
        try:
            thread = get_thread(store.paths, str(turn.get("thread_id")))
        except RelayError:
            thread = None
        mtime = None
        activity: Dict[str, Any] = {}
        if thread:
            path = rollout_path_for(store.paths, thread)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = None
            activity = rollout_activity(path)
        recent_mtime = bool(mtime and now - mtime < window_seconds)
        recent_start = bool(age is not None and age < window_seconds)
        pending_tools = int(activity.get("pending_tool_calls") or 0)
        if recent_mtime or recent_start or pending_tools:
            active.append(
                {
                    "thread_id": turn.get("thread_id"),
                    "turn_id": turn.get("turn_id"),
                    "started_at": local_time(started if isinstance(started, int) else None),
                    "age_seconds": round(age, 1) if age is not None else None,
                    "rollout_mtime": local_time(int(mtime)) if mtime else None,
                    "pending_tool_calls": pending_tools,
                    "pending_types": activity.get("pending_types") or [],
                    "last_rollout_type": activity.get("last_type"),
                }
            )
    return {
        "idle": not active,
        "window_seconds": window_seconds,
        "active": active,
        "app_running": codex_env.app_running(),
        "desktop_app_server": [p.pid for p in codex_env.desktop_app_server_processes()],
    }
