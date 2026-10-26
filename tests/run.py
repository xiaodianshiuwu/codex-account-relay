from __future__ import annotations

import os
import sys
import unittest
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CODEX_RELAY_TEST_MODE", "1")

if __name__ == "__main__":
    isolated = tempfile.TemporaryDirectory(prefix="relay-suite-")
    os.environ['CODEX_RELAY_HOME'] = str(Path(isolated.name)/'relay')
    os.environ['CODEX_HOME'] = str(Path(isolated.name)/'codex')
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
