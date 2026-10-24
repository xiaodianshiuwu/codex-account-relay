from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    from PyInstaller.__main__ import run as pyinstaller_run
except ImportError:
    print("PyInstaller 未安装：python -m pip install pyinstaller", file=sys.stderr)
    raise SystemExit(1)

resources = ROOT / "desktop" / "Resources"
if not resources.exists():
    resources = ROOT / "Resources"
data_arg = f"{resources}{os.pathsep}Resources"
package_resources = ROOT / "codex_relay" / "resources"
package_data_arg = f"{package_resources}{os.pathsep}codex_relay/resources"
args = [
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name",
    "CodexRelay",
    "--paths",
    str(ROOT),
    "--hidden-import",
    "codex_relay.cli",
    "--hidden-import",
    "codex_relay.portable_gui",
    "--add-data",
    data_arg,
    "--add-data",
    package_data_arg,
    str(ROOT / "tools" / "portable_entry.py"),
]
print("PyInstaller:", " ".join(args))
pyinstaller_run(args)
