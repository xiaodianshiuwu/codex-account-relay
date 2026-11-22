from __future__ import annotations

import contextlib
import hashlib
import json
import math
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from desktop_guard.desktop import ThreadUnavailable
from desktop_guard.upstream_policy import latest_turn

from . import codex_env
from .appserver import verify_profile
from .authfile import validate_auth
from .i18n import Translator
from .store import Store
from .threads import active_turn_status, rollout_activity, rollout_path_for, get_thread
from .util import RelayError, local_time, now_iso, redact


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


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _window_from_http(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    seconds = value.get("limit_window_seconds")
    minutes = None
    if _is_finite_number(seconds):
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
        body = str(redact(exc.read().decode("utf-8", errors="replace")))[:500]
        if exc.code == 401:
            raise RelayError(
                tr.t("用量接口返回 401：活动账号 token 已失效。请让 Codex App 刷新/重新登录后再试。")
            ) from exc
        raise RelayError(tr.t("用量接口 HTTP {code}：{body}", code=exc.code, body=body)) from exc
    except Exception as exc:  # noqa: BLE001 - network diagnostics
        raise RelayError(tr.t("用量接口请求失败：{error}", error=redact(str(exc)))) from exc
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


def _auth_fingerprint(auth: Dict[str, Any]) -> str:
    canonical = json.dumps(auth, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _desktop_active() -> bool:
    return bool(codex_env.codex_processes()) or codex_env.app_running()


def _store_lock_ctx(store: Any):
    lock_fn = getattr(store, "lock", None)
    return lock_fn() if callable(lock_fn) else contextlib.nullcontext()


def _profile_refresh_lock_ctx(store: Any, pid: str, timeout: float = 95.0):
    lock_fn = getattr(store, "profile_refresh_lock", None)
    return lock_fn(pid, timeout=timeout) if callable(lock_fn) else contextlib.nullcontext()


def _disk_secret_fingerprint(store: Store, pid: str) -> Optional[str]:
    try:
        return _auth_fingerprint(store.read_secret(pid))
    except RelayError:
        return None


def _profile_is_live_desktop_active(store: Store, pid: str) -> bool:
    active = store.current_profile()
    return bool(active and str(active.get("id") or "") == pid and _desktop_active())


def _reload_store(store: Any) -> None:
    reload_fn = getattr(store, "reload", None)
    if callable(reload_fn):
        reload_fn()


def _sync_profile_dict(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    target.clear()
    target.update(source)


def refresh_usage(
    store: Store,
    profile: Dict[str, Any],
    *,
    force_refresh: bool = False,
    timeout: float = 90.0,
    translator: Optional[Translator] = None,
) -> Dict[str, Any]:
    tr = translator or Translator()
    pid = str(profile["id"])
    lock_timeout = min(max(timeout + 10.0, 30.0), 120.0)

    with _profile_refresh_lock_ctx(store, pid, timeout=lock_timeout):
        with _store_lock_ctx(store):
            _reload_store(store)
            fresh = store.get_profile(pid) if callable(getattr(store, "get_profile", None)) else profile
            if fresh is None:
                fresh = profile
            active = store.current_profile()
            is_active = bool(active and str(active.get("id") or "") == pid)
            if is_active:
                # The running app-server may have refreshed and rotated auth.json since
                # the profile snapshot. Capture it before reading the profile secret.
                snap = store.snapshot_current(adopt=True)
                if snap:
                    fresh = snap
                    _sync_profile_dict(profile, snap)
            auth = store.read_secret(pid)
            identity = validate_auth(auth)
            snapshot_fp = _auth_fingerprint(auth)
            snapshot_verified_at = fresh.get("last_verified_at")
            use_http = is_active and _desktop_active()

        if use_http:
            try:
                usage = fetch_usage_http(auth, timeout=min(timeout, 20.0), translator=tr)
            except RelayError as exc:
                safe_error = str(redact(str(exc)))[:500]
                checked = now_iso()
                with _store_lock_ctx(store):
                    _reload_store(store)
                    current_prof = store.get_profile(pid) if callable(getattr(store, "get_profile", None)) else profile
                    if (
                        current_prof is not None
                        and _disk_secret_fingerprint(store, pid) == snapshot_fp
                        and current_prof.get("last_verified_at") == snapshot_verified_at
                    ):
                        current_prof.update(status="error", last_error=safe_error, last_error_at=checked)
                        store.save_state()
                        _sync_profile_dict(profile, current_prof)
                    elif current_prof is not None:
                        _sync_profile_dict(profile, current_prof)
                raise
            checked = now_iso()
            with _store_lock_ctx(store):
                _reload_store(store)
                current_prof = store.get_profile(pid) if callable(getattr(store, "get_profile", None)) else profile
                if (
                    current_prof is not None
                    and _disk_secret_fingerprint(store, pid) == snapshot_fp
                    and current_prof.get("last_verified_at") == snapshot_verified_at
                ):
                    current_prof["usage"] = usage
                    current_prof["last_verified_at"] = checked
                    current_prof["status"] = "ok"
                    current_prof.pop("last_error", None)
                    current_prof.pop("last_error_at", None)
                    store.save_state()
                    _sync_profile_dict(profile, current_prof)
                elif current_prof is not None:
                    _sync_profile_dict(profile, current_prof)
            return usage

        state_ref = {"current_fp": snapshot_fp, "persisted": False}

        def persist(new_auth: Dict[str, Any]) -> None:
            if new_auth == auth:
                return
            new_fp = _auth_fingerprint(new_auth)
            if new_fp == state_ref["current_fp"]:
                return
            with _store_lock_ctx(store):
                _reload_store(store)
                current_prof = store.get_profile(pid) if callable(getattr(store, "get_profile", None)) else profile
                if current_prof is None:
                    return
                if _disk_secret_fingerprint(store, pid) != state_ref["current_fp"]:
                    return
                if _profile_is_live_desktop_active(store, pid):
                    return
                store.persist_profile_auth(current_prof, new_auth)
                state_ref["current_fp"] = new_fp
                state_ref["persisted"] = True
                _sync_profile_dict(profile, current_prof)

        try:
            usage, _refreshed = verify_profile(
                store.paths,
                auth,
                force_refresh=force_refresh or bool(identity.get("access_expired")),
                persist_callback=persist,
                timeout=timeout,
            )
        except RelayError as exc:
            refreshed = getattr(exc, "refreshed_auth", None)
            if isinstance(refreshed, dict) and not state_ref["persisted"]:
                persist(refreshed)
            safe_error = str(redact(str(exc)))
            checked = now_iso()
            with _store_lock_ctx(store):
                _reload_store(store)
                current_prof = store.get_profile(pid) if callable(getattr(store, "get_profile", None)) else profile
                if (
                    current_prof is not None
                    and _disk_secret_fingerprint(store, pid) == state_ref["current_fp"]
                    and current_prof.get("last_verified_at") == snapshot_verified_at
                ):
                    current_prof["status"] = "needs_relogin" if _is_auth_error(safe_error) else "error"
                    current_prof["last_error"] = safe_error[:500]
                    current_prof["last_error_at"] = checked
                    store.save_state()
                    _sync_profile_dict(profile, current_prof)
                elif current_prof is not None:
                    _sync_profile_dict(profile, current_prof)
            raise
        checked = now_iso()
        with _store_lock_ctx(store):
            _reload_store(store)
            current_prof = store.get_profile(pid) if callable(getattr(store, "get_profile", None)) else profile
            if (
                current_prof is not None
                and _disk_secret_fingerprint(store, pid) == state_ref["current_fp"]
                and current_prof.get("last_verified_at") == snapshot_verified_at
            ):
                current_prof["usage"] = usage
                current_prof["last_verified_at"] = checked
                current_prof["status"] = "ok"
                current_prof.pop("last_error", None)
                current_prof.pop("last_error_at", None)
                store.save_state()
                _sync_profile_dict(profile, current_prof)
            elif current_prof is not None:
                _sync_profile_dict(profile, current_prof)
        return usage


def refresh_profiles_unlocked(
    store: Store,
    profile_ids: List[str],
    *,
    timeout: float = 90.0,
    translator: Optional[Translator] = None,
    on_start: Optional[Callable[[str], None]] = None,
    on_error: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Refresh usage for profiles without holding store.lock() across network I/O.

    Each profile is serialized by a per-profile refresh lock (strictly acquired
    before short store.lock() scopes) so concurrent refreshes of the same
    account never race on rotating OAuth refresh tokens while unrelated profiles
    still refresh in parallel and store.lock() stays free for concurrent writers.
    Every credential persist and final status/usage merge verifies the on-disk
    credential fingerprint and last_verified_at so a stale job can never clobber
    a concurrent re-login, token update, or deletion.
    """
    tr = translator or Translator()
    unique_ids = list(dict.fromkeys(str(pid) for pid in profile_ids if pid))
    if not unique_ids:
        return {}

    outcomes: Dict[str, Dict[str, Any]] = {}
    callback_lock = threading.Lock()
    lock_timeout = min(max(timeout + 10.0, 30.0), 120.0)

    def _run_profile(pid: str) -> tuple[str, Optional[Dict[str, Any]]]:
        with _profile_refresh_lock_ctx(store, pid, timeout=lock_timeout):
            # Step 1: short store.lock() snapshot of credential fingerprint & last_verified_at.
            with store.lock():
                store.reload()
                prof = store.get_profile(pid)
                if prof is None:
                    return pid, None
                active = store.current_profile()
                is_active = bool(active and str(active.get("id") or "") == pid)
                if is_active:
                    snap = store.snapshot_current(adopt=True)
                    if snap is not None:
                        prof = snap
                alias = str(prof.get("alias") or pid)
                snapshot_verified_at = prof.get("last_verified_at")
                try:
                    auth = store.read_secret(pid)
                    identity = validate_auth(auth)
                except RelayError as exc:
                    safe_err = str(redact(str(exc)))
                    checked = now_iso()
                    status = "needs_relogin" if _is_auth_error(safe_err) else "error"
                    prof["status"] = status
                    prof["last_error"] = safe_err[:500]
                    prof["last_error_at"] = checked
                    store.save_state()
                    return pid, {
                        "ok": False,
                        "alias": alias,
                        "status": status,
                        "error": safe_err[:500],
                    }
                snapshot_fp = _auth_fingerprint(auth)
                access_expired = bool(identity.get("access_expired"))
                use_http = is_active and _desktop_active()

            if on_start is not None:
                with callback_lock:
                    on_start(alias)

            # Step 2: network / app-server I/O outside store.lock().
            state_ref = {"current_fp": snapshot_fp, "persisted": False}
            if use_http:
                try:
                    usage_data = fetch_usage_http(auth, timeout=min(timeout, 20.0), translator=tr)
                    outcome: Dict[str, Any] = {"ok": True, "alias": alias, "usage": usage_data}
                except RelayError as exc:
                    safe_err = str(redact(str(exc)))
                    outcome = {"ok": False, "alias": alias, "status": "error", "error": safe_err[:500]}
            else:
                def persist_under_lock(new_auth: Dict[str, Any]) -> None:
                    if new_auth == auth:
                        return
                    new_fp = _auth_fingerprint(new_auth)
                    if new_fp == state_ref["current_fp"]:
                        return
                    with store.lock():
                        store.reload()
                        fresh = store.get_profile(pid)
                        if fresh is None:
                            return
                        if _disk_secret_fingerprint(store, pid) != state_ref["current_fp"]:
                            return
                        if _profile_is_live_desktop_active(store, pid):
                            return
                        store.persist_profile_auth(fresh, new_auth)
                        state_ref["current_fp"] = new_fp
                        state_ref["persisted"] = True

                try:
                    usage_data, _ = verify_profile(
                        store.paths,
                        auth,
                        force_refresh=access_expired,
                        persist_callback=persist_under_lock,
                        timeout=timeout,
                    )
                    outcome = {"ok": True, "alias": alias, "usage": usage_data}
                except RelayError as exc:
                    refreshed = getattr(exc, "refreshed_auth", None)
                    if isinstance(refreshed, dict) and not state_ref["persisted"]:
                        persist_under_lock(refreshed)
                    safe_err = str(redact(str(exc)))
                    outcome = {
                        "ok": False,
                        "alias": alias,
                        "status": "needs_relogin" if _is_auth_error(safe_err) else "error",
                        "error": safe_err[:500],
                    }

            # Step 3: guarded merge under short store.lock() while still holding per-profile lock.
            with store.lock():
                store.reload()
                fresh = store.get_profile(pid)
                if fresh is not None:
                    disk_fp = _disk_secret_fingerprint(store, pid)
                    if disk_fp == state_ref["current_fp"] and fresh.get("last_verified_at") == snapshot_verified_at:
                        checked = now_iso()
                        if outcome.get("ok"):
                            fresh["usage"] = outcome["usage"]
                            fresh["last_verified_at"] = checked
                            fresh["status"] = "ok"
                            fresh.pop("last_error", None)
                            fresh.pop("last_error_at", None)
                        else:
                            fresh["status"] = outcome.get("status") or "error"
                            fresh["last_error"] = outcome.get("error")
                            fresh["last_error_at"] = checked
                        store.save_state()
                    else:
                        outcome["skipped_stale_merge"] = True
            return pid, outcome

    if len(unique_ids) == 1:
        pid, outcome = _run_profile(unique_ids[0])
        if outcome is not None:
            outcomes[pid] = outcome
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(unique_ids))) as pool:
            for pid, outcome in pool.map(_run_profile, unique_ids):
                if outcome is not None:
                    outcomes[pid] = outcome

    if on_error is not None:
        for pid in unique_ids:
            outcome = outcomes.get(pid)
            if outcome and not outcome.get("ok") and not outcome.get("skipped_stale_merge"):
                on_error(str(outcome.get("alias") or pid), str(outcome.get("error") or ""))

    with store.lock():
        store.reload()
    return outcomes


def usage_known(usage: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(usage, dict): return False
    if usage.get('rate_limit_reached_type') or usage.get('ordinary_usage_allowed') is False: return True
    windows = [usage.get('primary'), usage.get('secondary')]
    valid = 0
    for window in windows:
        if window is None: continue
        if not isinstance(window, dict): return False
        value = window.get('used_percent')
        if not _is_finite_number(value) or not 0 <= value <= 100:
            return False
        reset = window.get('resets_at')
        if reset is not None and (not _is_finite_number(reset) or reset <= time.time()):
            return False
        valid += 1
    return valid > 0


def max_used_percent(usage: Optional[Dict[str, Any]]) -> float:
    if not isinstance(usage, dict):
        return 0.0
    if usage.get("rate_limit_reached_type") or usage.get("ordinary_usage_allowed") is False:
        return 100.0
    values = []
    for key in ("primary", "secondary"):
        window = usage.get(key)
        if isinstance(window, dict):
            raw = window.get("used_percent")
            if raw is not None and not isinstance(raw, bool):
                try:
                    parsed = float(raw)
                    if math.isfinite(parsed):
                        values.append(parsed)
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
    if _is_finite_number(minutes):
        label = tr.duration(minutes)
    reset_text = ""
    if _is_finite_number(resets):
        try:
            reset_text = tr.t("，{time} 重置", time=tr.dt(datetime.fromtimestamp(resets)))
        except (OverflowError, OSError, ValueError):
            reset_text = ""
    if used is None or isinstance(used, bool):
        return "-"
    try:
        parsed_used = float(used)
        if not math.isfinite(parsed_used):
            return "-"
        percent = f"{parsed_used:.1f}"
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


def idle_check(store: Store, window_seconds: float = 180.0, ignore_thread_id: Optional[str] = None) -> Dict[str, Any]:
    """Return whether a Codex turn appears to be actively running.

    History is not a live execution registry. Prefer the desktop owner, and
    recognize abandoned rows only with evidence across a desktop restart.
    Age alone must never turn a quiet, long-running task into an idle one.
    """
    now = time.time()
    active = []
    ignored = []
    desktop_starts = None
    seen_threads = set()
    for turn in active_turn_status(store.paths):
        thread_id = turn.get('thread_id')
        if thread_id == ignore_thread_id or thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)
        no_owner = False
        evidence = 'history-unconfirmed'
        try:
            from .desktop_continue import runtime_task
            _, state = runtime_task(store, str(turn.get('thread_id')))
            status = state.get('threadRuntimeStatus')
            live = status.get('type') if isinstance(status, dict) else None
            # Restart eligibility is independent of guard eligibility (e.g.
            # failed turns or paused goals are not executing tool calls).
            latest = latest_turn(state)
            if live in ('idle', 'systemError') and latest.get('status') in ('completed', 'interrupted', 'failed'):
                ignored.append(dict(turn, reason='desktop-terminal'))
                continue
            if live == 'active':
                evidence = 'desktop-runtime'
                turn = dict(turn, turn_id=latest.get('turnId') or turn.get('turn_id'))
        except ThreadUnavailable:
            # A successful discovery with no owner is distinct from a broken
            # socket, timeout, protocol failure or an app still loading.
            no_owner = True
        except (RuntimeError, OSError, TimeoutError, ValueError, TypeError, AttributeError):
            pass
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
            except FileNotFoundError:
                mtime = None
            except OSError:
                # Permission/I/O errors are uncertainty, not a missing file.
                mtime = now
            try:
                activity = rollout_activity(path)
            except OSError:
                mtime = now
        if no_owner and isinstance(started, (int, float)):
            if desktop_starts is None:
                desktop_starts = [p.started.timestamp() if isinstance(p.started, datetime) else None
                                  for p in codex_env.desktop_main_processes()]
            if desktop_starts and all(isinstance(value, (int, float)) for value in desktop_starts):
                generation = min(desktop_starts)
                if started < generation and (mtime is None or mtime < generation):
                    ignored.append(dict(turn, reason='unowned-before-desktop-start'))
                    continue
        recent_mtime = bool(mtime and now - mtime < window_seconds)
        recent_start = bool(age is not None and age < window_seconds)
        pending_tools = int(activity.get("pending_tool_calls") or 0)
        # A quiet reasoning/network wait is still an active turn. A stale
        # inProgress row is uncertainty, not permission to restart the app.
        if turn.get("status") in ("inProgress", "in_progress", "running") or recent_mtime or recent_start or pending_tools:
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
                    "evidence": evidence,
                }
            )
    return {
        "idle": not active,
        "window_seconds": window_seconds,
        "active": active,
        "ignored_history": ignored,
        "app_running": codex_env.app_running(),
        "desktop_app_server": [p.pid for p in codex_env.desktop_app_server_processes()],
    }


def idle_blocked_message(info: Dict[str, Any]) -> str:
    """Shared, localized, actionable detail for switch and continuation errors."""
    tr = Translator()
    details = '\n'.join(
        tr.t("聊天 {thread_id} · 轮次 {turn_id} · {evidence}",
             thread_id=item.get('thread_id'), turn_id=item.get('turn_id'),
             evidence=tr.t("桌面端确认运行中" if item.get('evidence') == 'desktop-runtime' else "运行状态尚未核实"))
        for item in info.get('active') or []
    )
    return tr.t("""暂时无法切换账号，以下聊天仍在运行或状态尚未确认：
{detail}
请等待这些聊天结束；需要续跑时，在切换确认页选择原聊天并勾选续跑。账号尚未更改。""", detail=details)
