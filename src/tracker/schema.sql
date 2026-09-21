-- Migration 1: full initial schema (SPEC §5).
-- SQLite cannot meaningfully ALTER a view or a CHECK constraint, so later schema
-- changes must arrive as new numbered migrations in db.py, not edits to this file.

CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- 5.1 companies
CREATE TABLE companies (
    id                     INTEGER PRIMARY KEY,
    name                   TEXT NOT NULL,
    normalized_name        TEXT NOT NULL UNIQUE,
    linkedin_company_url   TEXT,
    glassdoor_url          TEXT,
    glassdoor_company_id   TEXT,
    glassdoor_lookup_state TEXT NOT NULL DEFAULT 'pending'
        CHECK (glassdoor_lookup_state IN ('pending', 'resolved', 'not_found', 'skipped')),
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

-- 5.2 company_aliases
CREATE TABLE company_aliases (
    id                INTEGER PRIMARY KEY,
    company_id        INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    alias_normalized  TEXT NOT NULL UNIQUE,
    source            TEXT NOT NULL CHECK (source IN ('linkedin', 'glassdoor', 'manual'))
);

-- 5.3 jobs
CREATE TABLE jobs (
    id                   INTEGER PRIMARY KEY,
    linkedin_job_id      TEXT NOT NULL UNIQUE,
    title                TEXT NOT NULL,
    company_id           INTEGER NOT NULL REFERENCES companies(id),
    location             TEXT,
    workplace_type       TEXT CHECK (workplace_type IN ('Remote', 'Hybrid', 'On-site')),
    employment_type      TEXT,
    salary_text          TEXT,
    posted_text          TEXT,
    job_url              TEXT NOT NULL,
    easy_apply           INTEGER NOT NULL DEFAULT 0 CHECK (easy_apply IN (0, 1)),
    description_snippet  TEXT,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    is_saved             INTEGER NOT NULL DEFAULT 1 CHECK (is_saved IN (0, 1))
);

-- 5.4 glassdoor_ratings — append-only history, never updated in place (D5).
-- All rating columns are nullable on purpose: NULL means "not published", never 0 (D6).
CREATE TABLE glassdoor_ratings (
    id                       INTEGER PRIMARY KEY,
    company_id               INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    overall                  REAL CHECK (overall BETWEEN 0 AND 5),
    review_count             INTEGER CHECK (review_count >= 0),
    recommend_to_friend_pct  INTEGER CHECK (recommend_to_friend_pct BETWEEN 0 AND 100),
    ceo_approval_pct         INTEGER CHECK (ceo_approval_pct BETWEEN 0 AND 100),
    work_life_balance        REAL CHECK (work_life_balance BETWEEN 0 AND 5),
    compensation_benefits    REAL CHECK (compensation_benefits BETWEEN 0 AND 5),
    culture_values           REAL CHECK (culture_values BETWEEN 0 AND 5),
    diversity_inclusion      REAL CHECK (diversity_inclusion BETWEEN 0 AND 5),
    career_opportunities     REAL CHECK (career_opportunities BETWEEN 0 AND 5),
    senior_management        REAL CHECK (senior_management BETWEEN 0 AND 5),
    source_url               TEXT,
    match_confidence         TEXT NOT NULL CHECK (match_confidence IN ('high', 'medium', 'low')),
    captured_at               TEXT NOT NULL
);

-- 5.5 applications — exactly one per job.
CREATE TABLE applications (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    status          TEXT NOT NULL CHECK (status IN (
        'saved', 'applied', 'screening', 'interviewing', 'offer',
        'accepted', 'rejected', 'withdrawn', 'ghosted', 'not_interested'
    )),
    applied_on      TEXT,
    apply_channel   TEXT CHECK (apply_channel IN (
        'linkedin_easy_apply', 'company_site', 'recruiter', 'referral', 'other'
    )),
    priority        INTEGER NOT NULL DEFAULT 0 CHECK (priority BETWEEN 0 AND 3),
    next_action     TEXT,
    next_action_on  TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- 5.6 application_events — the interview pipeline. Append-mostly; editable/deletable
-- from the UI. "Pending interviews" (D9) is a query over this table, not a column.
CREATE TABLE application_events (
    id              INTEGER PRIMARY KEY,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL CHECK (event_type IN (
        'applied', 'recruiter_screen', 'tech_screen', 'interview',
        'take_home', 'onsite', 'offer', 'rejected', 'withdrawn', 'note'
    )),
    occurs_at   TEXT NOT NULL,
    title       TEXT,
    outcome     TEXT CHECK (outcome IN ('pending', 'passed', 'failed', 'cancelled')),
    notes       TEXT,
    created_at  TEXT NOT NULL
);

-- 5.10 job_descriptions — one row per job; re-capture replaces it.
CREATE TABLE job_descriptions (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    full_text     TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    source_url    TEXT,
    captured_at   TEXT NOT NULL
);

-- 5.11 salary_estimates — append-only, like Glassdoor ratings (D17, D18).
CREATE TABLE salary_estimates (
    id               INTEGER PRIMARY KEY,
    job_id           INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    basis            TEXT NOT NULL CHECK (basis IN ('posting', 'estimated')),
    currency         TEXT NOT NULL CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
    period           TEXT NOT NULL CHECK (period IN ('year', 'month', 'day', 'hour')),
    min_amount       REAL,
    max_amount       REAL,
    annualized_min   REAL,
    annualized_max   REAL,
    confidence       TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    sources_json     TEXT,
    method_notes     TEXT,
    captured_at      TEXT NOT NULL,
    CHECK (min_amount IS NOT NULL OR max_amount IS NOT NULL)
);

-- 5.12 cv_variants — one row per (job, version); immutable once rendered (D15).
CREATE TABLE cv_variants (
    id               INTEGER PRIMARY KEY,
    job_id           INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    version          INTEGER NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('draft', 'rendered', 'sent')),
    master_hash      TEXT NOT NULL,
    content_json     TEXT NOT NULL,
    tailoring_notes  TEXT,
    docx_path        TEXT,
    pdf_path         TEXT,
    rendered_at      TEXT,
    created_at       TEXT NOT NULL,
    UNIQUE (job_id, version)
);

-- Indexes (SPEC §5, Step 1 "Pitfalls" minimum list). SQLite does not auto-index
-- foreign-key columns, so these are explicit even where a FK exists.
CREATE INDEX idx_jobs_company_id ON jobs(company_id);
CREATE INDEX idx_jobs_is_saved ON jobs(is_saved);
CREATE INDEX idx_glassdoor_ratings_company_captured ON glassdoor_ratings(company_id, captured_at);
CREATE INDEX idx_applications_status ON applications(status);
CREATE INDEX idx_application_events_app_occurs ON application_events(application_id, occurs_at);
CREATE INDEX idx_salary_estimates_job_basis_captured ON salary_estimates(job_id, basis, captured_at);
-- cv_variants(job_id, version) is already indexed by the UNIQUE constraint above.

-- 5.9 v_latest_glassdoor — one row per company: the most recent glassdoor_ratings
-- row by captured_at, ties broken by id.
CREATE VIEW v_latest_glassdoor AS
SELECT
    id, company_id, overall, review_count, recommend_to_friend_pct,
    ceo_approval_pct, work_life_balance, compensation_benefits, culture_values,
    diversity_inclusion, career_opportunities, senior_management, source_url,
    match_confidence, captured_at
FROM (
    SELECT
        gr.*,
        ROW_NUMBER() OVER (
            PARTITION BY gr.company_id
            ORDER BY gr.captured_at DESC, gr.id DESC
        ) AS rn
    FROM glassdoor_ratings gr
) gr
WHERE gr.rn = 1;

-- 5.9 / D17 v_latest_salary — one row per job: the most recent 'posting' row if any
-- exists, otherwise the most recent 'estimated' one. A posting figure always
-- outranks an estimated one, however recent — this is a priority rule, not a
-- recency rule.
CREATE VIEW v_latest_salary AS
SELECT
    id, job_id, basis, currency, period, min_amount, max_amount,
    annualized_min, annualized_max, confidence, sources_json, method_notes,
    captured_at
FROM (
    SELECT
        se.*,
        CASE
            WHEN EXISTS (
                SELECT 1 FROM salary_estimates p
                WHERE p.job_id = se.job_id AND p.basis = 'posting'
            ) THEN 'posting'
            ELSE 'estimated'
        END AS preferred_basis,
        ROW_NUMBER() OVER (
            PARTITION BY se.job_id, se.basis
            ORDER BY se.captured_at DESC, se.id DESC
        ) AS rn
    FROM salary_estimates se
) se
WHERE se.basis = se.preferred_basis AND se.rn = 1;

-- 5.9 v_dashboard — the denormalized row the web API and the exporter both read.
-- There must not be a second definition of "the dashboard row" anywhere else.
-- Every join below a job's own row is a LEFT JOIN: a job with no rating, no salary,
-- no CV and no application must still return one row, with NULLs, not be dropped.
CREATE VIEW v_dashboard AS
SELECT
    j.id                          AS job_id,
    j.linkedin_job_id,
    j.title,
    j.company_id,
    c.name                        AS company_name,
    j.location,
    j.workplace_type,
    j.employment_type,
    j.salary_text,
    j.posted_text,
    j.job_url,
    j.easy_apply,
    j.description_snippet,
    j.first_seen_at,
    j.last_seen_at,
    j.is_saved,

    c.glassdoor_url,
    c.glassdoor_company_id,
    c.glassdoor_lookup_state,

    vlg.overall                   AS glassdoor_overall,
    vlg.review_count              AS glassdoor_review_count,
    vlg.recommend_to_friend_pct   AS glassdoor_recommend_to_friend_pct,
    vlg.ceo_approval_pct          AS glassdoor_ceo_approval_pct,
    vlg.work_life_balance         AS glassdoor_work_life_balance,
    vlg.compensation_benefits     AS glassdoor_compensation_benefits,
    vlg.culture_values            AS glassdoor_culture_values,
    vlg.diversity_inclusion       AS glassdoor_diversity_inclusion,
    vlg.career_opportunities      AS glassdoor_career_opportunities,
    vlg.senior_management         AS glassdoor_senior_management,
    vlg.match_confidence          AS glassdoor_match_confidence,
    vlg.captured_at               AS glassdoor_captured_at,

    a.id                           AS application_id,
    a.status                       AS status,
    a.applied_on                   AS applied_on,
    a.apply_channel                AS apply_channel,
    a.priority                     AS priority,
    a.next_action                  AS next_action,
    a.next_action_on               AS next_action_on,
    a.notes                        AS notes,
    a.created_at                   AS application_created_at,
    a.updated_at                   AS application_updated_at,

    (
        SELECT ae.occurs_at
        FROM application_events ae
        WHERE ae.application_id = a.id
          AND ae.occurs_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
          AND (ae.outcome IS NULL OR ae.outcome = 'pending')
        ORDER BY ae.occurs_at ASC, ae.id ASC
        LIMIT 1
    )                               AS next_event_at,
    (
        SELECT ae.title
        FROM application_events ae
        WHERE ae.application_id = a.id
          AND ae.occurs_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
          AND (ae.outcome IS NULL OR ae.outcome = 'pending')
        ORDER BY ae.occurs_at ASC, ae.id ASC
        LIMIT 1
    )                               AS next_event_title,
    (
        SELECT COUNT(*)
        FROM application_events ae
        WHERE ae.application_id = a.id
          AND ae.occurs_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
          AND (ae.outcome IS NULL OR ae.outcome = 'pending')
    )                               AS open_event_count,

    vls.basis                      AS salary_basis,
    vls.currency                   AS salary_currency,
    vls.period                     AS salary_period,
    vls.min_amount                 AS salary_min_amount,
    vls.max_amount                 AS salary_max_amount,
    vls.annualized_min             AS salary_annualized_min,
    vls.annualized_max             AS salary_annualized_max,
    vls.confidence                 AS salary_confidence,
    vls.sources_json               AS salary_sources_json,
    vls.method_notes               AS salary_method_notes,
    vls.captured_at                AS salary_captured_at,

    cvv.version                    AS cv_version,
    cvv.status                     AS cv_status,
    cvv.rendered_at                AS cv_rendered_at,

    CASE WHEN jd.id IS NOT NULL THEN 1 ELSE 0 END AS has_job_description

FROM jobs j
JOIN companies c ON c.id = j.company_id
LEFT JOIN v_latest_glassdoor vlg ON vlg.company_id = c.id
LEFT JOIN applications a ON a.job_id = j.id
LEFT JOIN v_latest_salary vls ON vls.job_id = j.id
LEFT JOIN job_descriptions jd ON jd.job_id = j.id
LEFT JOIN cv_variants cvv ON cvv.id = (
    SELECT id FROM cv_variants WHERE job_id = j.id ORDER BY version DESC LIMIT 1
);
