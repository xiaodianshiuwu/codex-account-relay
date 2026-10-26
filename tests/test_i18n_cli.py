"""Localization tests for the CLI, the shared catalog and the Tk loader.

These tests are intentionally hermetic: every CLI invocation points
``CODEX_HOME``/``CODEX_RELAY_HOME`` at a temporary directory, the test mode is
on, and the doctor's network probes are stubbed.  No real account, process or
network operation is involved.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("CODEX_RELAY_TEST_MODE", "1")

from codex_relay import i18n
from codex_relay.cli import main, _print_table
from codex_relay.doctor import check_title, run_doctor
from codex_relay.i18n import Translator, display_width, format_number, normalize_language, pad_display, resolve_language
from codex_relay.portable_gui import Localizer, _format_number, _format_time
from codex_relay.store import Store, default_paths


ROOT = Path(__file__).resolve().parent.parent
LANGUAGES = ("zh-Hans", "zh-Hant", "en", "ja")
_PRINTF = re.compile(r"%(?:\d+\$)?(?:@|lld|ld|d|i|s|\.\d+f|f)")
_NAMED = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_catalogs():
    ui = json.loads((ROOT / "i18n" / "strings.json").read_text(encoding="utf-8"))
    cli = json.loads((ROOT / "i18n" / "cli.json").read_text(encoding="utf-8"))
    merged = dict(ui)
    merged.update(cli)
    return ui, cli, merged


class CatalogTests(unittest.TestCase):
    def test_four_language_catalog_is_complete(self) -> None:
        _, _, merged = load_catalogs()
        self.assertGreater(len(merged), 400)
        for key, entry in merged.items():
            self.assertIsInstance(entry, dict, key)
            for language in LANGUAGES:
                value = entry.get(language)
                self.assertIsInstance(value, str, f"{key!r} missing {language}")
                self.assertTrue(value.strip(), f"{key!r} empty {language}")

    def test_cli_catalog_does_not_shadow_the_ui_catalog(self) -> None:
        """build_strings.py merges both catalogs; overlapping keys must not differ."""
        ui, cli, _ = load_catalogs()
        duplicated = sorted(set(ui) & set(cli))
        self.assertEqual(duplicated, [], f"cli.json must only contain CLI-specific keys: {duplicated}")

    def test_placeholders_match_across_languages(self) -> None:
        _, _, merged = load_catalogs()
        for key, entry in merged.items():
            named = sorted(_NAMED.findall(key))
            printf = sorted(_PRINTF.findall(key))
            for language in LANGUAGES:
                value = entry[language]
                self.assertEqual(sorted(_NAMED.findall(value)), named, f"named placeholder mismatch: {key!r} {language}")
                self.assertEqual(sorted(_PRINTF.findall(value)), printf, f"printf placeholder mismatch: {key!r} {language}")

    def test_legacy_external_messages_are_translated(self) -> None:
        """CLI modules still raise Chinese legacy messages; the CLI boundary
        translates them with Translator.external(). Keep representative
        switch/rollback templates from regressing to raw Chinese."""
        samples = [
            "切换完成",
            "桌面 App 正在运行；--force 将只替换文件，运行中的 App 不会被热切换",
            "已恢复切换前的 auth.json",
            "切换前没有 auth.json，已移除新写入的文件",
            "尚未替换 auth.json；已重新启动 ChatGPT App",
            "切换失败：boom",
            "回滚：one；two",
        ]
        tr = Translator("en")
        for sample in samples:
            translated = tr.external(sample)
            self.assertNotEqual(translated, sample, sample)
            self.assertNotRegex(translated, r"[\u4e00-\u9fff]", f"Chinese leaked: {sample!r} -> {translated!r}")

    def test_no_simplified_chinese_in_english_or_japanese(self) -> None:
        _, _, merged = load_catalogs()
        simplified_only = set("个时发这为后关开说请读写设计账额进据线网选单启应处确认证验问题页码临备战优势态样际实现环观览视图标准语户权资载连检测统显录复杂简终缓储还转换迁删询筛择项类级别")
        native_names = set(i18n.LANGUAGE_NAMES.values())
        for key, entry in merged.items():
            if key in native_names:
                continue  # language pickers intentionally use native names
            self.assertNotRegex(entry["en"], r"[\u4e00-\u9fff]", f"Chinese leaked into English: {key!r}")
            self.assertFalse(
                [char for char in entry["ja"] if char in simplified_only],
                f"Simplified Chinese character in Japanese: {key!r} -> {entry['ja']}",
            )

    def test_all_code_keys_exist(self) -> None:
        import ast

        _, _, merged = load_catalogs()
        used = set()
        for name in ("cli.py", "doctor.py", "usage.py", "portable_gui.py"):
            tree = ast.parse((ROOT / "codex_relay" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                attr = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                if attr not in ("t", "tf"):
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    used.add(first.value)
        missing = sorted(key for key in used if key not in merged)
        self.assertEqual(missing, [], f"missing catalog keys: {missing}")


class LanguageResolutionTests(unittest.TestCase):
    def test_locale_normalization(self) -> None:
        cases = {
            "zh-Hans": "zh-Hans",
            "zh_CN": "zh-Hans",
            "zh-CN": "zh-Hans",
            "zh": "zh-Hans",
            "zh-Hant": "zh-Hant",
            "zh_TW": "zh-Hant",
            "zh-HK": "zh-Hant",
            "en-US": "en",
            "en_US.UTF-8": "en",
            "ja_JP": "ja",
            "ja_JP.UTF-8": "ja",
            "zh_CN.UTF-8": "zh-Hans",
            "zh_TW.UTF-8": "zh-Hant",
            "fr-FR": None,
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_language(raw), expected, raw)

    def test_cli_flag_beats_environment(self) -> None:
        with patch.dict(os.environ, {"CODEX_RELAY_LANG": "en", "LC_ALL": "ja_JP.UTF-8"}):
            self.assertEqual(resolve_language("ja"), "ja")
            self.assertEqual(resolve_language(None), "en")

    def test_translator_resolves_new_cli_keys(self) -> None:
        tr = Translator("en")
        self.assertIn("Loading sessions", tr.t("读取会话…"))
        self.assertIn("Session not found", tr.t("找不到会话：{id}", id="abc"))
        self.assertEqual(tr.external("数据库不存在：/tmp/x"), "Database not found: /tmp/x")
        # `{key}` is both a placeholder name and a method argument; regression guard.
        self.assertEqual(tr.external("认证文件缺少 tokens"), "The auth file is missing tokens")


class FormattingTests(unittest.TestCase):
    def test_compact_units_follow_language(self) -> None:
        self.assertEqual(format_number(1_234_567, compact=True, language="en"), "1.23M")
        self.assertEqual(format_number(1_234_567, compact=True, language="zh-Hans"), "123万")
        self.assertEqual(format_number(1_234_567, compact=True, language="zh-Hant"), "123萬")
        self.assertEqual(format_number(100_000_000, compact=True, language="ja"), "1億")

    def test_legacy_portable_number_signature_is_stable(self) -> None:
        self.assertEqual(_format_number(1_234_567, "compact"), "1.23M")
        self.assertEqual(_format_number(1_234_567, "full"), "1,234,567")

    def test_dates_use_language_pattern(self) -> None:
        self.assertRegex(_format_time("2026-10-24T12:00:00Z", Localizer("zh-Hans")), r"^2026-10-\d{2} \d{2}:\d{2}$")
        self.assertRegex(_format_time("2026-10-24T12:00:00Z", Localizer("ja")), r"^2026/10/\d{2} \d{2}:\d{2}$")
        self.assertRegex(_format_time("2026-10-24T12:00:00Z", Localizer("en")), r"^[A-Z][a-z]{2} \d{2}, 2026 \d{2}:\d{2}$")

    def test_display_width_and_table_alignment(self) -> None:
        self.assertEqual(display_width("账号"), 4)
        self.assertEqual(display_width("Email"), 5)
        self.assertEqual(pad_display("账号", 6), "账号  ")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_table(["账号", "Email"], [["测试", "a@example.com"]])
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0].index("Email"), lines[2].index("a@example.com"))
        # The separator must cover the display width of each header cell.
        self.assertEqual(lines[1].split("  ")[0], "-" * 4)


class CliLocalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="relay-i18n-"))
        self.codex_home = self.root / "codex"
        self.relay_home = self.root / "relay"
        self.codex_home.mkdir(parents=True, exist_ok=True)
        self.env = patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.codex_home),
                "CODEX_RELAY_HOME": str(self.relay_home),
                "CODEX_RELAY_TEST_MODE": "1",
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()

    def run_cli(self, args):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(args)
            except SystemExit as exc:  # argparse --help / usage errors
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_help_is_localized(self) -> None:
        markers = {
            "en": ("Usage:", "Options:", "Show this help and exit"),
            "ja": ("使用法:", "オプション:", "ヘルプを表示して終了"),
            "zh-Hant": ("用法:", "選項:", "顯示說明並結束"),
            "zh-Hans": ("用法:", "选项:", "显示帮助并退出"),
        }
        for language, expected in markers.items():
            code, out, err = self.run_cli(["--lang", language, "--help"])
            self.assertEqual(code, 0, err)
            for marker in expected:
                self.assertIn(marker, out, f"{language}: {marker}")
            self.assertNotIn("positional arguments:", out)
            self.assertNotIn("show this help message and exit", out)

    def test_lang_may_follow_the_subcommand(self) -> None:
        code, out, err = self.run_cli(["status", "--lang", "en", "--json"])
        self.assertEqual(code, 0, err)
        self.assertIn("checked_at", json.loads(out))

    def test_environment_language_is_used_without_flag(self) -> None:
        with patch.dict(os.environ, {"CODEX_RELAY_LANG": "en"}):
            code, out, err = self.run_cli(["status"])
        self.assertEqual(code, 0, err)
        self.assertIn("Not signed in to Codex.", out)

    def test_missing_argument_error_is_localized(self) -> None:
        code, out, err = self.run_cli(["--lang", "en", "add"])
        self.assertEqual(code, 2)
        self.assertIn("Error: Missing required argument(s): alias", err)
        code, out, err = self.run_cli(["--lang", "ja", "add"])
        self.assertEqual(code, 2)
        self.assertIn("エラー: 必須引数がありません：alias", err)

    def test_read_only_json_commands_run_in_every_language(self) -> None:
        for language in LANGUAGES:
            for args in (["status", "--json"], ["list", "--json"], ["logs", "--json"], ["version", "--json"]):
                code, out, err = self.run_cli(["--lang", language, *args])
                self.assertEqual(code, 0, f"{language} {args}: {err}")
                json.loads(out)

    def test_json_structure_is_language_independent(self) -> None:
        def structure(value):
            if isinstance(value, dict):
                return {key: structure(item) for key, item in value.items()}
            if isinstance(value, list):
                return [structure(item) for item in value]
            return type(value).__name__

        structures = {}
        # The process table can change between language calls. Isolate the
        # external source so this checks localization rather than host activity.
        with patch("codex_relay.cli.codex_env.codex_processes", return_value=[]), patch(
            "codex_relay.cli.codex_env.app_running", return_value=False
        ), patch("codex_relay.cli.codex_env.daemon_running", return_value=False):
            for language in LANGUAGES:
                code, out, err = self.run_cli(["--lang", language, "status", "--json"])
                self.assertEqual(code, 0, err)
                structures[language] = structure(json.loads(out))
        self.assertEqual(len({json.dumps(value, sort_keys=True) for value in structures.values()}), 1)

    def test_doctor_json_is_localized_with_stable_ids(self) -> None:
        with patch("codex_relay.doctor._network_check", return_value={"url": "test", "ok": True, "status": 200}):
            code, en_out, err = self.run_cli(["--lang", "en", "doctor", "--json"])
            self.assertIn(code, (0, 1), err)
            code2, hans_out, err2 = self.run_cli(["--lang", "zh-Hans", "doctor", "--json"])
            self.assertIn(code2, (0, 1), err2)
        english = json.loads(en_out)
        chinese = json.loads(hans_out)
        self.assertEqual(
            [check["check"] for check in english["checks"]],
            [check["check"] for check in chinese["checks"]],
        )
        self.assertEqual(
            [check["level"] for check in english["checks"]],
            [check["level"] for check in chinese["checks"]],
        )
        for check in english["checks"]:
            self.assertEqual(set(check), {"check", "level", "message"})
        messages = {check["check"]: check["message"] for check in english["checks"]}
        self.assertNotIn("不存在", messages.get("state_db", ""))
        self.assertEqual(check_title(Translator("en"), "platform"), "Platform")
        self.assertEqual(check_title(Translator("ja"), "codex_cli"), "Codex CLI")
        # The GUI derives the localized check name from the stable id.
        self.assertNotIn("运行平台", en_out)

    def test_doctor_text_is_localized(self) -> None:
        with patch("codex_relay.doctor._network_check", return_value={"url": "test", "ok": True, "status": 200}):
            code, out, err = self.run_cli(["--lang", "ja", "doctor"])
        self.assertIn(code, (0, 1), err)
        self.assertIn("実行環境:", out)
        self.assertIn("結果：", out)
        self.assertNotIn("运行平台", out)


class TkLocalizerTests(unittest.TestCase):
    def test_localizer_resolves_new_tk_and_cli_keys(self) -> None:
        expected = {
            "zh-Hans": "读取会话…",
            "zh-Hant": "讀取工作階段…",
            "en": "Loading sessions…",
            "ja": "セッションを読み込み中…",
        }
        for language, value in expected.items():
            localizer = Localizer(language)
            self.assertEqual(localizer("读取会话…"), value)
            self.assertTrue(localizer("深色"))
            self.assertTrue(localizer("简写 K/M/B"))

    def test_localizer_normalizes_locale_codes(self) -> None:
        self.assertEqual(Localizer("en-US").language, "en")
        self.assertEqual(Localizer("zh_CN").language, "zh-Hans")

    def test_format_time_is_shared(self) -> None:
        self.assertRegex(_format_time("2026-10-24T12:00:00Z", Localizer("en")), r"^[A-Z][a-z]{2} \d{2}, 2026 \d{2}:\d{2}$")


class TkWidgetTests(unittest.TestCase):
    """Real Tk widget checks; skipped automatically on headless machines."""

    def setUp(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("Tk runtime is not installed (historical UI)")

        self.tk = tk
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display is not available")
        self.root.withdraw()
        self.root_dir = Path(tempfile.mkdtemp(prefix="relay-tk-i18n-"))
        (self.root_dir / "codex").mkdir()
        (self.root_dir / "relay").mkdir()
        (self.root_dir / "relay" / "portable-ui.json").write_text(
            json.dumps({"theme": "dark", "language": "en", "quota_display": "remaining", "number_format": "full"}),
            encoding="utf-8",
        )
        self.env = patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.root_dir / "codex"),
                "CODEX_RELAY_HOME": str(self.root_dir / "relay"),
                "CODEX_RELAY_TEST_MODE": "1",
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        try:
            self.root.destroy()
        except Exception:
            pass

    @staticmethod
    def _widgets(root, cls):
        found = []

        def walk(widget):
            if isinstance(widget, cls):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(root)
        return found

    def test_settings_labels_follow_language_and_rebuild(self) -> None:
        from unittest.mock import patch as mock_patch

        from codex_relay.portable_gui import PortableApp

        with mock_patch.object(PortableApp, "refresh_overview", lambda self: None):
            app = PortableApp(self.root)
        settings_labels = [
            widget.cget("text") for widget in self._widgets(self.root, self.tk.Label)
            if widget.cget("text") in {
                "剩余额度", "已使用", "完整数值", "简写 K/M/B",
                "跟随系统", "深色", "浅色", "简体中文", "繁體中文", "English", "日本語",
                "Remaining quota", "Used", "Full values", "Compact (K/M/B)",
                "System", "Dark", "Light",
            }
        ]
        self.assertEqual(len(settings_labels), 11)
        self.assertIn("Remaining quota", [widget.cget("text") for widget in self._widgets(self.root, self.tk.Label)])
        self.assertIn("Compact (K/M/B)", [widget.cget("text") for widget in self._widgets(self.root, self.tk.Label)])
        self.assertIn("Overview", [button.cget("text").strip() for button in app.nav_buttons.values()])

        old_pages = list(app.pages.values())
        generation = app._generation
        app.language_var.set("ja")
        app.apply_settings()

        self.assertEqual(app.localizer.language, "ja")
        self.assertEqual(app._generation, generation + 1)
        self.assertTrue(all(not page.winfo_exists() for page in old_pages))
        self.assertIn("セッション", [button.cget("text").strip() for button in app.nav_buttons.values()])
        texts = [widget.cget("text") for widget in self._widgets(self.root, self.tk.Label)]
        self.assertIn("残りの利用枠", texts)
        self.assertIn("短縮（K/M/B）", texts)

        # Diagnostics JSON has no title field; the page maps the stable id.
        app.show_doctor({"checks": [{"check": "platform", "level": "pass", "message": "darwin"}], "failures": 0, "warnings": 0})
        doctor_text = app.doctor_text.get("1.0", "end")
        self.assertIn("[合格]", doctor_text)
        self.assertIn("実行環境: darwin", doctor_text)


if __name__ == "__main__":
    unittest.main()
