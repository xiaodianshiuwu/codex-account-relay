"""Regression tests for the 2026-11-22 P0/P1 audit fixes."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import plistlib
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codex_relay import codex_env, guard_cli, threads, usage, util
from codex_relay.appserver import JsonRpcProcess, RpcError
from codex_relay.authfile import write_auth
from codex_relay.auto_config import save_auto_settings
from codex_relay.cli import _auto_once, cmd_auto, main
from codex_relay.desktop_continue import switch_and_continue
from codex_relay.store import ALIAS_RE, RelayPaths, Store
from codex_relay.switch import SwitchOptions, switch
from codex_relay.util import RelayError, redact
from desktop_guard.desktop import Desktop
from desktop_guard.policy import quota as guard_quota
from desktop_guard.remote import RemoteReadOnlyServer
from desktop_guard.rpc import ReadOnlyServer
from tests.helpers import make_auth, new_store


THREAD = "11111111-1111-4111-8111-111111111111"
REMOTE_HOST = "remote-ssh-discovered:example"
REMOTE_DESCRIPTOR = {
    "id": REMOTE_HOST,
    "name": "example",
    "target": "example",
    "alias": True,
    "port": None,
    "identity": None,
}


class GuardHttpAndEnvTests(unittest.TestCase):
    """P0-1: Local Guard prefers read-only HTTP when desktop runs; ReadOnlyServer strips env."""

    def test_guard_read_quota_prefers_http_when_desktop_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            auth = make_auth("acct-guard", "guard@example.com")
            write_auth(home / "auth.json", auth)
            future = int(time.time()) + 3600
            http_usage = {
                "checked_at": "2026-11-22T00:00:00Z",
                "source": "http",
                "primary": {"used_percent": 25, "window_minutes": 300, "resets_at": future},
                "secondary": {"used_percent": 40, "window_minutes": 10080, "resets_at": future + 1000},
                "rate_limit_reached_type": None,
                "ordinary_usage_allowed": True,
                "spend_control": None,
                "rate_limits_raw": {"allowed": True, "limit_reached": False},
            }
            args = SimpleNamespace(host="local", home=home, app=Path("/synthetic"), limit_id="codex")
            with patch.object(guard_cli, "_local_desktop_running", return_value=True), \
                 patch("codex_relay.usage.fetch_usage_http", return_value=http_usage) as http_call, \
                 patch.object(guard_cli, "server", side_effect=AssertionError("ReadOnlyServer must not be spawned")):
                raw = guard_cli.read_quota(args)
            http_call.assert_called_once()
            parsed = guard_quota(raw, time.time(), threshold=5, resume_remaining=10, limit_id="codex")
            self.assertTrue(parsed.known)
            self.assertFalse(parsed.low)
            self.assertTrue(parsed.ready)
            self.assertEqual(parsed.windows[0]["remaining"], 75)

    def test_guard_http_adapter_propagates_limit_reached_and_spend_control(self) -> None:
        future = int(time.time()) + 3600
        reached_raw = guard_cli._http_usage_to_rate_limits({
            "primary": {"used_percent": 10, "window_minutes": 300, "resets_at": future},
            "ordinary_usage_allowed": False,
        })
        q = guard_quota(reached_raw, time.time())
        self.assertTrue(q.known)
        self.assertFalse(q.ready)

        spend_raw = guard_cli._http_usage_to_rate_limits({
            "primary": {"used_percent": 10, "window_minutes": 300, "resets_at": future},
            "spend_control": {"reached": True},
        })
        q_spend = guard_quota(spend_raw, time.time())
        self.assertFalse(q_spend.known)

    def test_guard_read_quota_falls_back_when_desktop_stopped_or_custom_bucket(self) -> None:
        fake_result = {"rateLimits": {"primary": {"usedPercent": 10, "resetsAt": int(time.time()) + 300, "windowDurationMins": 300}}}

        class FakeReader:
            def bounded(self, _budget):
                return contextlib.nullcontext()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def query(self, method):
                assert method == "account/rateLimits/read"
                return fake_result

        # Case 1: desktop not running
        args = SimpleNamespace(host="local", home=Path("/synthetic"), app=Path("/synthetic"), limit_id="codex")
        with patch.object(guard_cli, "_local_desktop_running", return_value=False), \
             patch.object(guard_cli, "server", return_value=FakeReader()) as srv:
            self.assertEqual(guard_cli.read_quota(args), fake_result)
            srv.assert_called_once()

        # Case 2: desktop running, but explicit non-codex limit_id
        args_other = SimpleNamespace(host="local", home=Path("/synthetic"), app=Path("/synthetic"), limit_id="custom-bucket")
        with patch.object(guard_cli, "_local_desktop_running", return_value=True), \
             patch.object(guard_cli, "server", return_value=FakeReader()) as srv:
            self.assertEqual(guard_cli.read_quota(args_other), fake_result)
            srv.assert_called_once()

    def test_host_snapshot_local_uses_http_for_quota_when_desktop_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            future = int(time.time()) + 3600
            http_rpc_result = {
                "rateLimitsByLimitId": {
                    "codex": {
                        "primary": {"usedPercent": 20, "resetsAt": future, "windowDurationMins": 300}
                    }
                }
            }

            class FakeLocalReader:
                def bounded(self, _budget):
                    return contextlib.nullcontext()
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def query(self, method, params=None):
                    if method == "account/rateLimits/read":
                        raise AssertionError("host_snapshot must not call account/rateLimits/read via RPC when desktop runs")
                    return {"data": [{"id": THREAD, "name": "Chat", "cwd": "/tmp"}]}

            args = SimpleNamespace(home=home, app=Path("/synthetic"))
            with patch.object(guard_cli, "binary_for", return_value=Path("/synthetic/codex")), \
                 patch.object(guard_cli, "ReadOnlyServer", return_value=FakeLocalReader()), \
                 patch.object(guard_cli, "_local_desktop_running", return_value=True), \
                 patch.object(guard_cli, "_read_local_quota_http", return_value=http_rpc_result) as http_q:
                snap = guard_cli.host_snapshot(args, {"id": "local", "name": "本机"})
            http_q.assert_called_once()
            self.assertTrue(snap["connected"])
            self.assertTrue(snap["quotaKnown"])
            self.assertEqual(snap["windows"][0]["remaining"], 80)

    def test_readonly_server_strips_openai_env_vars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "fake-codex"
            binary.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line)\n"
                "    if 'id' not in msg: continue\n"
                "    if msg['method'] == 'initialize':\n"
                "        print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
                "    else:\n"
                "        leaked = {k: os.environ.get(k) for k in ('OPENAI_API_KEY', 'CODEX_ACCESS_TOKEN', 'CODEX_API_KEY', 'OPENAI_BASE_URL') if k in os.environ}\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'leaked': leaked, 'codex_home': os.environ.get('CODEX_HOME')}}), flush=True)\n",
                encoding="utf-8",
            )
            binary.chmod(0o700)
            injected = {
                "OPENAI_API_KEY": "sk-secret12345678",
                "CODEX_ACCESS_TOKEN": "tok-secret",
                "CODEX_API_KEY": "sk-codex12345678",
                "OPENAI_BASE_URL": "https://evil.example/v1",
            }
            with patch.dict(os.environ, injected, clear=False):
                with ReadOnlyServer(binary, tmp, timeout=5) as rpc:
                    result = rpc.query("thread/read")
            self.assertEqual(result["leaked"], {})
            self.assertEqual(result["codex_home"], tmp)


class RoleClassificationTests(unittest.TestCase):
    """P0-2: _role_for_command recognizes Contents/Resources/codex and paths with spaces."""

    def test_embedded_resources_codex_and_spaces(self) -> None:
        cases = [
            ("/Applications/ChatGPT.app/Contents/Resources/codex app-server --stdio", "desktop-app-server"),
            ("/Applications/ChatGPT.app/Contents/Resources/codex --version", "desktop-codex"),
            ("/Applications/Codex.app/Contents/Resources/codex app-server", "desktop-app-server"),
            ("/Applications/Codex.app/Contents/Resources/codex", "desktop-codex"),
            ("/Users/John Doe/Applications/ChatGPT.app/Contents/MacOS/ChatGPT --background", "desktop-main"),
            ("/Users/John Doe/Applications/Codex.app/Contents/MacOS/Codex", "desktop-main"),
            ("/Applications/Codex 2.app/Contents/MacOS/Codex --flag", "desktop-main"),
            ("/Users/John Doe/Applications/ChatGPT.app/Contents/Resources/codex app-server", "desktop-app-server"),
            ("/Users/John Doe/Applications/Codex.app/Contents/Resources/codex app-server", "desktop-app-server"),
            ("/Users/John Doe/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex app-server", "desktop-app-server"),
            ("/Users/John Doe/Applications/Codex 2.app/Contents/Resources/codex-code-mode-host", "helper"),
            ("/Applications/ChatGPT 2.app/Contents/Frameworks/ChatGPT Helper", "other"),
            ('"/Users/John Doe/bin/codex" app-server --listen stdio://', "cli-app-server"),
            ("/Applications/ChatGPT.app/Contents/Resources/codex app-server daemon start", "daemon"),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(codex_env._role_for_command(command), expected)


class InstallerSmokeTests(unittest.TestCase):
    """P0-4: install.sh copies desktop_guard/ excluding __pycache__ and installed CLI runs guard."""

    def test_install_sh_isolated_smoke(self) -> None:
        if sys.platform == "win32":
            self.skipTest("POSIX installer smoke test")
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp) / "share"
            bin_dir = Path(tmp) / "bin"
            relay_home = Path(tmp) / "relay"
            codex_home = Path(tmp) / "codex"
            codex_home.mkdir()
            env = dict(
                os.environ,
                CODEX_RELAY_SHARE=str(share),
                CODEX_RELAY_BIN_DIR=str(bin_dir),
                CODEX_RELAY_PYTHON=sys.executable,
            )
            install = subprocess.run(
                ["/bin/sh", str(root / "install.sh")],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
            self.assertTrue((share / "codex_relay" / "__init__.py").is_file())
            self.assertTrue((share / "desktop_guard" / "__init__.py").is_file())
            self.assertEqual(list(share.rglob("__pycache__")), [])

            run_env = {
                "PATH": "/usr/bin:/bin",
                "HOME": tmp,
                "CODEX_RELAY_HOME": str(relay_home),
                "CODEX_HOME": str(codex_home),
                "CODEX_RELAY_TEST_MODE": "1",
            }
            # Run from /tmp with no PYTHONPATH so imports MUST come from installed $SHARE.
            ver = subprocess.run(
                [str(bin_dir / "codex-relay"), "--version"],
                cwd=tmp,
                env=run_env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
            guard_status = subprocess.run(
                [str(bin_dir / "codex-relay"), "--lang", "en", "guard", "status"],
                cwd=tmp,
                env=run_env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(guard_status.returncode, 0, guard_status.stderr + guard_status.stdout)
            self.assertEqual(json.loads(guard_status.stdout), [])


class LockScopeAndUsageTests(unittest.TestCase):
    """P1-1, P1-2, P1-6: Short lock scope for refreshes, clear last_error, usage guards."""

    def test_list_refresh_and_usage_do_not_hold_store_lock_during_fetch(self) -> None:
        store = new_store()
        auth_a = make_auth("acct-a", "a@example.com")
        auth_b = make_auth("acct-b", "b@example.com")
        store.add_profile("a", auth_a)
        store.add_profile("b", auth_b)
        write_auth(store.paths.auth_path, auth_a)
        future = int(time.time()) + 3600
        lock_acquired_during_fetch = []

        def fake_verify(_paths, auth, **_kwargs):
            # Must be able to acquire store.lock() without blocking while network runs.
            with store.lock(timeout=0.5):
                lock_acquired_during_fetch.append(auth["tokens"]["account_id"])
            return (
                {"primary": {"used_percent": 15, "window_minutes": 300, "resets_at": future}},
                auth,
            )

        env = {
            "CODEX_RELAY_HOME": str(store.paths.relay_home),
            "CODEX_HOME": str(store.paths.codex_home),
        }
        with patch.dict(os.environ, env, clear=False), \
             patch("codex_relay.usage.codex_env.codex_processes", return_value=[]), \
             patch("codex_relay.usage.verify_profile", side_effect=fake_verify):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["list", "--refresh", "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(len(payload["profiles"]), 2)
            self.assertEqual(set(lock_acquired_during_fetch), {"acct-a", "acct-b"})

            lock_acquired_during_fetch.clear()
            out2 = io.StringIO()
            with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(io.StringIO()):
                code2 = main(["usage", "--all", "--json"])
            self.assertEqual(code2, 0)
            self.assertEqual(set(lock_acquired_during_fetch), {"acct-a", "acct-b"})

    def test_clear_stale_last_error_when_status_becomes_ok(self) -> None:
        store = new_store()
        auth_a = make_auth("acct-a", "a@example.com")
        auth_b = make_auth("acct-b", "b@example.com")
        prof_a = store.add_profile("a", auth_a)
        prof_b = store.add_profile("b", auth_b)
        write_auth(store.paths.auth_path, auth_a)

        # 1. Store.add_profile updating existing profile clears last_error
        prof_a["status"] = "error"
        prof_a["last_error"] = "stale network error"
        prof_a["last_error_at"] = "2026-01-01T00:00:00Z"
        store.save_state()
        store.add_profile("a", auth_a)
        self.assertIsNone(store.get_profile(prof_a["id"]).get("last_error"))
        self.assertIsNone(store.get_profile(prof_a["id"]).get("last_error_at"))

        # 2. Store.update_secret_metadata clears last_error
        prof_a = store.get_profile(prof_a["id"])
        prof_a["status"] = "error"
        prof_a["last_error"] = "stale"
        prof_a["last_error_at"] = "2026-01-01T00:00:00Z"
        store.update_secret_metadata(prof_a["id"], auth_a)
        self.assertIsNone(store.get_profile(prof_a["id"]).get("last_error"))
        self.assertIsNone(store.get_profile(prof_a["id"]).get("last_error_at"))

        # 3. switch() verify clears last_error
        prof_b["status"] = "error"
        prof_b["last_error"] = "old error"
        prof_b["last_error_at"] = "2026-01-01T00:00:00Z"
        store.save_state()
        future = int(time.time()) + 3600
        good_usage = {"primary": {"used_percent": 10, "window_minutes": 300, "resets_at": future}}
        with patch("codex_relay.switch.verify_profile", return_value=(good_usage, auth_b)), \
             patch("codex_relay.switch.preflight_processes", return_value=[]), \
             patch("codex_relay.switch.idle_check", return_value={"idle": True, "active": []}):
            switch(store, SwitchOptions(target="b", mode="desktop", thread="none", verify=True, dry_run=True))
        refreshed_b = store.get_profile(prof_b["id"])
        self.assertEqual(refreshed_b["status"], "ok")
        self.assertIsNone(refreshed_b.get("last_error"))
        self.assertIsNone(refreshed_b.get("last_error_at"))

    def test_switch_rejects_unknown_usage_without_force(self) -> None:
        store = new_store()
        auth_a = make_auth("acct-a", "a@example.com")
        auth_b = make_auth("acct-b", "b@example.com")
        store.add_profile("a", auth_a)
        store.add_profile("b", auth_b)
        write_auth(store.paths.auth_path, auth_a)

        stale_usage = {"primary": {"used_percent": float("nan"), "window_minutes": 300}}
        with patch("codex_relay.switch.verify_profile", return_value=(stale_usage, auth_b)):
            with self.assertRaises(RelayError) as ctx:
                switch(store, SwitchOptions(target="b", mode="desktop", thread="none", verify=True, allow_chatgpt_restart=True))
        self.assertIn("额度信息不可用", str(ctx.exception))

        # With --force and dry_run it records a note rather than raising
        with patch("codex_relay.switch.verify_profile", return_value=(stale_usage, auth_b)), \
             patch("codex_relay.switch.preflight_processes", return_value=[]):
            res = switch(store, SwitchOptions(target="b", mode="desktop", thread="none", verify=True, force=True, dry_run=True))
        self.assertTrue(any("额度信息不可用" in n for n in res["notes"]))

    def test_usage_finite_nan_and_bool_guards(self) -> None:
        self.assertEqual(usage._window_from_http({"limit_window_seconds": True})["window_minutes"], None)
        self.assertEqual(usage._window_from_http({"limit_window_seconds": float("inf")})["window_minutes"], None)
        self.assertEqual(usage._window_from_http({"limit_window_seconds": 3600})["window_minutes"], 60)
        self.assertFalse(usage.usage_known({"primary": {"used_percent": 10, "resets_at": True}}))
        self.assertEqual(usage.max_used_percent({"primary": {"used_percent": float("nan")}, "secondary": {"used_percent": 42}}), 42.0)
        self.assertEqual(usage.max_used_percent({"primary": {"used_percent": True}}), 0.0)
        self.assertEqual(usage.max_used_percent({"primary": {"used_percent": float("inf")}}), 0.0)
        self.assertEqual(usage._window_text({"used_percent": float("nan"), "window_minutes": True, "resets_at": True}, usage.Translator("en")), "-")
        self.assertEqual(usage._window_text({"used_percent": True}, usage.Translator("en")), "-")


class AliasBackupAndGuardMutexTests(unittest.TestCase):
    """P1-3..8: Alias deduplication, atomic backups, SSH guard mutex, killpg."""

    def test_snapshot_current_sanitizes_and_deduplicates_aliases(self) -> None:
        store = new_store()
        # Same first 8 chars with unsafe characters
        auth1 = make_auth("org/acct:one with spaces", "one@example.com")
        auth2 = make_auth("org/acct:two with spaces", "two@example.com")
        write_auth(store.paths.auth_path, auth1)
        p1 = store.snapshot_current(adopt=True)
        self.assertRegex(p1["alias"], ALIAS_RE)

        # Force collision with same prefix
        auth3 = make_auth("samepref-1111", "p1@example.com")
        auth4 = make_auth("samepref-2222", "p2@example.com")
        write_auth(store.paths.auth_path, auth3)
        p3 = store.snapshot_current(adopt=True)
        write_auth(store.paths.auth_path, auth4)
        p4 = store.snapshot_current(adopt=True)
        self.assertEqual(p3["alias"], "captured-samepref")
        self.assertEqual(p4["alias"], "captured-samepref-2")
        self.assertRegex(p4["alias"], ALIAS_RE)

        write_auth(store.paths.auth_path, auth2)
        p2 = store.snapshot_current(adopt=True)
        self.assertRegex(p2["alias"], ALIAS_RE)

    def test_backup_is_atomic_private_and_prune_ignores_dotfiles(self) -> None:
        store = new_store()
        write_auth(store.paths.auth_path, make_auth("acct-bkp"))
        store.save_state()
        bkp = store.backup("atomic-test")
        self.assertTrue((bkp / "auth.json").is_file())
        self.assertTrue((bkp / "state.json").is_file())
        self.assertTrue((bkp / "meta.json").is_file())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(bkp.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((bkp / "auth.json").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((bkp / "state.json").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((bkp / "meta.json").stat().st_mode), 0o600)
        # No leftover staging dotfiles
        hidden = [p for p in store.paths.backups_dir.iterdir() if p.name.startswith(".")]
        self.assertEqual(hidden, [])
        # Hidden dir is ignored by prune_backups
        marker = store.paths.backups_dir / ".staging-keep"
        marker.mkdir()
        store.backup("atomic-test-2")
        store.prune_backups(1)
        self.assertTrue(marker.exists())

    def test_guard_mutex_only_local_and_check_outside_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relay_home = root / "relay"
            codex_home = root / "codex"
            state_dir = root / "guard"
            codex_home.mkdir()
            with patch.dict(os.environ, {"CODEX_RELAY_HOME": str(relay_home), "CODEX_HOME": str(codex_home)}):
                save_auto_settings({"enabled": True, "allow_chatgpt_restart": True})
                local_args = SimpleNamespace(
                    host="local",
                    thread_id=THREAD,
                    app=root / "ChatGPT.app",
                    home=codex_home,
                    state_dir=state_dir,
                    threshold=5,
                    resume_remaining=10,
                    interval=10,
                    limit_id="codex",
                    max_resumes=0,
                    requested_at_ns=1,
                    continuation="continue",
                )
                with self.assertRaisesRegex(RuntimeError, "请先关闭自动切号或重启许可"):
                    guard_cli.background(local_args)

                # Remote host is NOT blocked by local auto-switch mutex and does NOT acquire AccountStore lock
                remote_args = SimpleNamespace(**dict(local_args.__dict__, host=REMOTE_HOST))
                with patch.object(guard_cli, "check", return_value={"kind": "blocked", "reason": "remote-checked"}) as chk, \
                     patch("codex_relay.store.Store.lock", side_effect=AssertionError("remote guard must not lock local account store")):
                    with self.assertRaisesRegex(RuntimeError, "remote-checked"):
                        guard_cli.background(remote_args)
                    chk.assert_called_once()

                # Local check() executes BEFORE AccountStore.lock()
                save_auto_settings({"enabled": False, "allow_chatgpt_restart": False})
                order = []

                @contextlib.contextmanager
                def fake_lock(self_store, timeout=20.0):
                    order.append("lock_enter")
                    try:
                        yield
                    finally:
                        order.append("lock_exit")

                def fake_check(_args, _tid):
                    order.append("check")
                    return {"kind": "blocked", "reason": "stop-after-check"}

                with patch.object(guard_cli, "check", side_effect=fake_check), \
                     patch("codex_relay.store.Store.lock", fake_lock):
                    with self.assertRaisesRegex(RuntimeError, "stop-after-check"):
                        guard_cli.background(local_args)
                self.assertEqual(order, ["check", "lock_enter", "lock_exit"])

    def test_guard_background_timeout_kills_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "guard"
            args = SimpleNamespace(
                host=REMOTE_HOST,
                thread_id=THREAD,
                app=root / "ChatGPT.app",
                home=root / "codex",
                state_dir=state_dir,
                threshold=5,
                resume_remaining=10,
                interval=10,
                limit_id="codex",
                max_resumes=0,
                requested_at_ns=1,
                continuation="continue",
                startup_timeout=0.15,
            )
            fake_cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
            eligibility = {"kind": "running", "canMonitor": True, "turnId": "t1", "connectionKey": "k1"}
            with patch.object(guard_cli, "backend_command", return_value=fake_cmd[:1]), \
                 patch.object(guard_cli, "subprocess") as mock_sub, \
                 patch.object(guard_cli.os, "killpg") as mock_killpg:
                # Use a real Popen-like mock to verify os.killpg is called with process.pid on timeout
                mock_sub.DEVNULL = subprocess.DEVNULL
                mock_sub.TimeoutExpired = subprocess.TimeoutExpired
                proc = mock_sub.Popen.return_value
                proc.pid = 424242
                proc.poll.return_value = None
                proc.wait.return_value = 0
                with self.assertRaisesRegex(RuntimeError, "后台启动未确认"):
                    guard_cli._background_locked(args, eligibility)
                mock_killpg.assert_called_once_with(424242, signal.SIGTERM)


class CliDesktopContinueAndProtocolTests(unittest.TestCase):
    """P1-3..8: CLI fixes, switch_and_continue active check, codex_env, redact, rpc/desktop/remote/threads."""

    def test_switch_and_continue_checks_target_is_active_before_interrupting(self) -> None:
        store = new_store()
        auth_a = make_auth("acct-a", "a@example.com")
        store.add_profile("a", auth_a)
        write_auth(store.paths.auth_path, auth_a)
        with patch("codex_relay.desktop_continue.prepare_handoff", side_effect=AssertionError("must not interrupt active chat")), \
             patch("codex_relay.usage.refresh_usage", side_effect=AssertionError("must not refresh when already active")), \
             patch("codex_relay.switch.switch", side_effect=AssertionError("must not call switch")):
            result = switch_and_continue(
                store,
                SwitchOptions(target="a", mode="desktop", thread=THREAD, verify=True, allow_chatgpt_restart=True),
                "continue",
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(result["reason"], "already-active")
        self.assertFalse(result["continued"])

    def test_cli_auto_force_forwards_to_switch_and_preflight(self) -> None:
        future = int(time.time()) + 3600
        current = {"id": "a", "alias": "a", "status": "ok", "usage": {"primary": {"used_percent": 98, "resets_at": future}}}
        candidate = {"id": "b", "alias": "b", "status": "ok", "usage": {"primary": {"used_percent": 5, "resets_at": future}}}

        class FakeStore:
            def current_profile(self): return current
            def profiles(self): return [current, candidate]

        args = Namespace(
            threshold=None, remaining_threshold=10.0, json=True, timeout=1.0,
            dry_run=False, force=True, allow_chatgpt_restart=True, yes=True,
            strategy="best", prompt="", thread="none",
        )
        with patch("codex_relay.cli.refresh_usage", return_value=None), \
             patch("codex_relay.cli.idle_check", return_value={"idle": False, "active": [{"thread_id": "t1"}]}), \
             patch("codex_relay.switch.preflight_processes", return_value=[]) as preflight, \
             patch("codex_relay.cli.switch", return_value={"thread_id": None}) as sw, \
             contextlib.redirect_stdout(io.StringIO()):
            code = _auto_once(FakeStore(), args)
        self.assertEqual(code, 0)
        preflight.assert_called_once_with("desktop", True)
        self.assertTrue(sw.call_args.args[1].force)

    def test_cli_auto_daemon_prints_threshold_reloads_config_and_exits_promptly(self) -> None:
        store = new_store()
        with patch.dict(os.environ, {"CODEX_RELAY_HOME": str(store.paths.relay_home), "CODEX_HOME": str(store.paths.codex_home)}):
            save_auto_settings({
                "enabled": True,
                "remaining_threshold": 15.0,
                "interval": 60.0,
                "prompt": "first",
                "strategy": "best",
                "allow_chatgpt_restart": True,
            })
            seen_prompts = []

            def fake_once(_store, a):
                seen_prompts.append((a.prompt, a.remaining_threshold))
                # Disable automation immediately so the 1s cancellable sleep exits without waiting 60s
                save_auto_settings({"enabled": False})
                return 2

            args = Namespace(
                once=False, enable=False, threshold=None, remaining_threshold=None,
                prompt=None, interval=None, strategy=None, thread=None,
                continue_timeout=300.0, timeout=60.0, dry_run=False, force=False,
                allow_chatgpt_restart=True, yes=True, json=False, respect_settings=False,
            )
            out = io.StringIO()
            started = time.monotonic()
            with patch("codex_relay.cli._auto_once", side_effect=fake_once), contextlib.redirect_stdout(out):
                code = cmd_auto(args, store)
            elapsed = time.monotonic() - started
            self.assertEqual(code, 2)
            self.assertLess(elapsed, 2.0)
            self.assertIn("85.0%", out.getvalue())
            self.assertNotIn("None%", out.getvalue())
            self.assertEqual(seen_prompts, [("first", 15.0)])

    def test_setup_saves_codex_home_when_cli_undetected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relay_home = root / "relay"
            codex_home = root / "my-codex"
            codex_home.mkdir()
            with patch.dict(os.environ, {"CODEX_RELAY_HOME": str(relay_home), "CODEX_HOME": ""}), \
                 patch("codex_relay.codex_env.which_codex", return_value=None):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    code = main(["setup", "--codex-home", str(codex_home), "--json"])
            self.assertEqual(code, 0)
            data = json.loads(out.getvalue())
            self.assertTrue(data["saved"])
            self.assertIsNone(data["detected"])
            self.assertEqual(data["codex_home"], str(codex_home.resolve()))
            saved = json.loads((relay_home / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["codex_home"], str(codex_home.resolve()))

    def test_lang_en_guard_and_non_json_messages_and_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CODEX_RELAY_HOME": tmp + "/relay", "CODEX_HOME": tmp + "/codex"}):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["--lang", "en", "guard", "status"]), 0)
                self.assertEqual(json.loads(out.getvalue()), [])

                out2 = io.StringIO()
                with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["--lang=en", "guard", "status"]), 0)
                self.assertEqual(json.loads(out2.getvalue()), [])

                # ValueError caught cleanly in _main
                err = io.StringIO()
                with patch("codex_relay.cli.cmd_status", side_effect=ValueError("bad value")), \
                     contextlib.redirect_stderr(err):
                    self.assertEqual(main(["status"]), 1)
                self.assertIn("bad value", err.getvalue())

        # Non-JSON blocked/none messages in _auto_once
        out = io.StringIO()
        with patch("codex_relay.cli.load_auto_settings", return_value={"enabled": False, "allow_chatgpt_restart": False}), \
             contextlib.redirect_stdout(out):
            code = _auto_once(None, Namespace(respect_settings=True, json=False))
        self.assertEqual(code, 2)
        self.assertTrue(out.getvalue().strip())
        self.assertFalse(out.getvalue().lstrip().startswith("{"))

    def test_codex_env_honors_configured_home_and_probes_stale_daemon_socket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            relay_home = Path(tmp) / "relay"
            relay_home.mkdir()
            (relay_home / "config.json").write_text(json.dumps({"codex_home": "~/custom-codex"}), encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_RELAY_HOME": str(relay_home), "CODEX_HOME": ""}, clear=False):
                sock = codex_env.daemon_socket()
                self.assertEqual(sock, Path.home() / "custom-codex" / "app-server-control" / "app-server-control.sock")

        if not hasattr(socket, "AF_UNIX"):
            return
        # Short path under /tmp so AF_UNIX path length (<104) always fits on macOS
        with tempfile.TemporaryDirectory(prefix="cr-", dir="/tmp") as tmp:
            codex_home = Path(tmp)
            sock_path = codex_home / "app-server-control" / "app-server-control.sock"
            sock_path.parent.mkdir(parents=True)
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(sock_path))
            srv.listen(1)
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=False):
                self.assertTrue(codex_env.daemon_running())
                # Close listener while leaving stale socket file behind -> ECONNREFUSED -> False
                srv.close()
                self.assertTrue(sock_path.exists())
                self.assertFalse(codex_env.daemon_running())

    def test_redact_sk_rt_and_error_paths(self) -> None:
        raw = "failed key=sk-proj-abc123def456 refresh=rt_secret987654.xyz123 task-manager Bearer tok123"
        cleaned = redact(raw)
        self.assertNotIn("sk-proj-abc123def456", cleaned)
        self.assertNotIn("rt_secret987654", cleaned)
        self.assertNotIn("tok123", cleaned)
        self.assertIn("task-manager", cleaned)

        rpc_err = RpcError("account/read", -1, "leaked sk-1234567890abcdef and rt_abcdefghij1234")
        self.assertNotIn("sk-1234567890abcdef", str(rpc_err))
        self.assertNotIn("rt_abcdefghij1234", str(rpc_err))

    def test_appserver_monotonic_request_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_rpc.py"
            script.write_text(
                "import json, sys\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line)\n"
                "    if 'id' in msg:\n"
                "        print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': {'seen_id': msg['id']}}), flush=True)\n",
                encoding="utf-8",
            )
            proc = JsonRpcProcess([sys.executable, str(script)], env=dict(os.environ), request_timeout=5.0)
            try:
                r1 = proc.request("initialize", request_id=1)
                r2 = proc.request("a")
                r3 = proc.request("b")
                r4 = proc.request("c")
                self.assertEqual([r1["seen_id"], r2["seen_id"], r3["seen_id"], r4["seen_id"]], [1, 2, 3, 4])
            finally:
                proc.close()

    def test_desktop_positive_timeout_guard_and_safe_close(self) -> None:
        d = Desktop(Path("/synthetic"))
        with d.bounded(0):
            with self.assertRaises(TimeoutError):
                d._remaining()

        # Socket always closes even if follow(False) raises
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        d.sock = sock
        d.owner = "owner"
        d.thread_id = THREAD
        with patch.object(d, "follow", side_effect=ValueError("boom")):
            d.close()
        self.assertIsNone(d.sock)
        self.assertIsNone(d.owner)
        self.assertEqual(sock.fileno(), -1)

    def test_remote_handshake_handles_latin1_banner_and_pre_http_crlfcrlf(self) -> None:
        seed = b"1" * 16
        key = base64.b64encode(seed).decode()
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        banner = "欢迎登录 café\r\n\r\n".encode("utf-8")
        response = (
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Connection: Upgrade\r\n"
            b"Upgrade: websocket\r\n"
            b"X-Banner: caf\xe9\r\n"
            b"Sec-WebSocket-Accept: " + accept.encode("ascii") + b"\r\n\r\n"
        )
        reader = RemoteReadOnlyServer(REMOTE_DESCRIPTOR)
        reader.buffer = banner + response + b"\x81\x00"
        reader.process = SimpleNamespace(stdin=io.BytesIO())
        with patch("desktop_guard.remote.os.urandom", return_value=seed):
            reader._handshake()
        self.assertEqual(reader.buffer, b"\x81\x00")

    def test_threads_tail_seek_and_utf8_byte_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            # Write > 150 KiB of padding lines followed by an unmatched tool call in the tail
            lines = []
            for i in range(1200):
                lines.append(json.dumps({"type": "event_msg", "payload": {"type": "ping", "pad": "x" * 150, "i": i}}))
            lines.append(json.dumps({
                "type": "response_item",
                "timestamp": "2026-11-22T00:00:01Z",
                "payload": {"type": "function_call", "call_id": "call-tail-1"},
            }))
            rollout.write_text("\n".join(lines) + "\n", encoding="utf-8")
            activity = threads.rollout_activity(rollout, tail_lines=50)
            self.assertTrue(activity["available"])
            self.assertEqual(activity["pending_tool_calls"], 1)
            self.assertEqual(activity["pending_types"], ["function_call"])

            # Verify UTF-8 byte count (CJK chars are 3 bytes each in UTF-8)
            utf8_rollout = Path(tmp) / "utf8.jsonl"
            cjk = "中文内容"  # 4 code points, 12 UTF-8 bytes
            history = [{"type": "message", "text": cjk}]
            records = [
                {"type": "response_item", "payload": {"type": "reasoning", "encrypted_content": cjk}},
                {"type": "compacted", "timestamp": "2026-11-22T00:00:00Z", "payload": {"window_number": 1, "replacement_history": history}},
            ]
            utf8_rollout.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
            metrics = threads.rollout_metrics(utf8_rollout)
            self.assertEqual(metrics.reasoning_encrypted_bytes, len(cjk.encode("utf-8")))
            self.assertEqual(metrics.compactions[0].serialized_bytes, len(json.dumps(history, ensure_ascii=False).encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
