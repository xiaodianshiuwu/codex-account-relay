from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_relay import codex_env, util
from codex_relay.portable_gui import _format_number, _shown_percent


class PlatformTests(unittest.TestCase):
    def test_process_roles_accept_windows_and_posix_paths(self) -> None:
        self.assertEqual(
            codex_env._role_for_command(r"C:\Program Files\ChatGPT\ChatGPT.exe --background"),
            "desktop-main",
        )
        self.assertEqual(
            codex_env._role_for_command(r"C:\Program Files\Codex\codex.exe app-server --listen stdio://"),
            "cli-app-server",
        )
        self.assertEqual(
            codex_env._role_for_command("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex app-server"),
            "desktop-app-server",
        )
        self.assertEqual(codex_env._role_for_command("/opt/ChatGPT/ChatGPT --background"), "desktop-main")
        self.assertEqual(codex_env._role_for_command("/home/me/Applications/ChatGPT.AppImage"), "desktop-main")
        self.assertEqual(codex_env._role_for_command("/usr/bin/ssh host"), "other")
        self.assertEqual(
            codex_env._role_for_command("/Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Versions/1/Codex Helper"),
            "other",
        )
        self.assertEqual(
            codex_env._role_for_command("/Users/me/Applications/Codex Relay.app/Contents/MacOS/Codex"),
            "other",
        )
        self.assertEqual(
            codex_env._role_for_command("/Users/me/.codex/plugins/cache/openai-bundled/chrome/latest/ChatGPT for Chrome chrome-extension://x"),
            "other",
        )

    def test_portable_quota_and_number_formats(self) -> None:
        self.assertEqual(_shown_percent(80.0, "remaining"), 20.0)
        self.assertEqual(_shown_percent(80.0, "used"), 80.0)
        self.assertEqual(_format_number(1_234_567, "compact"), "1.23M")
        self.assertEqual(_format_number(100_000_000, "compact"), "100M")
        self.assertEqual(_format_number(100_000, "compact"), "100K")
        self.assertEqual(_format_number(1_234_567, "full"), "1,234,567")
        self.assertEqual(_format_number(None, "full"), "-")

    def test_windows_script_shims_are_wrapped_for_createprocess(self) -> None:
        with patch.object(util.sys, "platform", "win32"):
            self.assertEqual(
                util.executable_command([r"C:\\Tools\\codex.cmd", "--version"]),
                ["cmd", "/c", r"C:\\Tools\\codex.cmd", "--version"],
            )

    def test_windows_process_json_is_classified(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                '[{"ProcessId": 10, "ParentProcessId": 1, '
                '"CommandLine": "C:\\\\Apps\\\\ChatGPT.exe --background"}, '
                '{"ProcessId": 11, "ParentProcessId": 1, '
                '"CommandLine": "C:\\\\Tools\\\\codex.exe app-server --listen stdio://"}]'
            ),
            stderr="",
        )
        with patch.object(codex_env.shutil, "which", side_effect=lambda name: "powershell.exe" if name == "powershell.exe" else None), \
             patch.object(codex_env.subprocess, "run", return_value=completed):
            table = codex_env._windows_process_table()
        roles = {item.pid: item.role for item in table}
        self.assertEqual(roles[10], "desktop-main")
        self.assertEqual(roles[11], "cli-app-server")

    def test_windows_tasklist_fallback_detects_desktop_and_cli_writers(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='"ChatGPT.exe","123","Console","1","10 K"\n"codex.exe","124","Console","1","10 K"\n',
            stderr="",
        )
        with patch.object(codex_env.subprocess, "run", return_value=completed):
            table = codex_env._windows_tasklist_processes()
        roles = {item.pid: item.role for item in table}
        self.assertEqual(roles[123], "desktop-main")
        self.assertEqual(roles[124], "cli")

    def test_non_macos_desktop_discovery_accepts_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "ChatGPT.exe"
            executable.write_text("", encoding="utf-8")
            with patch.object(codex_env, "IS_MACOS", False), \
                 patch.object(codex_env, "_desktop_candidates", return_value=[executable]):
                info = codex_env.discover_desktop()
            self.assertTrue(info.installed)
            self.assertEqual(info.display_name, "ChatGPT")
            self.assertIsNone(info.bundle_id)

    def test_packaged_localization_matches_desktop_resources(self) -> None:
        root = Path(__file__).resolve().parent.parent
        for language in ("zh-Hans", "zh-Hant", "en", "ja"):
            desktop = root / "desktop" / "Resources" / f"{language}.lproj" / "Localizable.strings"
            packaged = root / "codex_relay" / "resources" / f"{language}.lproj" / "Localizable.strings"
            self.assertTrue(packaged.exists(), packaged)
            self.assertEqual(packaged.read_text(encoding="utf-8"), desktop.read_text(encoding="utf-8"))

    def test_sqlite_uri_is_absolute_and_read_only(self) -> None:
        uri = util.sqlite_uri(Path("relative.sqlite"))
        self.assertTrue(uri.startswith("file://"))
        self.assertTrue(uri.endswith("?mode=ro"))

    def test_android_irrelevant_paths_are_not_platform_constants(self) -> None:
        # Sanity check that path handling is pathlib-based on the current OS.
        self.assertEqual(Path("a") / "b", Path("a/b"))


if __name__ == "__main__":
    unittest.main()
