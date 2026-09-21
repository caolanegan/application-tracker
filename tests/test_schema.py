from __future__ import annotations

import sqlite3

import pytest

from tracker import db

EXPECTED_TABLES = {
    "schema_migrations": {"version", "applied_at"},
    "companies": {
        "id", "name", "normalized_name", "linkedin_company_url", "glassdoor_url",
        "glassdoor_company_id", "glassdoor_lookup_state", "created_at", "updated_at",
    },
    "company_aliases": {"id", "company_id", "alias_normalized", "source"},
    "jobs": {
        "id", "linkedin_job_id", "title", "company_id", "location", "workplace_type",
        "employment_type", "salary_text", "posted_text", "job_url", "easy_apply",
        "description_snippet", "first_seen_at", "last_seen_at", "is_saved",
    },
    "glassdoor_ratings": {
        "id", "company_id", "overall", "review_count", "recommend_to_friend_pct",
        "ceo_approval_pct", "work_life_balance", "compensation_benefits",
        "culture_values", "diversity_inclusion", "career_opportunities",
        "senior_management", "source_url", "match_confidence", "captured_at",
    },
    "applications": {
        "id", "job_id", "status", "applied_on", "apply_channel", "priority",
        "next_action", "next_action_on", "notes", "created_at", "updated_at",
    },
    "application_events": {
        "id", "application_id", "event_type", "occurs_at", "title", "outcome",
        "notes", "created_at",
    },
    "job_descriptions": {
        "id", "job_id", "full_text", "content_hash", "source_url", "captured_at",
    },
    "salary_estimates": {
        "id", "job_id", "basis", "currency", "period", "min_amount", "max_amount",
        "annualized_min", "annualized_max", "confidence", "sources_json",
        "method_notes", "captured_at",
    },
    "cv_variants": {
        "id", "job_id", "version", "status", "master_hash", "content_json",
        "tailoring_notes", "docx_path", "pdf_path", "rendered_at", "created_at",
    },
}

EXPECTED_VIEWS = {"v_latest_glassdoor", "v_latest_salary", "v_dashboard"}


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "tracker.db")
    db.migrate(connection)
    yield connection
    connection.close()


def _columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({name})")}


def insert_company(conn, name="Acme Corp", normalized_name="acme corp"):
    now = db.utcnow()
    cur = conn.execute(
        """
        INSERT INTO companies (name, normalized_name, glassdoor_lookup_state, created_at, updated_at)
        VALUES (?, ?, 'pending', ?, ?)
        """,
        (name, normalized_name, now, now),
    )
    return cur.lastrowid


def insert_job(conn, company_id, linkedin_job_id="1", title="Engineer"):
    now = db.utcnow()
    cur = conn.execute(
        """
        INSERT INTO jobs (linkedin_job_id, title, company_id, job_url, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, 'https://example.com/job', ?, ?)
        """,
        (linkedin_job_id, title, company_id, now, now),
    )
    return cur.lastrowid


def insert_application(conn, job_id, status="saved"):
    now = db.utcnow()
    cur = conn.execute(
        """
        INSERT INTO applications (job_id, status, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (job_id, status, now, now),
    )
    return cur.lastrowid


def insert_salary(conn, job_id, basis, currency="EUR", period="year",
                   min_amount=70000, max_amount=90000, confidence="high",
                   captured_at=None):
    conn.execute(
        """
        INSERT INTO salary_estimates
            (job_id, basis, currency, period, min_amount, max_amount, confidence, captured_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, basis, currency, period, min_amount, max_amount, confidence,
         captured_at or db.utcnow()),
    )


def test_all_tables_exist_with_expected_columns(conn):
    for table, expected_columns in EXPECTED_TABLES.items():
        actual = _columns(conn, table)
        assert actual == expected_columns, f"{table}: {actual} != {expected_columns}"


def test_all_views_exist(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'view'"
    ).fetchall()
    names = {row["name"] for row in rows}
    assert EXPECTED_VIEWS.issubset(names)


def test_init_is_idempotent(tmp_path):
    path = tmp_path / "tracker.db"
    conn1 = db.connect(path)
    db.migrate(conn1)
    conn1.close()

    conn2 = db.connect(path)
    db.migrate(conn2)  # must not raise on a second run
    versions = [row[0] for row in conn2.execute("SELECT version FROM schema_migrations")]
    conn2.close()
    assert versions == [1]


def test_applications_status_check_rejects_bogus_value(conn):
    company_id = insert_company(conn)
    job_id = insert_job(conn, company_id)
    with pytest.raises(sqlite3.IntegrityError):
        insert_application(conn, job_id, status="not_a_real_status")


def test_deleting_a_job_cascades_to_application_and_events(conn):
    company_id = insert_company(conn)
    job_id = insert_job(conn, company_id)
    application_id = insert_application(conn, job_id, status="applied")
    conn.execute(
        """
        INSERT INTO application_events (application_id, event_type, occurs_at, created_at)
        VALUES (?, 'applied', ?, ?)
        """,
        (application_id, db.utcnow(), db.utcnow()),
    )
    conn.commit()

    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()

    assert conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone() is None
    assert conn.execute(
        "SELECT * FROM application_events WHERE application_id = ?", (application_id,)
    ).fetchone() is None


def test_deleting_a_job_without_foreign_keys_on_would_not_cascade(tmp_path):
    # Guards against a connect() that forgets PRAGMA foreign_keys = ON: this
    # test goes through db.connect() itself, not a manually configured
    # connection, so a regression there is caught here.
    conn = db.connect(tmp_path / "fk.db")
    db.migrate(conn)
    company_id = insert_company(conn)
    job_id = insert_job(conn, company_id)
    insert_application(conn, job_id, status="applied")
    conn.commit()

    fk_status = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk_status == 1
    conn.close()


def test_rating_with_null_sub_ratings_succeeds(conn):
    company_id = insert_company(conn)
    conn.execute(
        """
        INSERT INTO glassdoor_ratings (company_id, overall, match_confidence, captured_at)
        VALUES (?, 3.9, 'high', ?)
        """,
        (company_id, db.utcnow()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM glassdoor_ratings WHERE company_id = ?", (company_id,)
    ).fetchone()
    assert row["overall"] == 3.9
    assert row["work_life_balance"] is None
    assert row["compensation_benefits"] is None


def test_v_dashboard_returns_row_with_nulls_for_untouched_job(conn):
    company_id = insert_company(conn)
    job_id = insert_job(conn, company_id, linkedin_job_id="lonely-job")
    conn.commit()

    row = conn.execute("SELECT * FROM v_dashboard WHERE job_id = ?", (job_id,)).fetchone()
    assert row is not None
    assert row["company_name"] == "Acme Corp"
    assert row["glassdoor_overall"] is None
    assert row["status"] is None
    assert row["salary_basis"] is None
    assert row["cv_version"] is None
    assert row["has_job_description"] == 0


def test_v_latest_salary_prefers_posting_over_newer_estimated(conn):
    company_id = insert_company(conn)
    job_id = insert_job(conn, company_id)
    insert_salary(conn, job_id, basis="posting", min_amount=80000, max_amount=80000,
                  captured_at="2026-01-01T00:00:00Z")
    insert_salary(conn, job_id, basis="estimated", min_amount=95000, max_amount=95000,
                  confidence="medium", captured_at="2026-09-01T00:00:00Z")
    conn.commit()

    row = conn.execute("SELECT * FROM v_latest_salary WHERE job_id = ?", (job_id,)).fetchone()
    assert row["basis"] == "posting"
    assert row["min_amount"] == 80000

    dashboard_row = conn.execute(
        "SELECT * FROM v_dashboard WHERE job_id = ?", (job_id,)
    ).fetchone()
    assert dashboard_row["salary_basis"] == "posting"
    assert dashboard_row["salary_min_amount"] == 80000


def test_cv_variants_unique_job_id_version(conn):
    company_id = insert_company(conn)
    job_id = insert_job(conn, company_id)
    now = db.utcnow()
    conn.execute(
        """
        INSERT INTO cv_variants (job_id, version, status, master_hash, content_json, created_at)
        VALUES (?, 1, 'draft', 'sha256:abc', '{}', ?)
        """,
        (job_id, now),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO cv_variants (job_id, version, status, master_hash, content_json, created_at)
            VALUES (?, 1, 'draft', 'sha256:def', '{}', ?)
            """,
            (job_id, now),
        )
