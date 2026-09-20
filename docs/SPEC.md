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
- Capturing the full text of a saved job's description, so it can be tailored against.
- Generating a CV tailored to each individual role, from one master CV, and rendering
  it to `.docx` and PDF for download from the dashboard.
- A salary figure for every job: taken from the posting where LinkedIn publishes one,
  and otherwise estimated from market sources with its provenance recorded.

### Out of scope (v1)

- Auto-applying to jobs, or any write action against LinkedIn/Glassdoor.
- Job boards other than LinkedIn (Indeed, Otta, direct careers pages).
- Glassdoor review *text*. We take the numbers only.
- Cover letters. (The CV pipeline is built so this is a small addition later.)
- Currency conversion between salaries (see D19).
- Multi-user, auth, hosting, mobile.
- Notifications/reminders by email.

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
- `v_latest_salary` — one row per job: the most recent `salary_estimates` row with
  `basis = 'posting'` if any exists, otherwise the most recent `estimated` one. A
  figure the employer published always outranks one we derived, however recent
  (SPEC D18).
- `v_dashboard` — the denormalized row the UI and exports both consume: job fields
  + company name + latest Glassdoor columns + application status/dates/notes +
  `next_event_at`, `next_event_title`, `open_event_count` + `glassdoor_url` and
  `job_url` (the UI links out to both) + the `v_latest_salary` columns + `cv_version`,
  `cv_status`, `cv_rendered_at` from the newest `cv_variants` row +
  `has_job_description`.

Both the web API and the exporter read `v_dashboard`. There must not be two
different definitions of "the dashboard row".

### 5.10 `job_descriptions`

The full posting text. One row per job; re-capture replaces it. Needed because a CV
cannot be tailored to a role without the role's own words.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `job_id` | INTEGER NOT NULL UNIQUE FK → jobs(id) ON DELETE CASCADE | |
| `full_text` | TEXT NOT NULL | plain text, whitespace-normalized, no markup |
| `content_hash` | TEXT NOT NULL | sha256 of `full_text`; skip the write if unchanged |
| `source_url` | TEXT NULL | |
| `captured_at` | TEXT NOT NULL | |

### 5.11 `salary_estimates`

Append-only, like Glassdoor ratings. Two kinds of row, distinguished by `basis`, and
the distinction is never collapsed in the UI or an export (SPEC D18).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `job_id` | INTEGER NOT NULL FK → jobs(id) ON DELETE CASCADE | |
| `basis` | TEXT NOT NULL | `posting` = LinkedIn published it. `estimated` = derived from market sources. |
| `currency` | TEXT NOT NULL | ISO 4217, e.g. `EUR`, `GBP`, `USD` |
| `period` | TEXT NOT NULL | `year` \| `month` \| `day` \| `hour` |
| `min_amount` | REAL NULL | |
| `max_amount` | REAL NULL | at least one of min/max required |
| `annualized_min` / `annualized_max` | REAL NULL | derived, see below |
| `confidence` | TEXT NOT NULL | `high` \| `medium` \| `low`. Always `high` for `posting`. |
| `sources_json` | TEXT NULL | JSON array of `{name, url, note}`; **required and non-empty when `basis = 'estimated'`** |
| `method_notes` | TEXT NULL | how the estimate was reached, in a sentence |
| `captured_at` | TEXT NOT NULL | |

Annualization is fixed and deterministic so sorting is stable: `year` ×1,
`month` ×12, `day` ×220, `hour` ×1800. These multipliers live in one constant.

### 5.12 `cv_variants`

One row per (job, version). Versions are immutable once rendered — re-tailoring
produces v2, it never edits v1 (SPEC D15).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `job_id` | INTEGER NOT NULL FK → jobs(id) ON DELETE CASCADE | |
| `version` | INTEGER NOT NULL | 1-based; `UNIQUE(job_id, version)` |
| `status` | TEXT NOT NULL | `draft` \| `rendered` \| `sent` |
| `master_hash` | TEXT NOT NULL | sha256 of `cv/master.yaml` at tailoring time — so a variant built against a stale master is detectable |
| `content_json` | TEXT NOT NULL | the §6.7 variant document |
| `tailoring_notes` | TEXT NULL | why these choices were made |
| `docx_path` | TEXT NULL | relative to repo root |
| `pdf_path` | TEXT NULL | |
| `rendered_at` | TEXT NULL | |
| `created_at` | TEXT NOT NULL | |

### 5.13 The master CV — `cv/master.yaml`

Not a database table. A hand-editable file, the single source of every factual claim
about me, gitignored (it has my phone number and address). Structure:

```yaml
contact:
  name: Caolan Egan
  email: caolanegan1@gmail.com
  phone: "+353 ..."
  location: Dublin, Ireland
  links: { linkedin: "...", github: "..." }
summary_pool:                 # candidates; tailoring picks/adapts ONE
  - id: sum-platform
    text: "Platform engineer with ..."
    tags: [platform, infrastructure]
experience:
  - id: acme-2021            # stable; referenced by every variant
    company: Acme Corp
    title: Senior Platform Engineer
    start: "2021-03"
    end: null                # null = present
    location: Dublin
    bullets:
      - id: acme-2021-b1     # stable; the unit of traceability (D14)
        text: "Cut deploy time from 40 minutes to 6 by ..."
        tags: [ci, kubernetes, performance]
skills:
  - { id: sk-k8s, name: Kubernetes, group: Infrastructure, level: advanced }
education:
  - { id: edu-ucd, institution: "...", qualification: "...", year: "2018" }
certifications: []
```

Every `id` is stable and unique within the file. Tailored variants reference these
ids; a variant that references an id the master does not contain fails validation.

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

### 6.5 Job descriptions → `data/inbox/jd-YYYYMMDD-HHMM.json`

```json
{
  "schema_version": 1,
  "source": "job_descriptions",
  "captured_at": "2026-09-20T22:40:00Z",
  "descriptions": [
    {
      "linkedin_job_id": "4012345678",
      "source_url": "https://www.linkedin.com/jobs/view/4012345678/",
      "full_text": "About the role\n\nWe are looking for ...",
      "salary_text": "€75,000 - €95,000 per year"
    }
  ]
}
```

`full_text` is plain text with runs of whitespace collapsed and no markup. A record
whose `linkedin_job_id` is not already in `jobs` is an error, not an insert — job
descriptions attach to saved jobs, they do not create them. `salary_text`, when the
posting states one, is written through to `jobs.salary_text` and is what the `posting`
salary row is parsed from.

### 6.6 Salary → `data/inbox/salary-YYYYMMDD-HHMM.json`

```json
{
  "schema_version": 1,
  "source": "salary_estimates",
  "captured_at": "2026-09-20T22:55:00Z",
  "estimates": [
    {
      "linkedin_job_id": "4012345678",
      "basis": "estimated",
      "currency": "EUR",
      "period": "year",
      "min_amount": 75000,
      "max_amount": 95000,
      "confidence": "medium",
      "method_notes": "Median of three sources for senior platform roles in Dublin, adjusted down for a 200-person company.",
      "sources": [
        { "name": "Levels.fyi", "url": "https://...", "note": "P50 €88k for L5 Dublin" },
        { "name": "IrishJobs salary guide 2026", "url": "https://...", "note": "€72–92k" }
      ]
    }
  ]
}
```

Validation, per record:
- `basis = 'estimated'` with an empty or absent `sources` array is a **hard error**.
  An estimate without provenance is a guess wearing a number's clothes, and six weeks
  later I will not remember which it was.
- At least one of `min_amount` / `max_amount` must be present, non-negative, and
  `min <= max`.
- `currency` must be three uppercase letters; `period` must be one of the four values
  in §5.11.
- `basis = 'posting'` records are normally produced by parsing `jobs.salary_text`
  locally rather than captured — but the contract accepts them either way.

### 6.7 Tailored CV variant → `data/inbox/cv-<job_id>-v<N>.json`

```json
{
  "schema_version": 1,
  "source": "cv_variant",
  "job_id": 12,
  "created_at": "2026-09-20T23:10:00Z",
  "master_hash": "sha256:abc123...",
  "headline": "Senior Platform Engineer",
  "summary": { "master_id": "sum-platform", "text": "Platform engineer with ..." },
  "experience": [
    {
      "master_id": "acme-2021",
      "bullets": [
        { "master_bullet_id": "acme-2021-b1", "text": "Cut deploy time from 40 minutes to 6 by ..." }
      ]
    }
  ],
  "skills_order": ["sk-k8s", "sk-terraform"],
  "education_ids": ["edu-ucd"],
  "tailoring_notes": "Led on the Kubernetes and CI work; dropped the frontend bullets.",
  "coverage": {
    "jd_keywords_matched": ["kubernetes", "terraform", "ci/cd"],
    "jd_keywords_unmatched": ["golang", "fintech domain"]
  }
}
```

Validation, and this is the part that matters (SPEC D14):
- Every `master_id`, `master_bullet_id`, skill id and education id **must exist** in
  `cv/master.yaml`. Unknown id → hard error.
- Every numeric token in a bullet's `text` must also appear in the master bullet it
  cites. A rephrase may drop "40 minutes"; it may not introduce "£2M" or "12 engineers".
- `company`, `title`, `start` and `end` are **not** present in the variant at all —
  they are copied from the master at render time, so they are structurally unfabricable.
- `master_hash` mismatching the current `cv/master.yaml` is a **warning**, not an
  error: it is stored and surfaced so I know the variant predates a master edit.

### 6.8 Tailoring brief → stdout of `tracker cv brief <job_id>`

The package a tailoring session needs, in one place: the job (title, company,
location), the full JD text, the whole master CV, any existing variants for this job,
and a naive keyword diff between the JD and the master's bullet tags. It is an input
to judgement, not an answer — the keyword diff is a prompt to think, and nothing in
the system treats it as authoritative.

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
| GET | `/api/jobs/{job_id}/cv` | list of variants (version, status, rendered_at, paths, stale-master flag) |
| GET | `/api/jobs/{job_id}/cv/{version}/download?format=docx\|pdf` | streams the file with `Content-Disposition: attachment` |
| POST | `/api/jobs/{job_id}/cv/{version}/render` | renders docx + pdf, returns the updated variant |
| GET | `/api/jobs/{job_id}/description` | the stored JD text, or 404 |
| GET | `/api/jobs/{job_id}/salary` | the salary row plus its sources, or 404 |

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
tracker ingest descriptions <file.json>    store full JD text
tracker ingest salary <file.json>          store posted/estimated salary
tracker salary parse-postings               parse jobs.salary_text → 'posting' rows
tracker salary pending                      jobs with no salary figure, for estimation
tracker cv import-master <file.docx>       one-time: .docx → cv/master.yaml
tracker cv validate-master                 check ids unique, schema valid
tracker cv brief <job_id> [--out PATH]     emit the §6.8 tailoring brief
tracker cv ingest <file.json>              store a tailored variant (§6.7)
tracker cv render <job_id> [--version N] [--formats docx,pdf]
tracker cv list [<job_id>]
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

**D4 — Optional dependencies, each scoped to one subsystem (amended by D17).**
CSV export is stdlib and always works. `openpyxl` is imported only by the xlsx
exporter; `python-docx` and `PyYAML` only by the CV subsystem. Each import happens
inside the function that needs it, and a missing one produces a clear
"pip install X" message rather than a traceback. `tracker init`, ingest, the queries
layer, the server and CSV export must all work with **zero** third-party packages
installed.

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

**D11 — CV tailoring is done by Claude in-session, not by code calling an LLM API.**
Alternatives: an `--all` command hitting the Anthropic API unattended; a purely
deterministic keyword-reorderer. The API route would put a billable key on disk and
punch the first hole in D1's network-free rule, for a task I do a handful of times a
week and want to read before sending. The deterministic route can only select and
reorder, never rephrase, which is most of the value. So: `tracker cv brief` emits
everything needed to tailor, I write the variant, `tracker cv ingest` stores it. The
code owns structure, validation and rendering; the judgement stays in the session.
Consequence: no batch tailoring. Accepted.

**D12 — The master CV is a structured YAML file, converted once from the existing
.docx.** `tracker cv import-master` does a best-effort extraction that I then correct
by hand, once. After that the .docx is irrelevant and `cv/master.yaml` is the source
of every factual claim. Rejected: re-parsing a .docx on every render — heading and
bullet detection on real-world CVs is unreliable, and it would make every render
non-deterministic.

**D13 — `.docx` is rendered first; the PDF is converted from it via headless
LibreOffice.** Alternatives: HTML→PDF via WeasyPrint, or ReportLab direct. Both mean
the .docx and the PDF come from different code paths and drift visually — the version
a recruiter opens would not be the one I proofread. `soffice --headless --convert-to
pdf` guarantees parity. Cost: LibreOffice must be installed; `tracker cv render
--formats docx` still works without it, with a clear message.

**D14 — Tailoring may re-emphasise, but never fabricate. This is enforced
structurally, not by good intentions.**
Every bullet in a variant carries the `master_bullet_id` it derives from, and
validation rejects the variant if that id is not in the master. On top of that, every
numeric token (years, percentages, money, team sizes, durations) in a tailored bullet
must also appear in its source master bullet — a rephrase may drop a metric but may
not introduce one. Employer names, job titles and dates are not in the variant format
at all; they are copied from the master at render time, so they are structurally
unfabricable. Gaps between what the role wants and what I have are reported in
`coverage.jd_keywords_unmatched`, not papered over. A CV is a factual claim made to a
real employer; the cost of a plausible invention surviving into a PDF is mine to pay
at interview, so the system is built so it cannot happen quietly.

**D15 — Variants are versioned and immutable once rendered.** Re-tailoring against an
updated master or a changed JD creates v2. I need to know exactly which document I
sent to which employer, months later, when they ring me about it.

**D16 — Rendered CVs live at `data/cv/<company-slug>-<job-id>-v<N>.docx|pdf` and are
gitignored,** alongside `cv/master.yaml`. Filenames are predictable enough to attach
from a mail client's file dialog without opening the tracker.

**D17 — A stated salary and an estimated one are different claims and are never
merged.** Every salary row carries `basis`; `v_latest_salary` prefers `posting` over
`estimated` regardless of recency; the UI labels estimates explicitly and shows the
sources on hover; exports carry `Salary Basis` and `Salary Confidence` as their own
columns. Alternative considered and rejected: a single "salary" column with the best
available number. That column would eventually be read as fact, and I would walk into
a negotiation anchored on a figure Claude inferred from three blog posts.

**D18 — Estimates must cite sources, enforced at ingest.** An `estimated` row with no
`sources` array fails validation (§6.6). The estimate is Claude's judgement over
market data found at capture time, same division of labour as D1 and D11: the session
does the searching and the reasoning, the code stores the result and its provenance.

**D19 — No currency conversion.** Salaries are stored and displayed in the currency
posted. Sorting is by `annualized_*` within a currency, and mixed-currency sorts group
by currency first. Converting would need a live FX rate — a network dependency, a
staleness problem, and a number that is wrong by the time I read it.

---

## 9. Quality bar

- **Python 3.13, stdlib only** for the core; `openpyxl`, `python-docx` and `PyYAML`
  are optional, subsystem-scoped, function-level imports (D4). `pytest` as a dev
  dependency. YAML is parsed with `yaml.safe_load` only, never `yaml.load`.
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
