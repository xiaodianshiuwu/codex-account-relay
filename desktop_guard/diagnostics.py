"""Local diagnostic codes and stack locations, never raw payloads or secrets."""
from pathlib import Path
import time
import traceback


def diagnostic(exc, stage):
    return {'type': type(exc).__name__, 'stage': stage, 'at': time.time(),
            'frames': [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(exc.__traceback__)[-8:]]}
