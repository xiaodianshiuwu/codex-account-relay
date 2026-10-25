"""Build the self-contained Codex Relay CLI used inside the macOS SwiftUI app.

The packaged app must not depend on a separately installed Python package or
`codex-relay` executable. This one-file binary is copied into
`Codex Relay.app/Contents/Resources/codex-relay` by desktop/build.sh.
"""

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

resources = ROOT / "codex_relay" / "resources"
args = [
    "--noconfirm",
    "--clean",
    "--onefile",
    "--console",
    "--name",
    "codex-relay",
    "--paths",
    str(ROOT),
    "--distpath",
    str(ROOT / "dist-cli"),
    "--workpath",
    str(ROOT / "build-cli"),
    "--specpath",
    str(ROOT / "build-cli"),
    "--hidden-import",
    "codex_relay.cli",
    "--add-data",
    f"{resources}{os.pathsep}codex_relay/resources",
    str(ROOT / "tools" / "cli_entry.py"),
]
print("PyInstaller CLI:", " ".join(args))
pyinstaller_run(args)
