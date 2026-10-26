from __future__ import annotations

import os
import stat
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from codex_relay.auto_config import load_auto_settings, save_auto_settings
from codex_relay.auto_continue import continue_thread
from codex_relay.cli import _auto_once, _used_threshold, build_parser


class AutoParserTests(unittest.TestCase):
    def test_auto_once_has_timeout_default(self) -> None:
        args = build_parser().parse_args(["auto", "--once", "--json"])
        self.assertEqual(args.timeout, 60.0)


class AutoSettingsTests(unittest.TestCase):
    def test_round_trip_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "auto.json"
            save_auto_settings(
                {
                    "enabled": True,
                    "prompt": "继续处理，不要重做已完成的部分。",
                    "remaining_threshold": 12.5,
                    "interval": 45,
                    "strategy": "best",
                },
                path=path,
            )
            loaded = load_auto_settings(path)
            self.assertTrue(loaded["enabled"])
            self.assertEqual(loaded["prompt"], "继续处理，不要重做已完成的部分。")
            self.assertEqual(loaded["remaining_threshold"], 12.5)
            self.assertEqual(loaded["interval"], 45)
            self.assertEqual(loaded["strategy"], "best")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_invalid_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "auto.json"
            path.write_text("{not json", encoding="utf-8")
            loaded = load_auto_settings(path)
            self.assertFalse(loaded["enabled"])
            self.assertEqual(loaded["remaining_threshold"], 10.0)
            self.assertEqual(loaded["strategy"], "best")

    def test_remaining_threshold_converts_to_used_threshold(self) -> None:
        self.assertEqual(_used_threshold(Namespace(threshold=None, remaining_threshold=12.5)), 87.5)
        self.assertEqual(_used_threshold(Namespace(threshold=80, remaining_threshold=None)), 80.0)

    def test_below_threshold_does_not_switch(self) -> None:
        profile = {
            "id": "a",
            "alias": "personal",
            "usage": {"primary": {"used_percent": 50.0}, "secondary": {"used_percent": 20.0}},
        }

        class Store:
            def current_profile(self):
                return profile

        args = Namespace(
            threshold=None,
            remaining_threshold=10.0,
            json=True,
            timeout=1.0,
            dry_run=False,
            force=False,
            allow_chatgpt_restart=False,
            yes=False,
            strategy="best",
            prompt="",
        )
        output = __import__("io").StringIO()
        with patch("codex_relay.cli.refresh_usage", return_value=None), __import__("contextlib").redirect_stdout(output):
            result = _auto_once(Store(), args)
        self.assertEqual(result, 2)
        self.assertIn('"threshold": 90.0', output.getvalue())
        self.assertIn('"remaining_threshold": 10.0', output.getvalue())

    def test_switch_sends_continuation_prompt(self) -> None:
        current = {
            "id": "a",
            "alias": "personal",
            "status": "ok",
            "usage": {"primary": {"used_percent": 96.0}},
        }
        candidate = {
            "id": "b",
            "alias": "work",
            "status": "ok",
            "usage": {"primary": {"used_percent": 10.0}},
        }

        class Store:
            def current_profile(self):
                return current

            def profiles(self):
                return [current, candidate]

        args = Namespace(
            threshold=None,
            remaining_threshold=10.0,
            json=True,
            timeout=1.0,
            dry_run=False,
            force=False,
            allow_chatgpt_restart=True,
            yes=True,
            strategy="best",
            prompt="继续处理",
            continue_timeout=5.0,
            thread="thread-1",
        )
        output = __import__("io").StringIO()
        with patch("codex_relay.cli.refresh_usage", return_value=None), patch(
            "codex_relay.cli.idle_check", return_value={"idle": True, "active": [], "app_running": False}
        ), patch("codex_relay.cli.switch", return_value={"thread_id": "thread-1"}), patch(
            "codex_relay.cli.continue_desktop_thread", return_value="new-turn"
        ) as cont, patch("codex_relay.cli.prepare_handoff", return_value={"thread_id":"thread-1"}) as prepare, __import__("contextlib").redirect_stdout(output):
            result = _auto_once(Store(), args)
        self.assertEqual(result, 0)
        cont.assert_called_once_with(__import__("unittest.mock").mock.ANY, {"thread_id":"thread-1"}, "继续处理")
        self.assertIn('"continued": true', output.getvalue())


class ContinueTests(unittest.TestCase):
    def test_continue_thread_calls_resume_with_prompt(self) -> None:
        class Result:
            returncode = 0
            stdout = '{"type":"turn.completed"}\n'
            stderr = ""

        with tempfile.TemporaryDirectory() as root:
            with patch("codex_relay.auto_continue.which_codex", return_value="/usr/bin/codex"), patch(
                "codex_relay.auto_continue.run_capture", return_value=Result()
            ) as run:
                output = continue_thread("thread-123", "继续", timeout=42)
            self.assertEqual(output, '{"type":"turn.completed"}')
            command = run.call_args.args[0]
            self.assertEqual(command[:3], ["/usr/bin/codex", "exec", "resume"])
            self.assertIn("--json", command)
            self.assertIn("thread-123", command)
            self.assertEqual(command[-1], "-")
            self.assertEqual(run.call_args.kwargs["input_text"], "继续")
            self.assertEqual(run.call_args.kwargs["timeout"], 42)


if __name__ == "__main__":
    unittest.main()
