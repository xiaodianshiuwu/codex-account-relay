"""Regression gate for the localization catalogs and source integration."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class I18nAuditTests(unittest.TestCase):
    def test_audit_reports_full_coverage(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "i18n_audit.py"), "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "CODEX_RELAY_TEST_MODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertGreater(report["catalog_keys"], 700)
        for language in ("zh-Hans", "zh-Hant", "en", "ja"):
            self.assertEqual(report["languages"][language]["coverage"], 100.0)
        self.assertEqual(report["hardcoded_count"], 0)
        self.assertEqual(report["integration_coverage"], 100.0)
        self.assertEqual(report["unused_keys"], 0)
        self.assertGreater(report["external_keys"], 0)

    def test_generated_strings_are_current(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "build_strings.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
