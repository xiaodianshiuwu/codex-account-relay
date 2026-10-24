from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_relay.appserver import fetch_usage, login_in_isolated_home, parse_usage_response
from codex_relay.util import RelayError

from tests.helpers import make_auth, new_store


FAKE_CODEX = r'''#!/usr/bin/python3
import json, os, sys, time
home = os.environ["CODEX_HOME"]
auth_path = os.path.join(home, "auth.json")
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method")
    if "id" not in msg:
        continue
    result = None
    error = None
    if method == "initialize":
        result = {"userAgent": "fake", "codexHome": home, "platformFamily": "unix", "platformOs": "macos"}
    elif method == "account/read":
        with open(auth_path, "r", encoding="utf-8") as handle:
            auth = json.load(handle)
        auth["tokens"]["refresh_token"] = "rotated-refresh"
        auth["last_refresh"] = "2026-01-01T00:00:00Z"
        with open(auth_path, "w", encoding="utf-8") as handle:
            json.dump(auth, handle)
        if os.environ.get("FAKE_FAIL_ACCOUNT") == "1":
            error = {"code": -32000, "message": "simulated failure after token rotation"}
        else:
            result = {
                "account": {"type": "chatgpt", "email": "fake@example.com", "planType": "plus"},
                "requiresOpenaiAuth": True,
            }
    elif method == "account/rateLimits/read":
        result = {
            "accountId": "acct-fake",
            "rateLimits": {
                "planType": "plus",
                "primary": {"usedPercent": 42, "windowDurationMins": 300, "resetsAt": 123},
                "secondary": {"usedPercent": 7, "windowDurationMins": 10080, "resetsAt": 456},
            },
        }
    else:
        error = {"code": -32601, "message": "unknown method"}
    if error is not None:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": error}), flush=True)
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
'''


def make_fake_codex(root: Path) -> Path:
    path = root / "fake-codex"
    path.write_text(FAKE_CODEX, encoding="utf-8")
    os.chmod(path, 0o755)
    return path


@unittest.skipIf(sys.platform == "win32", "The fake Codex process uses POSIX shell scripts")
class AppServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = new_store()
        self.fake = make_fake_codex(self.store.paths.relay_home)
        self.auth = make_auth("acct-fake", "fake@example.com")

    def test_fetch_usage_persists_rotated_token(self) -> None:
        persisted = []
        with patch("codex_relay.appserver.which_codex", return_value=str(self.fake)):
            usage, refreshed = fetch_usage(
                self.store.paths,
                self.auth,
                persist_callback=lambda auth: persisted.append(auth),
            )
        self.assertEqual(usage["plan"], "plus")
        self.assertEqual(usage["primary"]["used_percent"], 42)
        self.assertEqual(usage["primary"]["window_minutes"], 300)
        self.assertEqual(refreshed["tokens"]["refresh_token"], "rotated-refresh")
        self.assertTrue(persisted)
        self.assertEqual(persisted[0]["tokens"]["refresh_token"], "rotated-refresh")

    def test_token_is_persisted_even_when_rpc_fails(self) -> None:
        persisted = []
        old = os.environ.get("FAKE_FAIL_ACCOUNT")
        os.environ["FAKE_FAIL_ACCOUNT"] = "1"
        try:
            with patch("codex_relay.appserver.which_codex", return_value=str(self.fake)):
                with self.assertRaises(RelayError) as ctx:
                    fetch_usage(
                        self.store.paths,
                        self.auth,
                        persist_callback=lambda auth: persisted.append(auth),
                    )
        finally:
            if old is None:
                os.environ.pop("FAKE_FAIL_ACCOUNT", None)
            else:
                os.environ["FAKE_FAIL_ACCOUNT"] = old
        self.assertEqual(persisted[0]["tokens"]["refresh_token"], "rotated-refresh")
        self.assertEqual(getattr(ctx.exception, "refreshed_auth")["tokens"]["refresh_token"], "rotated-refresh")

    def test_login_uses_isolated_home_and_leaves_active_auth_untouched(self) -> None:
        active = self.store.paths.auth_path
        active.write_text(json.dumps(make_auth("active-account", "active@example.com")), encoding="utf-8")
        before = active.read_bytes()
        template = self.store.paths.relay_home / "login-template.json"
        template.write_text(
            json.dumps(make_auth("new-account", "new@example.com")),
            encoding="utf-8",
        )
        fake = self.store.paths.relay_home / "fake-login-codex"
        fake.write_text(
            "#!/bin/sh\ncp \"$FAKE_AUTH\" \"$CODEX_HOME/auth.json\"\nchmod 600 \"$CODEX_HOME/auth.json\"\nexit 0\n",
            encoding="utf-8",
        )
        os.chmod(fake, 0o755)
        old = os.environ.get("FAKE_AUTH")
        os.environ["FAKE_AUTH"] = str(template)
        try:
            with patch("codex_relay.appserver.which_codex", return_value=str(fake)):
                auth = login_in_isolated_home(self.store.paths, timeout=10)
        finally:
            if old is None:
                os.environ.pop("FAKE_AUTH", None)
            else:
                os.environ["FAKE_AUTH"] = old
        self.assertEqual(auth["tokens"]["account_id"], "new-account")
        self.assertEqual(active.read_bytes(), before)

    def test_parse_usage_response_handles_camel_case(self) -> None:
        parsed = parse_usage_response(
            {"account": {"type": "chatgpt", "email": "x@example.com", "planType": "pro"}},
            {
                "accountId": "acct-x",
                "rateLimits": {
                    "planType": "pro",
                    "primary": {"usedPercent": 88, "windowDurationMins": 300, "resetsAt": 999},
                },
            },
        )
        self.assertEqual(parsed["account_id"], "acct-x")
        self.assertEqual(parsed["primary"]["used_percent"], 88)
        self.assertEqual(parsed["plan"], "pro")


if __name__ == "__main__":
    unittest.main()
