from __future__ import annotations

import os
import unittest
from datetime import datetime
from unittest.mock import patch

from codex_relay.authfile import read_auth, write_auth
from codex_relay.codex_env import ProcessInfo, _child_process_env
from codex_relay.store import Store
from codex_relay.switch import SwitchOptions, switch
from codex_relay.util import RelayError

from tests.helpers import (
    make_auth,
    new_store,
    write_active_turn,
    write_rollout,
    write_state_db,
)


class SwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store: Store = new_store()
        self.auth_a = make_auth("acct-a", "a@example.com", refresh_token="refresh-a")
        self.auth_b = make_auth("acct-b", "b@example.com", refresh_token="refresh-b")
        self.profile_a = self.store.add_profile("a", self.auth_a)
        self.profile_b = self.store.add_profile("b", self.auth_b)
        write_auth(self.store.paths.auth_path, self.auth_a)
        self.store.state["active_profile_id"] = self.profile_a["id"]
        self.store.save_state()

    def _common_patches(self):
        process = ProcessInfo(
            pid=4242,
            ppid=1,
            started=datetime.now(),
            command="fake app-server",
            role="desktop-app-server",
        )
        return [
            patch("codex_relay.switch.preflight_processes", return_value=[]),
            patch("codex_relay.switch.codex_env.app_running", return_value=False),
            patch("codex_relay.switch.codex_env.desktop_bundle_processes", return_value=[]),
            patch("codex_relay.switch.codex_env.quit_desktop", return_value=[]),
            patch("codex_relay.switch.codex_env.stop_daemon", return_value=(False, "未运行")),
            patch("codex_relay.switch.codex_env.launch_desktop", return_value=None),
            patch(
                "codex_relay.switch.codex_env.wait_for_desktop_app_server",
                return_value=process,
            ),
            patch("codex_relay.switch.codex_env.open_thread_deeplink", return_value=None),
        ]

    def test_child_launch_env_strips_relay_flags(self) -> None:
        old = {
            key: os.environ.get(key)
            for key in ("CODEX_RELAY_ALLOW_PROCESS_CONTROL", "CODEX_RELAY_TEST_MODE")
        }
        os.environ["CODEX_RELAY_ALLOW_PROCESS_CONTROL"] = "1"
        os.environ["CODEX_RELAY_TEST_MODE"] = "1"
        try:
            env = _child_process_env()
            self.assertNotIn("CODEX_RELAY_ALLOW_PROCESS_CONTROL", env)
            self.assertNotIn("CODEX_RELAY_TEST_MODE", env)
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_switch_writes_target_and_restores_thread(self) -> None:
        patches = self._common_patches()
        for item in patches:
            item.start()
        try:
            with patch("codex_relay.switch.codex_env.open_thread_deeplink") as deeplink:
                result = switch(
                    self.store,
                    SwitchOptions(target="b", mode="desktop", thread="none", verify=False, allow_chatgpt_restart=True),
                )
        finally:
            for item in patches:
                item.stop()
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["to"]["alias"], "b")
        self.assertEqual(read_auth(self.store.paths.auth_path)["tokens"]["refresh_token"], "refresh-b")
        self.assertEqual(self.store.state["active_profile_id"], self.profile_b["id"])
        self.assertEqual(self.store.journal_tail(1)[0]["event"], "switch")

    def test_switch_rolls_back_on_launch_failure(self) -> None:
        patches = self._common_patches()
        for item in patches:
            item.start()
        try:
            with patch(
                "codex_relay.switch.codex_env.launch_desktop",
                side_effect=RelayError("launch failed"),
            ):
                with self.assertRaises(RelayError):
                    switch(
                        self.store,
                        SwitchOptions(target="b", mode="desktop", thread="none", verify=False, allow_chatgpt_restart=True),
                    )
        finally:
            for item in patches:
                item.stop()
        self.assertEqual(read_auth(self.store.paths.auth_path)["tokens"]["refresh_token"], "refresh-a")
        self.assertEqual(self.store.state["active_profile_id"], self.profile_a["id"])
        last = self.store.journal_tail(1)[0]
        self.assertFalse(last["ok"])

    def test_already_active_does_not_rewrite_or_restart(self) -> None:
        with patch("codex_relay.switch.preflight_processes", return_value=[]):
            with patch("codex_relay.switch.codex_env.open_thread_deeplink") as deeplink:
                result = switch(
                    self.store,
                    SwitchOptions(target="a", mode="desktop", thread="none", verify=True, allow_chatgpt_restart=True),
                )
        self.assertFalse(result["changed"])
        self.assertEqual(result["reason"], "already-active")
        deeplink.assert_not_called()

    def test_preflight_refusal_leaves_auth_untouched(self) -> None:
        with patch(
            "codex_relay.switch.preflight_processes",
            side_effect=RelayError("busy"),
        ):
            with self.assertRaises(RelayError):
                switch(
                    self.store,
                    SwitchOptions(target="b", mode="desktop", thread="none", verify=False, allow_chatgpt_restart=True),
                )
        self.assertEqual(read_auth(self.store.paths.auth_path)["tokens"]["refresh_token"], "refresh-a")

    def test_exhausted_target_is_rejected_before_downtime(self) -> None:
        usage = {
            "primary": {"used_percent": 100, "window_minutes": 300},
            "secondary": {"used_percent": 10, "window_minutes": 10080},
            "rate_limit_reached_type": "rate_limit_reached",
        }
        with patch(
            "codex_relay.switch.verify_profile",
            return_value=(usage, self.auth_b),
        ):
            with self.assertRaises(RelayError) as ctx:
                switch(
                    self.store,
                    SwitchOptions(target="b", mode="desktop", thread="none", verify=True, allow_chatgpt_restart=True),
                )
        self.assertIn("额度已耗尽", str(ctx.exception))
        self.assertEqual(read_auth(self.store.paths.auth_path)["tokens"]["refresh_token"], "refresh-a")

    def test_requires_explicit_chatgpt_restart_permission(self) -> None:
        with self.assertRaises(RelayError) as ctx:
            switch(
                self.store,
                SwitchOptions(target="b", mode="desktop", thread="none", verify=False),
            )
        self.assertIn("allow-chatgpt-restart", str(ctx.exception))
        self.assertEqual(read_auth(self.store.paths.auth_path)["tokens"]["refresh_token"], "refresh-a")

    def test_dry_run_does_not_touch_auth_or_processes(self) -> None:
        with patch("codex_relay.switch.preflight_processes", return_value=[]):
            with patch(
                "codex_relay.switch.idle_check",
                return_value={"idle": True, "active": []},
            ):
                result = switch(
                    self.store,
                    SwitchOptions(
                        target="b",
                        mode="desktop",
                        thread="none",
                        verify=False,
                        dry_run=True,
                    ),
                )
        self.assertEqual(result["reason"], "dry-run")
        self.assertFalse(result["changed"])
        self.assertEqual(read_auth(self.store.paths.auth_path)["tokens"]["refresh_token"], "refresh-a")

    def test_real_desktop_quit_is_refused_in_test_mode(self) -> None:
        from codex_relay import codex_env

        with self.assertRaises(RelayError) as ctx:
            codex_env.quit_desktop()
        self.assertIn("test mode", str(ctx.exception))

    def test_active_turn_blocks_switch_without_force(self) -> None:
        rollout = self.store.paths.codex_home / "sessions" / "active.jsonl"
        write_rollout(rollout)
        write_state_db(
            self.store.paths,
            [
                {
                    "id": "active-thread",
                    "rollout_path": str(rollout),
                    "title": "Active",
                }
            ],
        )
        write_active_turn(self.store.paths, "active-thread", "active-turn")
        with patch("codex_relay.switch.preflight_processes", return_value=[]):
            with patch("codex_relay.switch.codex_env.app_running", return_value=True):
                with self.assertRaises(RelayError) as ctx:
                    switch(
                        self.store,
                        SwitchOptions(target="b", mode="desktop", thread="none", verify=False, allow_chatgpt_restart=True),
                    )
        self.assertIn("正在执行", str(ctx.exception))
        self.assertEqual(read_auth(self.store.paths.auth_path)["tokens"]["refresh_token"], "refresh-a")


if __name__ == "__main__":
    unittest.main()
