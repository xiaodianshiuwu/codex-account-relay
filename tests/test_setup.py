from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from codex_relay import config
from codex_relay.cli import main
from codex_relay.store import default_paths
from codex_relay.util import which_codex


class SetupTests(unittest.TestCase):
    def test_manual_codex_config_round_trip(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="relay-setup-"))
        relay_home = root / "relay"
        codex_home = root / "codex"
        codex_home.mkdir()
        env = {
            "CODEX_RELAY_HOME": str(relay_home),
            "CODEX_HOME": "",
            "CODEX_RELAY_TEST_MODE": "1",
            "PATH": "/usr/bin:/bin",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CODEX_RELAY_CODEX", None)
            with redirect_stdout(__import__("io").StringIO()), redirect_stderr(__import__("io").StringIO()):
                code = main([
                    "--lang", "en", "setup",
                    "--codex", sys.executable,
                    "--codex-home", str(codex_home),
                    "--json",
                ])
            self.assertEqual(code, 0)
            saved = json.loads((relay_home / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["codex_binary"], str(Path(sys.executable).resolve()))
            self.assertEqual(saved["codex_home"], str(codex_home.resolve()))
            self.assertEqual(which_codex(), str(Path(sys.executable).resolve()))
            self.assertEqual(default_paths().codex_home, codex_home.resolve())
            self.assertEqual(config.config_path(), relay_home / "config.json")

    def test_setup_rejects_missing_binary(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="relay-setup-bad-"))
        env = {
            "CODEX_RELAY_HOME": str(root / "relay"),
            "CODEX_HOME": "",
            "CODEX_RELAY_TEST_MODE": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CODEX_RELAY_CODEX", None)
            with redirect_stdout(__import__("io").StringIO()), redirect_stderr(__import__("io").StringIO()):
                code = main(["--lang", "en", "setup", "--codex", str(root / "missing")])
            self.assertNotEqual(code, 0)
            self.assertFalse((root / "relay" / "config.json").exists())


if __name__ == "__main__":
    unittest.main()
