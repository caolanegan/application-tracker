"""Puts src/ on sys.path so `import tracker` works under pytest without an
editable install (the venv only installs pytest and per-step optional libs)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
