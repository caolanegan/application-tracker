"""Connection handling and schema migrations (SPEC §5)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# src/tracker/db.py -> src/tracker -> src -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "tracker.db"


def utcnow() -> str:
    """Return the current time as the SPEC §5 UTC ISO-8601 timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection with the pragmas SPEC §5 requires, applied every time.

    PRAGMA foreign_keys does not persist in the database file — it is
    per-connection and defaults to OFF — so every connection must set it here
    or FK cascades silently do nothing.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migrations() -> list[tuple[int, str]]:
    """Numbered, ordered migrations. schema.sql is migration 1.

    SQLite cannot meaningfully ALTER a view or a CHECK constraint, so a later
    schema change is a new (version, sql) entry appended here, never an edit
    to an already-shipped migration.
    """
    schema_sql = (Path(__file__).resolve().parent / "schema.sql").read_text()
    return [(1, schema_sql)]


def migrate(conn: sqlite3.Connection) -> None:
    """Apply any unapplied migration, in order. Safe to run repeatedly."""
    has_migrations_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    applied: set[int] = set()
    if has_migrations_table:
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    for version, sql in _migrations():
        if version in applied:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, utcnow()),
        )
    conn.commit()
