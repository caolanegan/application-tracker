# Job Application Tracker — Specification & Decision Record

**Status:** Approved, pre-implementation
**Owner:** Caolan Egan
**Last updated:** 2026-09-20
**Audience:** implementing agents. This document is the source of truth. If code and
spec disagree, the spec wins — or the spec gets amended in the same change that
diverges from it (see §10).

---

## 1. Problem statement

I save jobs on LinkedIn and lose track of them. I want one local view that answers:

- What have I saved, and from which company?
- Is that company any good to work for (Glassdoor overall + the sub-ratings that
  actually matter to me: work/life balance, comp & benefits, career opportunities,
  culture, senior management, diversity)?
- Have I applied? When? What stage am I at? What is the next thing I have to do,
  and when?

Everything runs on my machine. No accounts, no hosted service, no data leaving the
laptop.

---

## 2. Scope

### In scope

- Extracting my own LinkedIn saved-jobs list into structured records.
- Extracting Glassdoor overall rating + sub-ratings per company, cached.
- A durable local store that survives repeated re-extraction without losing my
  hand-entered tracking data.
- A localhost web page that reads that store and writes application status,
  interview events and notes back to it.
- CSV and Excel export.

### Out of scope (v1)

- Auto-applying to jobs, or any write action against LinkedIn/Glassdoor.
- Job boards other than LinkedIn (Indeed, Otta, direct careers pages).
- Scraping full job descriptions or Glassdoor review text. We take the numbers and
  a short snippet only.
- Multi-user, auth, hosting, mobile.
- Salary estimation, résumé tailoring, notifications/reminders by email.

### Explicitly deferred (v2 candidates, do not build now)

- Reminder/notification engine for upcoming interviews.
- Additional sources behind the same ingest contract.
- Historical charting of pipeline over time.

---

## 3. Constraints and the legal/ToS position

This is the single most important design constraint, and it is why the architecture
looks the way it does.

LinkedIn and Glassdoor both prohibit automated scraping in their terms of service,
and both deploy bot detection (Glassdoor sits behind Cloudflare with a content wall;
LinkedIn issues account checkpoints against unusual automated traffic). An
unattended headless scraper against either would be fragile, would break silently,
and would put my actual LinkedIn account at risk.

**Decision: no component of this system ever makes a network request to LinkedIn or
Glassdoor.** Not `requests`, not `httpx`, not Playwright, not a headless browser.

Instead, data acquisition is **agent-driven and human-paced**: Claude drives my real,
already-logged-in Chrome session via the Claude-in-Chrome browser tools, reads the
pages I am entitled to read as a signed-in user, and writes a JSON file to
`data/inbox/`. The Python code's job starts at that JSON file.

This has three consequences that implementers must respect:

1. **The codebase is network-free.** Any PR that adds an HTTP client pointed at
   linkedin.com or glassdoor.com is rejected. There is no "fallback scraper".
2. **The JSON contracts in §6 are the seam.** They are a public API between the
   browser-driving agent and the code. Changing them is a breaking change and
   requires a `schema_version` bump.
3. **Ingestion is idempotent and re-runnable.** Because capture is manual, it will
   happen at irregular intervals with partial and overlapping data. Every ingest
   path must be safe to run twice on the same file.

Rate discipline for the capture agent: Glassdoor company pages are visited at most
one every few seconds, each company is fetched at most once per 30 days (§6.3), and
capture is always attended — if a page shows a CAPTCHA or checkpoint, the agent
stops and reports, it does not try to work around it.

---

## 4. Architecture

```
┌────────────────────────┐
│  Claude in Chrome      │  attended, manual trigger
│  (user's real session) │
└───────────┬────────────┘
            │ writes JSON  (contracts, §6)
            ▼
   data/inbox/*.json
            │
            │  tracker ingest linkedin|glassdoor
            ▼
┌────────────────────────┐
│  SQLite: tracker.db    │  ← SOURCE OF TRUTH
│  (companies, jobs,     │
│   ratings, apps,       │
│   events)              │
└───┬──────────────┬─────┘
    │              │
    │              │ tracker export csv|xlsx
    │              ▼
    │        data/exports/*.csv|.xlsx
    │
    │ tracker serve  (stdlib http.server, 127.0.0.1:8765)
    ▼
┌────────────────────────┐
│  web/index.html        │  vanilla JS, no build step
│  reads GET /api/jobs   │
│  writes POST /api/...  │
└────────────────────────┘
```

### Component responsibilities

| Component | Responsibility | Must NOT |
|---|---|---|
| Capture agent (Chrome) | Read pages, emit JSON to `data/inbox/` | Write to the DB directly |
| `tracker ingest` | Validate JSON, resolve companies, upsert | Invent data for missing fields |
| SQLite | Hold all state | Be bypassed by the web layer writing files |
| `tracker serve` | Serve `web/` + JSON API over localhost only | Bind to 0.0.0.0, or accept remote origins |
| `web/index.html` | Render + edit | Hold state that isn't persisted via the API |
| `tracker export` | Snapshot for Excel | Be treated as a source of truth |

---

## 5. Data model

SQLite, at `data/tracker.db`. `PRAGMA foreign_keys = ON` on every connection.
`PRAGMA journal_mode = WAL`. All timestamps are **UTC ISO-8601 strings**
(`2026-09-20T21:53:00Z`); all dates are `YYYY-MM-DD`.

### 5.1 `companies`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT NOT NULL | display name, as first seen |
| `normalized_name` | TEXT NOT NULL UNIQUE | see §5.8 |
| `linkedin_company_url` | TEXT NULL | |
| `glassdoor_url` | TEXT NULL | |
| `glassdoor_company_id` | TEXT NULL | the `E12345` number |
| `glassdoor_lookup_state` | TEXT NOT NULL | `pending` \| `resolved` \| `not_found` \| `skipped` |
| `created_at` / `updated_at` | TEXT NOT NULL | |

### 5.2 `company_aliases`

Handles "Acme Corp" vs "Acme Corporation Ltd" vs the Glassdoor spelling.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `company_id` | INTEGER NOT NULL FK → companies(id) ON DELETE CASCADE | |
| `alias_normalized` | TEXT NOT NULL UNIQUE | |
| `source` | TEXT NOT NULL | `linkedin` \| `glassdoor` \| `manual` |

### 5.3 `jobs`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `linkedin_job_id` | TEXT NOT NULL UNIQUE | natural key from the job URL |
| `title` | TEXT NOT NULL | |
| `company_id` | INTEGER NOT NULL FK → companies(id) | |
| `location` | TEXT NULL | raw string, e.g. `Dublin, County Dublin, Ireland` |
| `workplace_type` | TEXT NULL | `Remote` \| `Hybrid` \| `On-site` \| NULL |
| `employment_type` | TEXT NULL | `Full-time` etc., often absent |
| `salary_text` | TEXT NULL | raw, unparsed — LinkedIn's format is inconsistent |
| `posted_text` | TEXT NULL | raw, e.g. `2 weeks ago` |
| `job_url` | TEXT NOT NULL | |
| `easy_apply` | INTEGER NOT NULL DEFAULT 0 | 0/1 |
| `description_snippet` | TEXT NULL | ≤ 500 chars, optional |
| `first_seen_at` | TEXT NOT NULL | |
| `last_seen_at` | TEXT NOT NULL | bumped on every ingest that includes it |
| `is_saved` | INTEGER NOT NULL DEFAULT 1 | set to 0 when a full capture no longer lists it |

`is_saved = 0` is a soft delete. Rows are **never** hard-deleted by ingest: my
application history hangs off them.

### 5.4 `glassdoor_ratings`

Append-only history; one row per capture. Never updated in place.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `company_id` | INTEGER NOT NULL FK → companies(id) ON DELETE CASCADE | |
| `overall` | REAL NULL | 0.0–5.0 |
| `review_count` | INTEGER NULL | |
| `recommend_to_friend_pct` | INTEGER NULL | 0–100 |
| `ceo_approval_pct` | INTEGER NULL | 0–100 |
| `work_life_balance` | REAL NULL | |
| `compensation_benefits` | REAL NULL | |
| `culture_values` | REAL NULL | |
| `diversity_inclusion` | REAL NULL | |
| `career_opportunities` | REAL NULL | |
| `senior_management` | REAL NULL | |
| `source_url` | TEXT NULL | |
| `match_confidence` | TEXT NOT NULL | `high` \| `medium` \| `low` — the capture agent's own call |
| `captured_at` | TEXT NOT NULL | |

All rating columns are nullable on purpose: Glassdoor hides sub-ratings for
companies with few reviews. **Never substitute 0 for a missing rating.** NULL means
"not published", 0 would mean "rated zero" and would poison any sort or average.

### 5.5 `applications`

Exactly one per job. Created lazily on first status change (or eagerly at ingest
with status `saved` — implementer's choice, but be consistent).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `job_id` | INTEGER NOT NULL UNIQUE FK → jobs(id) ON DELETE CASCADE | |
| `status` | TEXT NOT NULL | see §5.7 |
| `applied_on` | TEXT NULL | date |
| `apply_channel` | TEXT NULL | `linkedin_easy_apply` \| `company_site` \| `recruiter` \| `referral` \| `other` |
| `priority` | INTEGER NOT NULL DEFAULT 0 | 0–3, my own interest level |
| `next_action` | TEXT NULL | free text, e.g. `send follow-up email` |
| `next_action_on` | TEXT NULL | date — drives the "needs attention" view |
| `notes` | TEXT NULL | markdown-ish free text |
| `created_at` / `updated_at` | TEXT NOT NULL | |

### 5.6 `application_events`

The interview pipeline. Append-mostly; editable and deletable from the UI.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `application_id` | INTEGER NOT NULL FK → applications(id) ON DELETE CASCADE | |
| `event_type` | TEXT NOT NULL | `applied` \| `recruiter_screen` \| `tech_screen` \| `interview` \| `take_home` \| `onsite` \| `offer` \| `rejected` \| `withdrawn` \| `note` |
| `occurs_at` | TEXT NOT NULL | ISO-8601 datetime; future-dated = a *pending* interview |
| `title` | TEXT NULL | e.g. `Round 2 — system design` |
| `outcome` | TEXT NULL | `pending` \| `passed` \| `failed` \| `cancelled` |
| `notes` | TEXT NULL | |
| `created_at` | TEXT NOT NULL | |

"Pending interviews" is a query, not a column: events with `occurs_at >= now` and
`outcome IS NULL OR outcome = 'pending'`.

### 5.7 `applications.status` — the state set

`saved`, `applied`, `screening`, `interviewing`, `offer`, `accepted`, `rejected`,
`withdrawn`, `ghosted`, `not_interested`.

Enforced by a `CHECK` constraint. Transitions are **not** restricted — real hiring
processes go sideways and I want to be able to set anything from the dropdown.
Terminal-ish statuses for filtering purposes: `accepted`, `rejected`, `withdrawn`,
`ghosted`, `not_interested`.

### 5.8 Company name normalization

One function, used by every path that resolves a company. Deterministic, no
network, no fuzzy library:

1. Unicode NFKD normalize, strip combining marks, casefold.
2. Strip a trailing legal suffix, repeatedly: `inc`, `inc.`, `llc`, `l.l.c.`,
   `ltd`, `ltd.`, `limited`, `plc`, `gmbh`, `bv`, `nv`, `sa`, `ag`, `pty`, `co`,
   `corp`, `corporation`, `company`, `holdings`, `group`.
3. Remove all characters that are not `[a-z0-9 ]`.
4. Collapse whitespace, strip.

Resolution order when ingest sees a company name:
`company_aliases.alias_normalized` → `companies.normalized_name` → create new.

Ambiguity is resolved by **creating a new company, never by guessing a merge**. A
`tracker companies merge <keep_id> <drop_id>` command exists for me to fix it by
hand; it repoints jobs and ratings and writes an alias row.

### 5.9 Views

- `v_latest_glassdoor` — one row per company, the most recent `glassdoor_ratings`
  row by `captured_at` (ties broken by `id`).
- `v_dashboard` — the denormalized row the UI and exports both consume: job fields
  + company name + latest Glassdoor columns + application status/dates/notes +
  `next_event_at`, `next_event_title`, `open_event_count`.

Both the web API and the exporter read `v_dashboard`. There must not be two
different definitions of "the dashboard row".

---

## 6. Interface contracts

### 6.1 LinkedIn saved jobs → `data/inbox/linkedin-saved-YYYYMMDD-HHMM.json`

```json
{
  "schema_version": 1,
  "source": "linkedin_saved_jobs",
  "captured_at": "2026-09-20T21:53:00Z",
  "capture_complete": true,
  "jobs": [
    {
      "linkedin_job_id": "4012345678",
      "title": "Senior Platform Engineer",
      "company_name": "Acme Corp",
      "company_url": "https://www.linkedin.com/company/acme-corp/",
      "location": "Dublin, County Dublin, Ireland",
      "workplace_type": "Hybrid",
      "employment_type": null,
      "salary_text": null,
      "posted_text": "2 weeks ago",
      "job_url": "https://www.linkedin.com/jobs/view/4012345678/",
      "easy_apply": true,
      "applied_on_linkedin": false,
      "description_snippet": null
    }
  ]
}
```

- Required per job: `linkedin_job_id`, `title`, `company_name`, `job_url`.
  Everything else may be `null`. A job missing a required field is **rejected with
  a line-item error**, not silently dropped — ingest reports it and continues.
- `capture_complete`: `true` only when the agent paginated to the end of the saved
  list. When `true`, ingest marks jobs absent from the payload as `is_saved = 0`.
  When `false`, absent jobs are left untouched. Getting this wrong silently
  un-saves my whole list, so it defaults to `false` if missing.
- `applied_on_linkedin: true` seeds `applications.status = 'applied'` **only if no
  application row exists yet**. It never overwrites a status I set by hand.

### 6.2 Glassdoor ratings → `data/inbox/glassdoor-YYYYMMDD-HHMM.json`

```json
{
  "schema_version": 1,
  "source": "glassdoor",
  "captured_at": "2026-09-20T22:10:00Z",
  "companies": [
    {
      "company_name": "Acme Corp",
      "glassdoor_url": "https://www.glassdoor.com/Reviews/Acme-Corp-Reviews-E12345.htm",
      "glassdoor_company_id": "12345",
      "match_confidence": "high",
      "overall": 3.9,
      "review_count": 1204,
      "recommend_to_friend_pct": 78,
      "ceo_approval_pct": 85,
      "ratings": {
        "work_life_balance": 3.8,
        "compensation_benefits": 4.1,
        "culture_values": 3.7,
        "diversity_inclusion": 4.0,
        "career_opportunities": 3.5,
        "senior_management": 3.2
      },
      "not_found": false,
      "notes": null
    }
  ]
}
```

- `not_found: true` with everything else null is a valid, meaningful record: it sets
  `glassdoor_lookup_state = 'not_found'` so the company stops appearing in the
  worklist. Small/private companies genuinely have no Glassdoor page.
- `match_confidence` is the agent's judgement that the Glassdoor page is actually
  the same company as the LinkedIn employer. `low` still gets stored, but the UI
  badges it so I know not to trust it.
- Ratings out of range (`overall` outside 0–5, percentages outside 0–100) are a
  validation error for that record.

### 6.3 Glassdoor worklist → stdout of `tracker companies pending-glassdoor`

```json
{
  "generated_at": "2026-09-20T22:00:00Z",
  "stale_after_days": 30,
  "companies": [
    {
      "company_id": 7,
      "company_name": "Acme Corp",
      "job_count": 3,
      "reason": "never_fetched",
      "search_url": "https://www.glassdoor.com/Search/results.htm?keyword=Acme+Corp"
    }
  ]
}
```

`reason` ∈ `never_fetched` | `stale`. Companies with `glassdoor_lookup_state` of
`not_found` or `skipped` are excluded. This is what the capture agent reads to know
what to go and fetch — it is the handshake that stops us re-fetching a company
every session.

### 6.4 HTTP API (localhost only)

All responses JSON. All mutations return the updated resource.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | serve `web/index.html` |
| GET | `/api/jobs` | dashboard rows from `v_dashboard`; query params `status`, `q`, `min_overall`, `sort`, `saved_only` |
| GET | `/api/jobs/{id}` | one row + its full event list |
| PATCH | `/api/applications/{job_id}` | body: any of `status`, `applied_on`, `apply_channel`, `priority`, `next_action`, `next_action_on`, `notes`. Creates the application row if absent. |
| GET | `/api/jobs/{job_id}/events` | list |
| POST | `/api/jobs/{job_id}/events` | create event |
| PATCH | `/api/events/{id}` | update event |
| DELETE | `/api/events/{id}` | delete event |
| GET | `/api/stats` | counts by status, pending-interview count, needs-attention count |
| POST | `/api/export` | body `{"format": "csv"|"xlsx"}` → writes to `data/exports/`, returns path |

Errors: `{"error": "human readable", "field": "optional"}` with 400/404/500.

**Security posture:** bind `127.0.0.1` only. Reject any request whose `Origin`
header is present and is not `http://127.0.0.1:<port>` or `http://localhost:<port>`
(blocks DNS-rebinding and drive-by fetches from other pages while the server is up).
No auth beyond that — it is a single-user localhost tool.

---

## 7. CLI surface

Entry point: `python -m tracker <command>` (and a `./tracker` shim script).

```
tracker init                              create/migrate data/tracker.db
tracker ingest linkedin <file.json>       upsert jobs + companies
tracker ingest glassdoor <file.json>      append ratings, resolve lookup state
tracker ingest --all                      process every unprocessed file in data/inbox/
tracker companies pending-glassdoor       emit the §6.3 worklist JSON
tracker companies list
tracker companies merge <keep_id> <drop_id>
tracker status <job_id> <status> [--applied-on DATE] [--note TEXT]
tracker event add <job_id> --type T --at DATETIME [--title T] [--notes N]
tracker export csv [--out PATH]
tracker export xlsx [--out PATH]
tracker serve [--port 8765] [--no-open]
tracker stats
```

Global flags: `--db PATH` (default `data/tracker.db`), `--json` (machine-readable
output on any command), `--dry-run` (ingest only: validate + report, write nothing).

Exit codes: `0` ok, `1` unexpected error, `2` validation failure (bad input file),
`3` not found.

---

## 8. Decision record

Each entry: the decision, the alternatives considered, and why.

**D1 — No programmatic access to LinkedIn or Glassdoor. Capture is agent-driven
through the user's own Chrome.**
Alternatives: Playwright with a persistent profile; third-party jobs APIs; paid
Glassdoor data vendors. Rejected because the Playwright route trips bot detection
and risks the LinkedIn account, LinkedIn's public API does not expose saved jobs,
and paying a vendor is disproportionate for one person's job search. Consequence:
capture is manual and attended. Accepted — I run this a few times a week, not hourly.

**D2 — SQLite is the source of truth; CSV/Excel are exports, not inputs.**
Alternative: CSV as the store, as originally sketched. Rejected because re-scraping
would have to merge my hand-typed application status back into a regenerated file,
and every merge bug loses tracking data I cannot recover. SQLite gives real keys and
foreign keys. Cost: I can't edit the store in Excel. Mitigated by the web UI being
the editing surface, plus `tracker export xlsx` whenever I want a spreadsheet.

**D3 — Stdlib `http.server` + vanilla JS. No FastAPI, no npm, no build step.**
Alternative: FastAPI + uvicorn + a Vite frontend. Rejected for a ~10-endpoint
single-user localhost app: it adds a virtualenv, a lockfile and a build step to
something that should start with one command and keep working in six months when I
come back to it. `uv` is not installed on this machine and I don't want a dependency
on it. Cost: hand-rolled routing and JSON handling, no automatic OpenAPI docs, no
free request validation — so §9's validation tests matter more. Revisit if the
endpoint count passes ~25.

**D4 — One optional dependency: `openpyxl`, for xlsx export only.**
CSV export is stdlib and always works. `tracker export xlsx` fails with a clear
"pip install openpyxl" message if it is absent. Nothing else in the system may
import it.

**D5 — Glassdoor ratings are append-only history, not an updated row.**
Costs a view (`v_latest_glassdoor`) but means I can see a company's rating move, and
a bad/low-confidence capture never destroys a good earlier one.

**D6 — Missing ratings are NULL, never 0.** Sorting by "worst work/life balance"
must not surface every company that simply doesn't publish that figure.

**D7 — Ingest never hard-deletes jobs.** Unsaving a job on LinkedIn sets
`is_saved = 0`. My application history for that job outlives my LinkedIn saved list.

**D8 — Company matching is deterministic normalization + an explicit alias table,
never fuzzy matching.** A fuzzy matcher that silently merges two different
companies corrupts the data in a way I would not notice. Deterministic rules plus a
manual `merge` command fail visibly instead.

**D9 — "Pending interviews" is a derived query over `application_events`, not a
status column.** I can have a scheduled second round and a pending take-home at the
same time; a single status field cannot represent that.

**D10 — The JSON inbox is a durable, versioned, on-disk artifact.** The capture
agent could have called the API directly. Writing files instead means captures are
replayable, diffable, and testable as fixtures, and a bad ingest is recoverable by
re-running it.

---

## 9. Quality bar

- **Python 3.13, stdlib only** (plus optional `openpyxl`, D4). `pytest` for tests,
  as a dev dependency.
- Type hints on every public function. `from __future__ import annotations`.
- No network calls anywhere in `src/`. A test asserts this by grepping for
  `requests|httpx|urllib.request|playwright|selenium` in `src/`.
- Every ingest path has a fixture-driven test in `tests/fixtures/`, including the
  nasty cases: duplicate job ids in one payload, a company appearing under two
  spellings, missing required fields, out-of-range ratings, `capture_complete:false`
  followed by `true`, and re-running the same file twice (must be a no-op).
- Ingest is transactional per file: a validation failure part-way through rolls back
  the whole file, and the error report names every bad record.
- No secrets, cookies or session tokens are ever written to disk by this codebase.
- `data/` is gitignored. This repo must never contain my job data.

---

## 10. Changing this spec

Implementing agents that hit a genuine conflict between the spec and reality should:
amend the relevant §, add a `D<n>` entry recording what changed and why, and note it
in the step's completion summary. Do not silently diverge — the next agent is
reading this file, not your code.
