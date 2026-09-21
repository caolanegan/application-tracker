"""Job application tracker.

Exit codes (SPEC §7), defined once here — not in cli.py — so that ingest and
query modules can import them without creating a circular import with the CLI
module that in turn imports them.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_VALIDATION = 2
EXIT_NOT_FOUND = 3
