"""Make both test import styles work regardless of how pytest is invoked.

The suite mixes `import app.*` (relative to `python_backend/`) with
`import python_backend.app.*` (relative to the project root), so both
directories need to be importable.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

for path in (ROOT, ROOT / "python_backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
