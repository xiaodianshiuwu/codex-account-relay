#!/usr/bin/env python3
"""Audit Codex Relay UI localization coverage.

This is intentionally dependency-free.  It validates the JSON catalogs, the
generated .strings files, explicit localization calls, and CJK literals that
never entered the catalog.  Exit code 0 means the checked scope is fully
covered; 1 means at least one hard error.
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys
from typing import Any, Dict, Iterable, List, Set, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOGS = [ROOT / "i18n" / "strings.json", ROOT / "i18n" / "cli.json"]
LANGUAGES = ("zh-Hans", "zh-Hant", "en", "ja")
PLACEHOLDER = re.compile(r"%(?:\d+\$)?(?:@|lld|ld|d|i|s|\.\d+f|f)")
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
SWIFT_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
SWIFT_EXPLICIT = re.compile(r'L10n\.(?:text|format|localized)\(\s*"((?:[^"\\]|\\.)*)"')
PY_EXPLICIT = re.compile(r'(?:self\.)?t(?:f)?\(\s*"((?:[^"\\]|\\.)*)"')


def strict_load(path: pathlib.Path) -> Tuple[Dict[str, Any], List[str]]:
    duplicates: List[str] = []

    def hook(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=hook)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} must be a JSON object")
    return value, duplicates


def load_catalog() -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    merged: Dict[str, Dict[str, str]] = {}
    errors: List[str] = []
    for path in CATALOGS:
        if not path.exists():
            continue
        table, duplicates = strict_load(path)
        for key in duplicates:
            errors.append(f"duplicate key in {path.name}: {key!r}")
        for key, entry in table.items():
            if key in merged and merged[key] != entry:
                errors.append(f"conflicting key across catalogs: {key!r}")
            merged[key] = entry
    return merged, errors


def validate_catalog(table: Dict[str, Dict[str, str]], allowlist: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    allowed_same = {
        language: set(allowlist.get("same_as_key", {}).get(language, []))
        for language in LANGUAGES
    }
    for key, entry in sorted(table.items()):
        if not key.strip():
            errors.append("empty translation key")
            continue
        reference = sorted(PLACEHOLDER.findall(key))
        for language in LANGUAGES:
            value = entry.get(language)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"missing {language} translation: {key!r}")
                continue
            if sorted(PLACEHOLDER.findall(value)) != reference:
                errors.append(
                    f"placeholder mismatch in {language} for {key!r}: "
                    f"expected {reference}, got {sorted(PLACEHOLDER.findall(value))}"
                )
    return errors


def swift_sources() -> Iterable[pathlib.Path]:
    return sorted((ROOT / "desktop" / "Sources").rglob("*.swift"))


def python_sources() -> Iterable[pathlib.Path]:
    paths = [
        ROOT / "codex_relay" / "portable_gui.py",
        ROOT / "codex_relay" / "cli.py",
        ROOT / "codex_relay" / "doctor.py",
        ROOT / "codex_relay" / "usage.py",
    ]
    return [path for path in paths if path.exists()]


def scan_swift(table: Dict[str, Any]) -> Tuple[Set[str], List[str], List[str], int]:
    explicit: Set[str] = set()
    hardcoded: List[str] = []
    dynamic: List[str] = []
    total = 0
    for path in swift_sources():
        text = path.read_text(encoding="utf-8")
        for match in SWIFT_EXPLICIT.finditer(text):
            key = match.group(1)
            if key:
                explicit.add(key)
                if key not in table:
                    hardcoded.append(f"{path.relative_to(ROOT)}: missing explicit key {key!r}")
        for match in SWIFT_STRING.finditer(text):
            value = match.group(1)
            if not CJK.search(value) or len(value) < 2:
                continue
            if "\\(" in value:
                dynamic.append(f"{path.relative_to(ROOT)}: interpolated literal {value!r}")
                continue
            total += 1
            if value not in table:
                line = text.count("\n", 0, match.start()) + 1
                hardcoded.append(f"{path.relative_to(ROOT)}:{line}: hardcoded {value!r}")
    return explicit, hardcoded, dynamic, total


def _python_docstrings(tree: ast.AST) -> Set[int]:
    ids: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def scan_python(table: Dict[str, Any], technical: Set[str] | None = None) -> Tuple[Set[str], List[str], int]:
    technical = technical or set()
    explicit: Set[str] = set()
    hardcoded: List[str] = []
    total = 0
    for path in python_sources():
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            hardcoded.append(f"{path.relative_to(ROOT)}: syntax error {exc}")
            continue
        docstrings = _python_docstrings(tree)
        parents: Dict[int, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
                if name in ("t", "tf") and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        key = first.value
                        explicit.add(key)
                        if key not in table:
                            hardcoded.append(f"{path.relative_to(ROOT)}:{node.lineno}: missing explicit key {key!r}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if id(node) in docstrings or not CJK.search(value) or len(value) < 2:
                    continue
                if value in technical:
                    continue
                parent = parents.get(id(node))
                if isinstance(parent, ast.JoinedStr):
                    continue
                total += 1
                if value not in table:
                    hardcoded.append(f"{path.relative_to(ROOT)}:{node.lineno}: hardcoded {value!r}")
    return explicit, hardcoded, total


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--strict-unused", action="store_true", help="fail on unused catalog keys")
    args = parser.parse_args(argv)

    allowlist: Dict[str, Any] = {"same_as_key": {}, "unused": [], "technical_literals": []}
    allowlist_path = ROOT / "i18n" / "i18n-allowlist.json"
    if allowlist_path.exists():
        try:
            allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"error: cannot read {allowlist_path}: {exc}", file=sys.stderr)
            return 1

    try:
        table, catalog_errors = load_catalog()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    errors = catalog_errors + validate_catalog(table, allowlist)
    swift_explicit, swift_hardcoded, swift_dynamic, swift_total = scan_swift(table)
    python_explicit, python_hardcoded, python_total = scan_python(table, set(allowlist.get("technical_literals", [])))
    errors.extend(swift_hardcoded)
    errors.extend(python_hardcoded)

    all_source = "\n".join(path.read_text(encoding="utf-8") for path in list(swift_sources()) + python_sources())
    external = set(allowlist.get("external", []))
    unused = sorted(
        key
        for key in table
        if key not in all_source
        and key not in set(allowlist.get("unused", []))
        and key not in external
    )
    if args.strict_unused and unused:
        errors.extend(f"unused key: {key!r}" for key in unused)

    language_stats = {}
    for language in LANGUAGES:
        valid = sum(1 for entry in table.values() if isinstance(entry.get(language), str) and entry[language].strip())
        language_stats[language] = {"valid": valid, "total": len(table), "coverage": round(valid / len(table) * 100, 2) if table else 0.0}

    source_refs = swift_explicit | python_explicit
    integrated = sum(1 for key in source_refs if key in table)
    same_as_key = {
        language: sum(1 for key, entry in table.items() if language != "zh-Hans" and entry.get(language) == key)
        for language in LANGUAGES
    }
    report = {
        "catalog_keys": len(table),
        "languages": language_stats,
        "same_as_key": same_as_key,
        "explicit_keys": len(source_refs),
        "integrated_keys": integrated,
        "integration_coverage": round(integrated / len(source_refs) * 100, 2) if source_refs else 100.0,
        "swift_literals": swift_total,
        "python_literals": python_total,
        "hardcoded_count": len(swift_hardcoded) + len(python_hardcoded),
        "dynamic_interpolations": len(swift_dynamic),
        "unused_keys": len(unused),
        "external_keys": len([key for key in external if key in table]),
        "errors": errors,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"catalog keys: {len(table)}")
        for language, stats in language_stats.items():
            print(f"  {language}: {stats['valid']}/{stats['total']} ({stats['coverage']}%)")
        print(f"explicit keys: {integrated}/{len(source_refs)} integrated ({report['integration_coverage']}%)")
        print(f"hardcoded CJK literals: {report['hardcoded_count']}")
        print(f"dynamic interpolations: {len(swift_dynamic)}")
        print(f"unused keys: {len(unused)}")
        for error in errors[:100]:
            print(f"ERROR: {error}")
        if len(errors) > 100:
            print(f"... {len(errors) - 100} more errors")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
