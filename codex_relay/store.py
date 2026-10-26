from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .authfile import auth_summary, read_auth, validate_auth, write_auth
from .config import configured_codex_home
from .util import (
    RelayError,
    atomic_write_json,
    ensure_dir,
    file_lock,
    now_iso,
    parse_iso,
    read_json,
    safe_unlink,
    short_text,
)


ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _safe_component(value: str) -> str:
    if value not in (".", "..") and PROFILE_ID_RE.fullmatch(value):
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


@dataclass
class RelayPaths:
    relay_home: Path
    codex_home: Path

    @property
    def state_path(self) -> Path:
        return self.relay_home / "state.json"

    @property
    def profiles_dir(self) -> Path:
        return self.relay_home / "profiles"

    @property
    def backups_dir(self) -> Path:
        return self.relay_home / "backups"

    @property
    def tmp_dir(self) -> Path:
        return self.relay_home / "tmp"

    @property
    def lock_path(self) -> Path:
        return self.relay_home / "lock"

    @property
    def journal_path(self) -> Path:
        return self.relay_home / "journal.jsonl"

    @property
    def auth_path(self) -> Path:
        return self.codex_home / "auth.json"

    @property
    def config_path(self) -> Path:
        return self.codex_home / "config.toml"

    @property
    def state_db(self) -> Path:
        return self.codex_home / "state_5.sqlite"

    @property
    def thread_history_db(self) -> Path:
        return self.codex_home / "thread_history_1.sqlite"


def default_paths() -> RelayPaths:
    codex_home = Path(
        os.environ.get("CODEX_HOME")
        or configured_codex_home()
        or str(Path.home() / ".codex")
    ).expanduser()
    relay_home = Path(
        os.environ.get("CODEX_RELAY_HOME", str(Path.home() / ".codex-relay"))
    ).expanduser()
    return RelayPaths(relay_home=relay_home, codex_home=codex_home)


def _default_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "active_profile_id": None,
        "profiles": [],
        "last_switch": None,
        "last_thread_id": None,
        "settings": {
            "auto_threshold": 95,
            "desktop_bundle_id": "com.openai.codex",
            "secret_backend": "file",
        },
    }


class Store:
    def __init__(self, paths: Optional[RelayPaths] = None):
        self.paths = paths or default_paths()
        self._state: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ layout
    def ensure_layout(self) -> None:
        ensure_dir(self.paths.relay_home, 0o700)
        ensure_dir(self.paths.profiles_dir, 0o700)
        ensure_dir(self.paths.backups_dir, 0o700)
        ensure_dir(self.paths.tmp_dir, 0o700)

    def secret_path(self, profile_id: str) -> Path:
        return self.paths.profiles_dir / _safe_component(profile_id) / "auth.json"

    # ------------------------------------------------------------------- state
    @property
    def state(self) -> Dict[str, Any]:
        if self._state is None:
            self._state = self._load_state()
        return self._state

    def _load_state(self) -> Dict[str, Any]:
        self.ensure_layout()
        if not self.paths.state_path.exists():
            return _default_state()
        try:
            data = read_json(self.paths.state_path)
        except (OSError, ValueError) as exc:
            raise RelayError(f"状态文件损坏：{self.paths.state_path}（{exc}）") from exc
        if not isinstance(data, dict):
            raise RelayError(f"状态文件结构异常：{self.paths.state_path}")
        state = _default_state()
        state.update(data)
        if not isinstance(state.get("profiles"), list):
            state["profiles"] = []
        settings = state.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        merged = _default_state()["settings"]
        merged.update(settings)
        state["settings"] = merged
        return state

    def reload(self) -> None:
        self._state = self._load_state()

    def save_state(self) -> None:
        self.ensure_layout()
        atomic_write_json(self.paths.state_path, self.state, mode=0o600)

    def lock(self, timeout: float = 20.0):
        return file_lock(self.paths.lock_path, timeout=timeout)

    # ---------------------------------------------------------------- profiles
    def profiles(self) -> List[Dict[str, Any]]:
        return list(self.state.get("profiles") or [])

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        for profile in self.profiles():
            if profile.get("id") == profile_id:
                return profile
        return None

    def resolve(self, needle: str) -> Dict[str, Any]:
        needle = (needle or "").strip()
        if not needle:
            raise RelayError("请提供账号别名")
        exact = []
        prefix = []
        lowered = needle.lower()
        for profile in self.profiles():
            alias = str(profile.get("alias", ""))
            email = str(profile.get("email") or "")
            pid = str(profile.get("id", ""))
            if alias.lower() == lowered or pid == needle or email.lower() == lowered:
                exact.append(profile)
            elif alias.lower().startswith(lowered) or pid.startswith(needle):
                prefix.append(profile)
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise RelayError(f"账号标识不唯一：{needle}")
        if len(prefix) == 1:
            return prefix[0]
        if len(prefix) > 1:
            names = ", ".join(str(p.get("alias")) for p in prefix)
            raise RelayError(f"账号标识有歧义：{needle}（匹配：{names}）")
        raise RelayError(f"没有找到账号：{needle}")

    def add_profile(
        self,
        alias: str,
        auth: Dict[str, Any],
        *,
        backend: str = "file",
        adopt: bool = False,
    ) -> Dict[str, Any]:
        alias = alias.strip()
        if not ALIAS_RE.match(alias):
            raise RelayError("别名只能包含字母、数字、点、下划线和短横线，长度 1-32，且必须以字母或数字开头")
        identity = validate_auth(auth)
        account_id = str(identity["account_id"])
        profile_id = _safe_component(account_id)

        existing = self.get_profile(profile_id)
        now = now_iso()
        profiles = self.profiles()
        display_name = (identity.get("name") or "").strip()
        if not display_name:
            existing_names = {
                str(p.get("display_name") or p.get("name") or "")
                for p in profiles
            }
            number = len(profiles) + 1
            while f"账号{number}" in existing_names:
                number += 1
            display_name = f"账号{number}"
        if existing:
            if not adopt and existing.get("alias") != alias:
                # Keep the original alias stable; an explicit rename is a separate action.
                alias = str(existing.get("alias"))
            existing.update(
                {
                    "email": identity.get("email"),
                    "name": identity.get("name"),
                    "plan": identity.get("plan"),
                    "user_id": identity.get("user_id"),
                    "account_id": account_id,
                    "last_refresh": auth.get("last_refresh"),
                    "updated_at": now,
                    "status": "ok",
                    "display_name": existing.get("display_name") or display_name,
                }
            )
            profile = existing
        else:
            for other in self.profiles():
                if str(other.get("alias", "")).lower() == alias.lower():
                    raise RelayError(f"别名已被另一个账号使用：{alias}")
            profile = {
                "id": profile_id,
                "alias": alias,
                "email": identity.get("email"),
                "name": identity.get("name"),
                "plan": identity.get("plan"),
                "user_id": identity.get("user_id"),
                "account_id": account_id,
                "created_at": now,
                "updated_at": now,
                "last_refresh": auth.get("last_refresh"),
                "display_name": display_name,
                "last_used_at": None,
                "last_verified_at": None,
                "status": "ok",
                "auto_adopted": adopt,
                "secret_backend": backend,
            }
            self.state.setdefault("profiles", []).append(profile)
        self.write_secret(profile_id, auth)
        self.save_state()
        return profile

    def rename_profile(self, needle: str, new_alias: str) -> Dict[str, Any]:
        new_alias = new_alias.strip()
        if not ALIAS_RE.match(new_alias):
            raise RelayError("新别名格式不合法")
        profile = self.resolve(needle)
        for other in self.profiles():
            if other is not profile and str(other.get("alias", "")).lower() == new_alias.lower():
                raise RelayError(f"别名已被使用：{new_alias}")
        profile["alias"] = new_alias
        profile["updated_at"] = now_iso()
        self.save_state()
        return profile

    def remove_profile(self, needle: str) -> Dict[str, Any]:
        profile = self.resolve(needle)
        actual = self.current_profile()
        active_id = actual.get("id") if actual else (self.state.get("active_profile_id") if self.current_auth() is None else None)
        if active_id == profile.get("id"):
            raise RelayError("不能删除当前活动账号；请先切换到其他账号")
        self.state["profiles"] = [p for p in self.profiles() if p.get("id") != profile.get("id")]
        if self.state.get("active_profile_id") == profile.get("id"):
            self.state["active_profile_id"] = None
        self.save_state()
        directory = self.secret_path(str(profile["id"])).parent
        shutil.rmtree(directory, ignore_errors=True)
        return profile

    # ----------------------------------------------------------------- secrets
    def write_secret(self, profile_id: str, auth: Dict[str, Any]) -> None:
        validate_auth(auth)
        path = self.secret_path(profile_id)
        write_auth(path, auth)
        ensure_dir(path.parent, 0o700)

    def read_secret(self, profile_id: str) -> Dict[str, Any]:
        path = self.secret_path(profile_id)
        if not path.exists():
            raise RelayError(f"账号凭据文件缺失：{path}。请重新登录该账号。")
        return read_auth(path)

    def update_secret_metadata(self, profile_id: str, auth: Dict[str, Any]) -> None:
        profile = self.get_profile(profile_id)
        if not profile:
            return
        identity = validate_auth(auth)
        profile.update(
            {
                "email": identity.get("email"),
                "name": identity.get("name"),
                "plan": identity.get("plan"),
                "user_id": identity.get("user_id"),
                "account_id": identity.get("account_id"),
                "last_refresh": auth.get("last_refresh"),
                "updated_at": now_iso(),
                "status": "ok",
            }
        )
        self.save_state()

    def persist_profile_auth(self, profile: Dict[str, Any], auth: Dict[str, Any]) -> None:
        """Persist a rotated credential bundle.

        If this profile is the currently active account, the live auth.json must
        be updated in the same operation. Otherwise a successful OAuth refresh
        would leave ~/.codex/auth.json holding an already-rotated refresh token.
        """
        profile_id = str(profile["id"])
        self.write_secret(profile_id, auth)
        self.update_secret_metadata(profile_id, auth)
        try:
            current = self.current_auth()
            current_account = validate_auth(current, require_refresh=False).get("account_id") if current else None
        except RelayError:
            current_account = None
        if current_account and current_account == profile.get("account_id"):
            write_auth(self.paths.auth_path, auth)

    # --------------------------------------------------------------- auth file
    def current_auth(self) -> Optional[Dict[str, Any]]:
        if not self.paths.auth_path.exists():
            return None
        return read_auth(self.paths.auth_path)

    def current_profile(self) -> Optional[Dict[str, Any]]:
        auth = self.current_auth()
        if not auth:
            return None
        try:
            identity = validate_auth(auth, require_refresh=False)
        except RelayError:
            return None
        account_id = identity.get("account_id")
        for profile in self.profiles():
            if profile.get("account_id") == account_id:
                return profile
        return None

    def snapshot_current(self, *, adopt: bool = True) -> Optional[Dict[str, Any]]:
        auth = self.current_auth()
        if not auth:
            return None
        identity = validate_auth(auth, require_refresh=False)
        profile = self.current_profile()
        if profile:
            self.write_secret(str(profile["id"]), auth)
            self.update_secret_metadata(str(profile["id"]), auth)
            self.state["active_profile_id"] = profile["id"]
            self.save_state()
            return profile
        if not adopt:
            return None
        short = str(identity.get("account_id"))[:8]
        alias = f"captured-{short}"
        profile = self.add_profile(alias, auth, adopt=True)
        self.state["active_profile_id"] = profile["id"]
        self.save_state()
        return profile

    def set_active(self, profile: Dict[str, Any]) -> None:
        self.state["active_profile_id"] = profile.get("id")
        profile["last_used_at"] = now_iso()
        self.save_state()

    # ----------------------------------------------------------------- backups
    def backup(self, label: str = "switch", *, include_auth: bool = True) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.paths.backups_dir / f"{stamp}-{label}"
        suffix = 1
        while target.exists():
            suffix += 1
            target = self.paths.backups_dir / f"{stamp}-{label}-{suffix}"
        ensure_dir(target, 0o700)
        meta = {"created_at": now_iso(), "label": label, "auth_path": str(self.paths.auth_path)}
        if include_auth and self.paths.auth_path.exists():
            data = self.paths.auth_path.read_bytes()
            (target / "auth.json").write_bytes(data)
            os.chmod(target / "auth.json", 0o600)
            meta["auth_sha256"] = hashlib.sha256(data).hexdigest()
        if self.paths.state_path.exists():
            shutil.copy2(self.paths.state_path, target / "state.json")
            os.chmod(target / "state.json", 0o600)
        atomic_write_json(target / "meta.json", meta, mode=0o600)
        keep = self.state.get("settings", {}).get("backup_keep", 0)
        if isinstance(keep, int) and not isinstance(keep, bool) and keep > 0:
            self.prune_backups(keep)
        return target

    def prune_backups(self, keep: int = 0) -> None:
        if keep <= 0 or not self.paths.backups_dir.exists():
            return
        entries = sorted(
            [p for p in self.paths.backups_dir.iterdir() if p.is_dir()],
            key=lambda p: (p.stat().st_mtime_ns, p.name),
        )
        for old in entries[:-keep]:
            shutil.rmtree(old, ignore_errors=True)

    # ----------------------------------------------------------------- journal
    def journal(self, event: Dict[str, Any]) -> None:
        self.ensure_layout()
        payload = dict(event)
        payload.setdefault("at", now_iso())
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        with self.paths.journal_path.open("a", encoding="utf-8") as handle:
            os.chmod(self.paths.journal_path, 0o600)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def journal_tail(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.paths.journal_path.exists():
            return []
        lines = self.paths.journal_path.read_text(encoding="utf-8", errors="replace").splitlines()
        result = []
        for line in lines[-limit:]:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                result.append(value)
        return result

    # ----------------------------------------------------------------- helpers
    def profile_summary(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        usage = profile.get("usage") if isinstance(profile.get("usage"), dict) else None
        return {
            "id": profile.get("id"),
            "alias": profile.get("alias"),
            "display_name": profile.get("display_name") or profile.get("name") or profile.get("alias"),
            "email": profile.get("email"),
            "name": profile.get("name"),
            "plan": profile.get("plan"),
            "account_id": profile.get("account_id"),
            "status": profile.get("status", "ok"),
            "last_refresh": profile.get("last_refresh"),
            "last_used_at": profile.get("last_used_at"),
            "last_verified_at": profile.get("last_verified_at"),
            "last_error": profile.get("last_error"),
            "usage": usage,
        }

    def describe_profile(self, profile: Dict[str, Any]) -> str:
        alias = profile.get("alias")
        email = profile.get("email") or "unknown"
        plan = profile.get("plan") or "?"
        status = profile.get("status") or "ok"
        return f"{alias}  {email}  [{plan}]  {status}"
