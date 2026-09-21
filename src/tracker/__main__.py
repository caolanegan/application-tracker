"""Allows `python -m tracker <command>` (SPEC §7)."""

from __future__ import annotations

import sys

from tracker.cli import main

if __name__ == "__main__":
    sys.exit(main())
