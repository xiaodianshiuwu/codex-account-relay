"""Shared translation loader for the CLI and the portable Tk client.

Two catalogs are layered:

* ``desktop/Resources/<lang>.lproj/Localizable.strings`` (the generated SwiftUI
  and Tk catalog, mirrored under ``codex_relay/resources``);
* ``i18n/cli.json``, a JSON catalog keyed by the Simplified Chinese source
  text, holding CLI/Tk sentences that do not belong in the app UI catalog.

Keys are always the Simplified Chinese source text, so ``zh-Hans`` can fall
back to the key itself if a translation is missing.  Lookup order is the CLI
catalog, then the generated UI catalog, then the key.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

LANGUAGES = ("zh-Hans", "zh-Hant", "en", "ja")
DEFAULT_LANGUAGE = "zh-Hans"

# Native language names are deliberately not translated: a language picker is
# most useful when every option is written in its own language.
LANGUAGE_NAMES = {
    "zh-Hans": "简体中文",
    "zh-Hant": "繁體中文",
    "en": "English",
    "ja": "日本語",
}

_STRING_RE = re.compile(r'"((?:\\.|[^"])*)"\s*=\s*"((?:\\.|[^"])*)"\s*;')
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ESCAPE_RE = re.compile(r"\\(n|t|r|\"|\\)")


def normalize_language(value: Optional[str]) -> Optional[str]:
    """Map a locale-ish string (``en-US``, ``zh_CN``, ``zh-TW``) to a catalog."""
    if not value:
        return None
    text = str(value).strip().lower().replace("_", "-")
    if not text:
        return None
    # POSIX locales carry an encoding/codeset suffix and LANGUAGE is a
    # colon-separated priority list: zh_CN.UTF-8, ja_JP.eucJP, zh_TW:en.
    text = text.split(":", 1)[0].split(".", 1)[0].split("@", 1)[0]
    # ``LANGUAGE`` may be a colon-separated preference list.
    if ":" in text:
        for part in text.split(":"):
            resolved = normalize_language(part)
            if resolved:
                return resolved
        return None
    if text in ("zh-hans", "zh-cn", "zh-sg", "zh"):
        return "zh-Hans"
    if text in ("zh-hant", "zh-tw", "zh-hk", "zh-mo"):
        return "zh-Hant"
    if text.startswith("zh-hans"):
        return "zh-Hans"
    if text.startswith("zh-hant"):
        return "zh-Hant"
    if text.startswith("ja"):
        return "ja"
    if text.startswith("en"):
        return "en"
    return None


def _settings_language() -> Optional[str]:
    """Read the portable UI preference shared with the GUI.

    Tests must never depend on a developer's real ``~/.codex-relay`` file, so
    the lookup is disabled under ``CODEX_RELAY_TEST_MODE``.
    """
    if os.environ.get("CODEX_RELAY_TEST_MODE") == "1":
        return None
    home = os.environ.get("CODEX_RELAY_HOME")
    relay_home = Path(home).expanduser() if home else Path.home() / ".codex-relay"
    path = relay_home / "portable-ui.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        return normalize_language(data.get("language"))
    return None


def _macos_preferred_language() -> Optional[str]:
    if sys.platform != "darwin":
        return None
    try:
        import subprocess

        result = subprocess.run(
            ["defaults", "read", "-g", "AppleLanguages"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'"([^"]+)"', result.stdout or "")
    return normalize_language(match.group(1)) if match else None


def _windows_ui_language() -> Optional[str]:
    if os.name != "nt":
        return None
    try:
        import ctypes

        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
        # LANGID: primary language in the low 10 bits.  0x04 = Chinese,
        # 0x11 = Japanese, 0x09 = English.
        primary = lang_id & 0x3FF
        if primary == 0x04:
            # 0x0404/0x0C04/0x1404 are Traditional Chinese locales.
            return "zh-Hant" if lang_id in (0x0404, 0x0C04, 0x1404) else "zh-Hans"
        if primary == 0x11:
            return "ja"
        if primary == 0x09:
            return "en"
    except (AttributeError, OSError):
        return None
    return None


def system_language() -> str:
    """Resolve the language from env vars, OS preference, then settings."""
    for name in ("CODEX_RELAY_LANG", "LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        resolved = normalize_language(os.environ.get(name))
        if resolved:
            return resolved
    for probe in (_macos_preferred_language, _windows_ui_language, _settings_language):
        resolved = probe()
        if resolved:
            return resolved
    try:
        import locale

        resolved = normalize_language(locale.getdefaultlocale()[0])
        if resolved:
            return resolved
    except (ImportError, ValueError):
        pass
    return DEFAULT_LANGUAGE


def resolve_language(explicit: Optional[str] = None) -> str:
    """Resolve an explicit ``--lang`` value first, then the system default."""
    return normalize_language(explicit) or system_language()


def language_from_argv(argv: Iterable[str]) -> str:
    args = list(argv)
    for index, arg in enumerate(args):
        if arg == "--lang" and index + 1 < len(args):
            resolved = normalize_language(args[index + 1])
            if resolved:
                return resolved
        if arg.startswith("--lang="):
            resolved = normalize_language(arg.split("=", 1)[1])
            if resolved:
                return resolved
    return system_language()


def _unescape(value: str) -> str:
    """Decode a ``.strings`` literal without the ordering bug of chained replaces."""
    return _ESCAPE_RE.sub(
        lambda match: {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}[match.group(1)],
        value,
    )


def _catalog_paths() -> List[Path]:
    here = Path(__file__).resolve().parent
    paths = []
    override = os.environ.get("CODEX_RELAY_I18N_CLI")
    if override:
        paths.append(Path(override).expanduser())
    paths.extend(
        [
            here.parent / "i18n" / "cli.json",
            here / "resources" / "cli.json",
        ]
    )
    return paths


def _ui_candidates(language: str) -> List[Path]:
    here = Path(__file__).resolve().parent
    return [
        here.parent / "desktop" / "Resources" / f"{language}.lproj" / "Localizable.strings",
        here / "resources" / f"{language}.lproj" / "Localizable.strings",
    ]


class Translator:
    """Loads the merged catalogs and provides safe interpolation helpers."""

    def __init__(self, language: Optional[str] = None, strict: bool = False) -> None:
        self.strict = strict
        self.language = normalize_language(language) or system_language()
        self.messages: Dict[str, str] = {}
        self._pattern_cache: List[Tuple[str, "re.Pattern[str]"]] = []
        self.load(self.language)

    # -- loading ---------------------------------------------------------
    def load(self, language: str) -> None:
        self.language = normalize_language(language) or DEFAULT_LANGUAGE
        messages: Dict[str, str] = {}
        for path in _ui_candidates(self.language):
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found = 0
            for match in _STRING_RE.finditer(text):
                messages[_unescape(match.group(1))] = _unescape(match.group(2))
                found += 1
            if found:
                break
        for path in _catalog_paths():
            if not path.exists():
                continue
            try:
                table = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(table, dict):
                continue
            for key, entry in table.items():
                if not isinstance(entry, dict):
                    continue
                value = entry.get(self.language)
                if isinstance(value, str) and value:
                    messages[key] = value
        self.messages = messages
        self._pattern_cache = self._build_patterns()

    def _build_patterns(self) -> List[Tuple[str, "re.Pattern[str]"]]:
        patterns: List[Tuple[str, "re.Pattern[str]"]] = []
        for key in self.messages:
            if "{" not in key:
                continue
            parts = _PLACEHOLDER_RE.split(key)
            # re.split with a capture group yields [literal, name, literal, ...]
            regex = "".join(
                re.escape(part) if index % 2 == 0 else "(.*?)"
                for index, part in enumerate(parts)
            )
            try:
                patterns.append((key, re.compile("^" + regex + "$", re.DOTALL)))
            except re.error:
                continue
        patterns.sort(key=lambda item: len(item[0]), reverse=True)
        return patterns

    # -- lookup ----------------------------------------------------------
    def has(self, key: str) -> bool:
        return key in self.messages

    def t(self, template_key: str, **kwargs: Any) -> str:
        value = self.messages.get(template_key)
        if value is None:
            if self.strict:
                raise KeyError(template_key)
            value = template_key
        if not kwargs:
            return value
        try:
            return value.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return value

    def __call__(self, key: str) -> str:
        return self.t(key)

    def tf(self, template_key: str, *args: Any, **kwargs: Any) -> str:
        """Format a catalog entry.

        Named placeholders (``{alias}``) are preferred; positional printf-style
        placeholders are accepted for compatibility with the generated
        ``.strings`` catalog (``%@``/``%d``/``%.0f``).
        """
        if kwargs:
            return self.t(template_key, **kwargs)
        template = self.t(template_key)
        template = template.replace("%@", "%s").replace("%lld", "%d")
        try:
            return template % args
        except (TypeError, ValueError):
            return template

    def format(self, template_key: str, *args: Any) -> str:
        return self.tf(template_key, *args)

    def external(self, message: Any) -> str:
        """Best-effort translation for messages raised by older modules.

        Those modules still raise Simplified Chinese text.  Exact catalog keys
        are translated directly; keys containing ``{name}`` placeholders are
        matched against the message and the captured values are substituted
        into the translated template.  Unmatched text is returned unchanged.
        """
        text = str(message or "")
        if not text or self.language == "zh-Hans":
            return text
        exact = self.messages.get(text)
        if exact is not None:
            return exact
        for key, pattern in self._pattern_cache:
            match = pattern.match(text)
            if not match:
                continue
            names = _PLACEHOLDER_RE.findall(key)
            if len(names) != len(match.groups()):
                continue
            values = {}
            for name, value in zip(names, match.groups()):
                # Captured words such as the codex_env action verb ("退出") are
                # catalog keys themselves; translate them when possible.
                values[name] = self.messages.get(value, value)
            return self.t(key, **values)
        return text

    # -- formatting helpers ---------------------------------------------
    def n(self, value: Any, compact: bool = False) -> str:
        if value is None or value == "":
            return "-"
        try:
            number = int(value)
        except (TypeError, ValueError):
            return str(value)
        if compact:
            return _compact_number(number, self.language)
        return f"{number:,}"

    def pct(self, value: Any, digits: int = 0) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "-"
        return f"{number:.{digits}f}%"

    def dt(self, value: Any, seconds: bool = False) -> str:
        if value is None or value == "":
            return "-"
        parsed: Optional[datetime] = None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)):
            try:
                parsed = datetime.fromtimestamp(value)
            except (OverflowError, OSError, ValueError):
                return str(value)
        else:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return str(value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        fmt = {
            "zh-Hans": "%Y-%m-%d %H:%M",
            "zh-Hant": "%Y-%m-%d %H:%M",
            "en": "%b %d, %Y %H:%M",
            "ja": "%Y/%m/%d %H:%M",
        }.get(self.language, "%Y-%m-%d %H:%M")
        if seconds:
            fmt += ":%S"
        return parsed.strftime(fmt)

    def duration(self, minutes: Any) -> str:
        try:
            total = float(minutes)
        except (TypeError, ValueError):
            return "-"
        if total >= 24 * 60:
            return self.t("{value} 天", value=_trim_number(total / (24 * 60)))
        if total >= 60:
            return self.t("{value} 小时", value=_trim_number(total / 60))
        return self.t("{value} 分钟", value=_trim_number(total))

    def role(self, role: Any) -> str:
        mapping = {
            "desktop-main": "桌面主进程",
            "desktop-app-server": "桌面 app-server",
            "desktop-exec-server": "桌面 exec-server",
            "desktop-codex": "桌面 Codex",
            "cli": "CLI",
            "cli-app-server": "CLI app-server",
            "daemon": "共享 daemon",
            "helper": "辅助进程",
            "other": "其他进程",
        }
        key = mapping.get(str(role))
        return self.t(key) if key else str(role or "-")

    def status(self, status: Any) -> str:
        mapping = {
            "ok": "正常",
            "error": "错误",
            "needs_relogin": "需重新登录",
            "exhausted": "额度已耗尽",
        }
        key = mapping.get(str(status or "ok"))
        return self.t(key) if key else str(status or "-")

    def mode(self, mode: Any) -> str:
        mapping = {"desktop": "桌面", "cli": "CLI", "none": "仅文件"}
        key = mapping.get(str(mode))
        return self.t(key) if key else str(mode or "-")

    def level(self, level: Any) -> str:
        mapping = {"pass": "通过", "warn": "警告", "fail": "失败"}
        key = mapping.get(str(level))
        return self.t(key) if key else str(level or "-")


def _trim_number(value: float) -> str:
    text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_number(value: Any, compact: bool = False, language: str = DEFAULT_LANGUAGE) -> str:
    """Format an integer with locale-aware grouping and compact units."""
    if value is None or value == "":
        return "-"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    if compact:
        return _compact_number(number, normalize_language(language) or DEFAULT_LANGUAGE)
    return f"{number:,}"


def _compact_number(value: int, language: str) -> str:
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    if language == "zh-Hant":
        units = ((100_000_000, "億"), (10_000, "萬"))
    elif language == "ja":
        units = ((100_000_000, "億"), (10_000, "万"))
    elif language == "zh-Hans":
        units = ((100_000_000, "亿"), (10_000, "万"))
    else:
        units = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))
    for size, suffix in units:
        if absolute >= size:
            scaled = absolute / size
            text = f"{scaled:.0f}" if scaled >= 100 else f"{scaled:.1f}" if scaled >= 10 else f"{scaled:.2f}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return sign + text + suffix
    return f"{value:,}"


def display_width(text: str) -> int:
    """Terminal display width (East Asian wide/fullwidth characters count 2)."""
    import unicodedata

    width = 0
    for char in str(text):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def pad_display(text: str, width: int) -> str:
    text = str(text)
    return text + " " * max(0, width - display_width(text))


def truncate_display(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if display_width(text) <= limit:
        return text
    budget = max(0, limit - 1)
    out: List[str] = []
    used = 0
    for char in text:
        char_width = display_width(char)
        if used + char_width > budget:
            break
        out.append(char)
        used += char_width
    return "".join(out) + "…"


def get_translator(language: Optional[str] = None) -> Translator:
    return Translator(language)


def main(argv: Optional[List[str]] = None) -> int:
    """Tiny diagnostic entry point used by tests and ``--self-test``."""
    args = list(sys.argv[1:] if argv is None else argv)
    language = language_from_argv(args)
    translator = Translator(language)
    if "--json" in args:
        print(json.dumps({"language": translator.language, "keys": len(translator.messages)}, ensure_ascii=False))
    else:
        print(f"{translator.language}: {translator('设置')} ({len(translator.messages)} keys)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
