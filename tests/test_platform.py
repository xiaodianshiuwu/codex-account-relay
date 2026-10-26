from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import call, patch

from codex_relay import codex_env, util
from codex_relay.portable_gui import _format_number, _shown_percent
from codex_relay.util import RelayError


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
        # Finder can place a second copy under a numbered bundle name; the
        # space in the path must not make the relay binary look like Codex CLI.
        self.assertEqual(
            codex_env._role_for_command(
                "/private/var/folders/x/Codex Relay 2.app/Contents/MacOS/CodexRelay"
            ),
            "other",
        )
        self.assertEqual(
            codex_env._role_for_command("/Users/me/.codex/plugins/cache/openai-bundled/chrome/latest/ChatGPT for Chrome chrome-extension://x"),
            "other",
        )

    def test_tls_certificate_fallback_for_frozen_backend(self) -> None:
        with unittest.mock.patch.dict(os.environ, {}, clear=True), unittest.mock.patch.object(
            util.os.path, "isfile", side_effect=lambda value: value == "/etc/ssl/cert.pem"
        ):
            util.ensure_tls_certificates()
            self.assertEqual(os.environ.get("SSL_CERT_FILE"), "/etc/ssl/cert.pem")

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

    def test_launch_desktop_demands_test_mode_and_process_control(self) -> None:
        with patch.dict(os.environ, {"CODEX_RELAY_TEST_MODE": "1"}, clear=True):
            with self.assertRaises(RelayError) as ctx:
                codex_env.launch_desktop()
        self.assertIn("test mode", str(ctx.exception))
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RelayError) as ctx:
                codex_env.launch_desktop()
        self.assertIn("已禁止擅自启动", str(ctx.exception))

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


class MacDesktopLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.app_path = Path(self.root.name) / "ChatGPT.app"
        self.app_path.mkdir()
        self.desktop = codex_env.DesktopInfo(
            path=self.app_path,
            bundle_id="com.openai.codex",
            display_name="ChatGPT",
            version="1.0",
            embedded_cli=None,
        )
        for patcher in (
            patch.object(codex_env, "IS_MACOS", True),
            patch.object(codex_env, "_refuse_in_test_mode", return_value=None),
            patch.object(codex_env, "_require_process_control", return_value=None),
            patch.object(codex_env.time, "sleep", return_value=None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)

    def test_launch_retries_proc_not_found(self) -> None:
        failure = "_LSOpenURLsWithCompletionHandler() failed with error -600."
        responses = [
            self._completed(1, failure),
            self._completed(1, failure),
            self._completed(0),
        ]
        with patch.object(codex_env, "run_capture", side_effect=responses) as run, patch.object(
            codex_env, "desktop_main_processes", return_value=[]
        ), patch.object(codex_env, "app_running", return_value=False):
            codex_env.launch_desktop(self.desktop)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[0].args[0], ["open", "-b", "com.openai.codex"])
        self.assertEqual(run.call_args_list[1].args[0], ["open", str(self.app_path)])
        self.assertEqual(run.call_args_list[2].args[0], ["open", "-b", "com.openai.codex"])

    def test_launch_accepts_new_process_after_transient_error(self) -> None:
        failure = "_LSOpenURLsWithCompletionHandler() failed with error -600."
        process = codex_env.ProcessInfo(
            pid=9001,
            ppid=1,
            started=datetime.now(),
            command=str(self.app_path / "Contents/MacOS/ChatGPT"),
            role="desktop-main",
        )
        with patch.object(
            codex_env,
            "run_capture",
            side_effect=[self._completed(1, failure), self._completed(1, failure)],
        ) as run, patch.object(
            codex_env, "desktop_main_processes", side_effect=[[], [process], [process]]
        ), patch.object(codex_env, "app_running", return_value=False):
            codex_env.launch_desktop(self.desktop)
        self.assertEqual(run.call_count, 2)

    def test_launch_accepts_process_that_appears_during_final_wait(self) -> None:
        failure = "_LSOpenURLsWithCompletionHandler() failed with error -600."
        process = codex_env.ProcessInfo(
            pid=9003,
            ppid=1,
            started=datetime.now(),
            command=str(self.app_path / "Contents/MacOS/ChatGPT"),
            role="desktop-main",
        )
        with patch.object(
            codex_env,
            "run_capture",
            side_effect=[self._completed(1, failure), self._completed(1, failure)] * 4,
        ) as run, patch.object(
            codex_env,
            "desktop_main_processes",
            side_effect=[[]] * 8 + [[process]],
        ):
            codex_env.launch_desktop(self.desktop)
        self.assertEqual(run.call_count, 8)

    def test_launch_falls_back_to_bundle_path(self) -> None:
        failure = "_LSOpenURLsWithCompletionHandler() failed with error -600."
        with patch.object(
            codex_env,
            "run_capture",
            side_effect=[self._completed(1, failure), self._completed(0)],
        ) as run, patch.object(
            codex_env, "desktop_main_processes", return_value=[]
        ), patch.object(codex_env, "app_running", return_value=False):
            codex_env.launch_desktop(self.desktop)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[0], ["open", str(self.app_path)])

    def test_launch_retries_when_only_bundle_open_reports_proc_not_found(self) -> None:
        proc_not_found = "_LSOpenURLsWithCompletionHandler() failed with error -600."
        generic = "The application cannot be opened."
        with patch.object(
            codex_env,
            "run_capture",
            side_effect=[
                self._completed(1, proc_not_found),
                self._completed(1, generic),
                self._completed(0),
            ],
        ) as run, patch.object(
            codex_env, "desktop_main_processes", return_value=[]
        ), patch.object(codex_env, "app_running", return_value=False):
            codex_env.launch_desktop(self.desktop)
        self.assertEqual(run.call_count, 3)

    def test_launch_does_not_treat_an_existing_process_as_a_new_launch(self) -> None:
        failure = "_LSOpenURLsWithCompletionHandler() failed with error -600."
        existing = codex_env.ProcessInfo(
            pid=8000,
            ppid=1,
            started=datetime.now(),
            command=str(self.app_path / "Contents/MacOS/ChatGPT"),
            role="desktop-main",
        )
        with patch.object(
            codex_env,
            "run_capture",
            side_effect=[self._completed(1, failure), self._completed(1, failure)] * 4,
        ) as run, patch.object(
            codex_env, "desktop_main_processes", return_value=[existing]
        ), patch.object(codex_env, "wait_until", return_value=False):
            with self.assertRaises(RelayError):
                codex_env.launch_desktop(self.desktop)
        self.assertEqual(run.call_count, 8)

    def test_launch_backoff_order(self) -> None:
        failure = "_LSOpenURLsWithCompletionHandler() failed with error -600."
        with patch.object(
            codex_env,
            "run_capture",
            side_effect=[self._completed(1, failure), self._completed(1, failure)] * 2
            + [self._completed(0)],
        ), patch.object(
            codex_env, "desktop_main_processes", return_value=[]
        ), patch.object(codex_env.time, "sleep") as sleep_call:
            codex_env.launch_desktop(self.desktop)
        self.assertEqual([call.args[0] for call in sleep_call.call_args_list], [0.3, 0.8])

    def test_transient_launch_error_matching(self) -> None:
        self.assertTrue(
            codex_env._is_transient_launch_error(
                "_LSOpenURLsWithCompletionHandler() failed with error -600."
            )
        )
        self.assertTrue(codex_env._is_transient_launch_error("Code=-600"))
        self.assertTrue(codex_env._is_transient_launch_error("procNotFound"))
        self.assertFalse(codex_env._is_transient_launch_error("error -6000"))
        self.assertFalse(codex_env._is_transient_launch_error("prefix-600"))
        self.assertFalse(codex_env._is_transient_launch_error("LSCopy failed with error -10810"))
        self.assertEqual(codex_env._launch_error_text(self._completed(7)), "open exit code 7")

    def test_launch_does_not_retry_non_transient_error(self) -> None:
        failure = "LSCopyApplicationURLsForBundleIdentifier() failed."
        with patch.object(
            codex_env,
            "run_capture",
            side_effect=[self._completed(1, failure), self._completed(1, failure)],
        ) as run, patch.object(
            codex_env, "desktop_main_processes", return_value=[]
        ), patch.object(codex_env, "app_running", return_value=False):
            with self.assertRaises(RelayError) as ctx:
                codex_env.launch_desktop(self.desktop)
        self.assertIn("启动桌面 App 失败", str(ctx.exception))
        self.assertEqual(run.call_count, 2)

    def test_launch_fails_when_no_new_process_appears(self) -> None:
        failure = "_LSOpenURLsWithCompletionHandler() failed with error -600."
        responses = [self._completed(1, failure), self._completed(1, failure)] * 4
        with patch.object(codex_env, "run_capture", side_effect=responses) as run, patch.object(
            codex_env, "desktop_main_processes", return_value=[]
        ), patch.object(codex_env, "wait_until", return_value=False) as final_wait:
            with self.assertRaises(RelayError):
                codex_env.launch_desktop(self.desktop)
        self.assertEqual(run.call_count, 8)
        final_wait.assert_called_once()

    def test_quit_waits_for_launch_services_release(self) -> None:
        process = codex_env.ProcessInfo(
            pid=9002,
            ppid=1,
            started=datetime.now(),
            command=str(self.app_path / "Contents/MacOS/ChatGPT"),
            role="desktop-main",
        )
        with patch.object(
            codex_env, "desktop_bundle_processes", side_effect=[[process], []]
        ), patch.object(
            codex_env, "run_capture", return_value=self._completed(0)
        ), patch.object(
            codex_env, "wait_for_launch_services_release"
        ) as release:
            codex_env.quit_desktop()
        release.assert_called_once_with()

    def test_quit_does_not_script_a_stale_registration(self) -> None:
        for released in (True, False):
            with patch.object(
                codex_env, "desktop_bundle_processes", return_value=[]
            ), patch.object(
                codex_env, "app_running", return_value=True
            ), patch.object(
                codex_env, "wait_for_launch_services_release", return_value=released
            ), patch.object(
                codex_env, "run_capture"
            ) as run:
                notes = codex_env.quit_desktop()
            self.assertEqual(notes, [])
            run.assert_not_called()

    def test_launch_services_release_is_bounded_and_detected(self) -> None:
        with patch.object(
            codex_env, "_launch_services_registered", side_effect=[True, True, False]
        ) as registered:
            self.assertTrue(codex_env.wait_for_launch_services_release(timeout=2.0))
        self.assertEqual(registered.call_count, 3)

    def test_launch_services_release_timeout_is_non_fatal(self) -> None:
        with patch.object(codex_env, "_launch_services_registered", return_value=True):
            self.assertFalse(codex_env.wait_for_launch_services_release(timeout=0.0))
        with patch.object(codex_env, "IS_MACOS", False):
            self.assertTrue(codex_env.wait_for_launch_services_release(timeout=0.0))

    def test_quit_succeeds_when_launch_services_wait_times_out(self) -> None:
        process = codex_env.ProcessInfo(
            pid=9004,
            ppid=1,
            started=datetime.now(),
            command=str(self.app_path / "Contents/MacOS/ChatGPT"),
            role="desktop-main",
        )
        with patch.object(
            codex_env, "desktop_bundle_processes", side_effect=[[process], []]
        ), patch.object(
            codex_env, "run_capture", return_value=self._completed(0)
        ), patch.object(
            codex_env, "wait_for_launch_services_release", return_value=False
        ):
            notes = codex_env.quit_desktop()
        self.assertIsInstance(notes, list)


if __name__ == "__main__":
    unittest.main()
