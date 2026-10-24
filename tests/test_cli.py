from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_relay.cli import main
from codex_relay.store import RelayPaths
from tests.helpers import make_auth, write_rollout, write_state_db


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="relay-cli-"))
        self.codex_home = self.root / "codex"
        self.relay_home = self.root / "relay"
        self.codex_home.mkdir(parents=True)
        self.auth = make_auth("acct-cli", "cli@example.com")
        (self.codex_home / "auth.json").write_text(json.dumps(self.auth), encoding="utf-8")
        os.chmod(self.codex_home / "auth.json", 0o600)
        self.rollout = self.codex_home / "sessions" / "rollout-cli.jsonl"
        write_rollout(self.rollout, account_id="acct-cli")
        write_state_db(
            RelayPaths(relay_home=self.relay_home, codex_home=self.codex_home),
            [
                {
                    "id": "cli-thread",
                    "rollout_path": str(self.rollout),
                    "title": "CLI Thread",
                    "creator_account_id": "acct-cli",
                }
            ],
        )
        self.env = patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.codex_home),
                "CODEX_RELAY_HOME": str(self.relay_home),
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()

    def run_cli(self, args):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_capture_list_and_status(self) -> None:
        code, out, err = self.run_cli(["add", "--capture", "test"])
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli(["list", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data["profiles"]), 1)
        self.assertTrue(data["profiles"][0]["active"])
        code, out, err = self.run_cli(["status", "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["auth"]["email"], "cli@example.com")

    def test_account_label_and_default_name(self) -> None:
        code, out, err = self.run_cli(["add", "--capture", "test"])
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli(["list", "--json"])
        self.assertEqual(code, 0, err)
        profile = json.loads(out)["profiles"][0]
        self.assertEqual(profile["alias"], "test")
        self.assertTrue(profile["display_name"])
        code, out, err = self.run_cli(["label", "test", "工作账号", "--json"])
        self.assertEqual(code, 0, err)
        labeled = json.loads(out)["profile"]
        self.assertEqual(labeled["alias"], "test")
        self.assertEqual(labeled["display_name"], "工作账号")

    def test_sessions_and_inspect(self) -> None:
        self.run_cli(["add", "--capture", "test"])
        code, out, err = self.run_cli(["sessions", "--json"])
        self.assertEqual(code, 0, err)
        threads = json.loads(out)["threads"]
        self.assertEqual(threads[0]["id"], "cli-thread")
        self.assertEqual(threads[0]["creator_alias"], "test")
        code, out, err = self.run_cli(["inspect", "cli-thread", "--json"])
        self.assertEqual(code, 0, err)
        metrics = json.loads(out)["metrics"]
        self.assertEqual(metrics["reasoning_encrypted_items"], 1)
        self.assertEqual(len(metrics["turns"]), 2)

    def test_report_with_explicit_boundary(self) -> None:
        self.run_cli(["add", "--capture", "test"])
        code, out, err = self.run_cli(
            ["report", "cli-thread", "--since", "2026-01-01T00:30:00Z", "--json"]
        )
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["handoff"]["after"]["turn_id"], "turn-2")
        self.assertEqual(data["handoff"]["after"]["first_cached_tokens"], 0)

    def test_usage_cached_does_not_network(self) -> None:
        self.run_cli(["add", "--capture", "test"])
        code, out, err = self.run_cli(["usage", "--cached", "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(json.loads(out)["profiles"]), 1)


if __name__ == "__main__":
    unittest.main()
