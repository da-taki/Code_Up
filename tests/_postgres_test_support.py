"""Shared helper for tests that run against a real, disposable PostgreSQL
database (CLASSROOM_TEST_DATABASE_URL). Not collected as a test module
itself (no test_ prefix).

These tests share one long-lived Neon database across test functions and
files. Real usage during earlier tests in the same run leaves real rows
behind (auto-incrementing IDs keep climbing), which is exactly what a
migration test that inserts explicit historical IDs cannot tolerate - it
needs the destination genuinely empty, as spec section 9 requires
("migrate into a freshly reset Neon database"). This resets only the 17
classroom data tables (never classroom_schema_version, so the schema
migration doesn't have to re-run for every test) so each test that needs
a clean slate can ask for one regardless of what ran before it.
"""

from __future__ import annotations

import psycopg

CLASSROOM_DATA_TABLES = (
    "instructors", "cohorts", "learners", "assignments", "assignment_progress",
    "help_requests", "curriculum_progress", "module_progress", "custom_lessons",
    "custom_projects", "custom_project_checkpoints", "onboarding_progress",
    "learner_notification_state", "lesson_progress", "project_progress",
    "concept_progress", "progress_events",
)


def reset_postgres_classroom_data(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)",
                (list(CLASSROOM_DATA_TABLES),),
            )
            existing = {row[0] for row in cur.fetchall()}
            if not existing:
                return  # nothing created yet - schema init will handle it
            table_list = ", ".join(sorted(existing))
            cur.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")


def reset_postgres_schema_completely(url: str) -> None:
    """Drops every classroom table INCLUDING classroom_schema_version, so
    the next _storage.connect()/ensure_schema() call re-creates the whole
    schema from scratch. Used by tests that deliberately damage the
    schema (e.g. dropping one table to force a migration failure) and
    need a guaranteed-clean rebuild afterward, since the normal
    version-gated migration path won't recreate a table that already
    "should" exist at the current recorded version."""
    all_tables = list(CLASSROOM_DATA_TABLES) + ["classroom_schema_version"]
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            for table in all_tables:
                cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
