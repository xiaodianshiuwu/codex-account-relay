"""Build the self-contained Codex Relay CLI used inside the macOS SwiftUI app.

The packaged app must not depend on a separately installed Python package or
`codex-relay` executable. A one-folder runtime keeps native libraries at
stable paths instead of extracting and validating them for every invocation.
`desktop/build.sh` embeds both the executable and relay-runtime in the app.
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
# Migrate only the generated legacy one-file output, never a source directory.
legacy = ROOT / "dist-cli" / "codex-relay"
if legacy.is_file():
    legacy.unlink()
args = [
    "--noconfirm",
    "--clean",
    "--onedir",
    "--contents-directory",
    "relay-runtime",
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
