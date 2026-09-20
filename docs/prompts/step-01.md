Implement **Step 1** of the job application tracker: database schema, migrations and
the CLI skeleton.

## Orientation

Working directory: `/Users/caolan/Developer/claude/application-tracker` (a git repo
with two commits, no code yet — you are writing the first code).

Read these two files in full before writing anything:

- `docs/SPEC.md` — the source of truth. Pay closest attention to **§5 (data model,
  all subsections 5.1–5.13)**, **§7 (CLI surface)** and **§8 (decision record)**.
- `docs/PLAN.md` — read the "Ground rules for every step" section, then **Step 1**.
  Skim Steps 2–13 so you know what you are unblocking; do not implement any of them.

Step 1 in `PLAN.md` is your task definition: it lists the files you own, what to
build, and the acceptance criteria. This prompt adds context that is not in those
documents.

## Environment

- macOS, `python3` is 3.13.5, sqlite 3.50.1. Both fine as-is.
- **`pytest` is not installed.** Create a `.venv` in the repo root and install pytest
  into it (`python3 -m venv .venv && .venv/bin/pip install pytest`). `.venv/` is
  already gitignored. Run tests as `.venv/bin/python -m pytest tests/ -q`.
- Critically: the venv is for **tests only**. The runtime code must run under bare
  system `python3` with zero third-party packages installed — that is SPEC D3 and D4,
  and it is not negotiable. `./tracker init` must work without the venv.

## What matters most in this step

Everything downstream reads what you build, so a subtle error here propagates. In
rough order of how much damage getting it wrong would do:

**1. `v_dashboard` is the highest-risk artifact in the step.** Every other step —
the API, both exporters, the UI — reads it, and SPEC §5.9 forbids anyone defining a
second version of it. It must LEFT JOIN throughout so that a job with no Glassdoor
rating, no salary row, no CV variant and no application row **still returns a row**,
with NULLs in those columns. The classic failure is an inner join or a join to a
subquery that silently drops exactly the rows the user most needs to see (a job they
just saved and have done nothing with yet). Write the test for that case first.

It needs `next_event_at`, `next_event_title` and `open_event_count` — derived from
`application_events`, per SPEC D9's definition of pending (`occurs_at >= now` and
`outcome IS NULL OR outcome = 'pending'`). Correlated subqueries are fine and clearer
here than window functions.

**2. `v_latest_salary` has a priority rule, not just a recency rule.** Per SPEC D17,
a `basis = 'posting'` row beats an `estimated` row *regardless of which is newer*. The
test in the acceptance criteria (6-month-old posting vs estimate captured today) is
the one that catches a naive `ORDER BY captured_at DESC`.

**3. `PRAGMA foreign_keys` does not persist in the database file.** It is a
per-connection setting and defaults to OFF, so `db.connect()` must set it every time
or the cascade behaviour in SPEC §5 silently does nothing. `journal_mode = WAL` *does*
persist, but setting it every connection is harmless. Your cascade test will pass
against a connection that forgot the pragma only if you test through `db.connect()` —
so test through it.

**4. Database path resolution.** `data/tracker.db` in SPEC §7 is relative to the repo
root, not the caller's cwd. `./tracker init` must behave identically from any
directory. Resolve the root from the module's own location, not `os.getcwd()`.

**5. Migrations need to survive the next twelve steps.** Views and CHECK constraints
cannot be meaningfully `ALTER`ed in SQLite, so later steps will need to drop and
recreate views. Design `migrate(conn)` around a numbered, ordered sequence from the
start — `schema.sql` is migration 1 — rather than a single "create everything if not
exists" call you would have to unpick later. Record applied versions in
`schema_migrations`. Running `migrate` twice must be a clean no-op.

## Scope discipline

- Implement `init` and the global flags only. **Every other subcommand in SPEC §7
  must exist in the argparse tree** — including the `cv`, `salary`, `ingest` and
  `companies` subcommand groups — but their handlers print a clear "not implemented
  yet (Step N)" and exit 1. Later agents should only have to fill in a function, not
  restructure your CLI.
- Define the SPEC §7 exit codes (0/1/2/3) as named constants in one module that every
  later step imports. Do the same for `utcnow()`. If two modules ever format a
  timestamp differently, the comparisons in `v_dashboard` start lying.
- Do not write any ingest, query, export or server logic. If you find yourself
  tempted, that is Step 2+.

## Definition of done

- All eight acceptance criteria in PLAN.md Step 1, each with a real test.
- `.venv/bin/python -m pytest tests/ -q` passes.
- `./tracker init` works from a clean state and is a no-op on a second run. Verify by
  actually running it, twice, and inspecting the resulting schema with
  `sqlite3 data/tracker.db ".schema"`.
- One git commit, message ending with the Co-Authored-By line used in the existing
  commits (see `git log`).

Then report back: what you built, anything in SPEC.md that turned out to be wrong or
underspecified and how you amended it (SPEC §10 — amend the spec, do not silently
diverge), and anything you deliberately left for a later step.

If something in the spec genuinely cannot be built as written, stop and say so rather
than inventing a workaround — the spec is meant to be corrected, and every later step
is reading it.
