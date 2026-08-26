"""Tests for scripts/migrate_classroom_sqlite_to_postgres.py.

Two tiers:

1. Dry-run tests against a synthetic SQLite classroom.db - no network, no
   Postgres, always run.
2. A real-Postgres migration acceptance test (row counts, explicit IDs,
   relationships, password_hash/token/JSON preserved exactly, sequences
   reset, new post-migration records don't collide) - only runs when
   CLASSROOM_TEST_DATABASE_URL points at a real, reachable, disposable
   PostgreSQL database. It is SKIPPED (not failed) otherwise, and that skip
   is exactly the "real Postgres migration remains unverified" gap called
   out in docs/CLASSROOM_PRODUCTION_STORAGE.md and the persistence-pass
   report - this file does not, by itself, prove the migration tool works
   against real Postgres.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402

from codeup.classroom import _storage, db  # noqa: E402
from scripts import migrate_classroom_sqlite_to_postgres as migrate_tool  # noqa: E402
from tests._postgres_test_support import (  # noqa: E402
    reset_postgres_classroom_data,
    reset_postgres_schema_completely,
)

REAL_PG_URL = os.environ.get("CLASSROOM_TEST_DATABASE_URL", "").strip()


def _seed_representative_data() -> dict:
    """Populate the (SQLite, since DATABASE_URL is unset in tests)
    classroom.db with at least one row in every table, entirely through
    the public db.py API - the same calls the real app makes."""
    instructor = db.create_instructor("migrate_teacher", "pbkdf2:sha256:fake-hash", "Ms Rao")
    cohort = db.create_cohort(instructor["id"], "Migration Cohort")
    learner_a = db.join_cohort(cohort["id"], "Amir")
    learner_b = db.join_cohort(cohort["id"], "Priya")

    assignment = db.create_assignment(
        cohort["id"], "Student Marks", "Build it", "marks = {}",
        None, ["dictionaries"], "EXPLANATIONS_ONLY",
    )
    db.publish_assignment(assignment["id"])
    db.get_or_create_progress(assignment["id"], learner_a["id"])
    db.save_progress_code(assignment["id"], learner_a["id"], "marks = {'A': 1}", ran=True, run_ok=True)
    db.submit_assignment(assignment["id"], learner_b["id"], "marks = {'B': 2}")

    hr = db.create_help_request(cohort["id"], learner_a["id"], assignment["id"], "stuck on loops")
    db.resolve_help_request(hr["id"], "explained loops")

    db.set_curriculum_position(learner_a["id"], "loops_module", "attempt")
    db.upsert_module_stage(learner_a["id"], "loops_module", "example")
    db.record_quiz_result(learner_a["id"], "loops_module", 4, 5)

    lesson = db.create_custom_lesson(
        cohort["id"], instructor["id"], title="Custom Lesson", objective="obj",
        explanation="exp", starter_code="x = 1", instructions="do it",
        expected_concepts=["variables"], challenge="challenge text",
        quiz_choices=["a", "b"], quiz_answer_index=0,
    )
    project = db.create_custom_project(
        cohort["id"], instructor["id"], title="Custom Project", instructions="build",
        starter_code="", expected_concepts=["functions"],
        checkpoints=[{"label": "Step 1", "check_type": "contains_print", "check_config": {"x": 1}}],
    )
    db.get_or_create_project_progress(learner_a["id"], f"custom:{project['id']}")
    db.save_project_progress(
        learner_a["id"], f"custom:{project['id']}", code="print('hi')",
        checkpoints_completed=["Step 1"],
    )

    db.upsert_lesson_progress(learner_a["id"], "builtin_lesson_1", "completed", last_code="print(1)")
    db.set_concept_state(learner_a["id"], "loops", "practised")
    db.log_event(learner_a["id"], cohort["id"], "run_success", {"detail": "ok"})
    db.mark_onboarding_completed(learner_a["id"])
    db.mark_assignments_seen(learner_a["id"])
    db.mark_ide_orientation_shown(learner_a["id"])

    return {
        "instructor": instructor,
        "cohort": cohort,
        "learner_a": learner_a,
        "learner_b": learner_b,
        "assignment": assignment,
        "lesson": lesson,
        "project": project,
    }


def _classroom_db_path() -> str:
    return _storage._sqlite_db_path()


# ---- dry-run (no network) --------------------------------------------------------

def test_dry_run_reports_source_counts_and_touches_nothing(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    seeded = _seed_representative_data()
    sqlite_path = _classroom_db_path()

    buf = io.StringIO()
    with redirect_stdout(buf):
        counts = migrate_tool.migrate(
            sqlite_path, "postgresql://user:pass@127.0.0.1:1/does-not-matter", dry_run=True
        )
    output = buf.getvalue()

    assert counts["instructors"] == 1
    assert counts["cohorts"] == 1
    assert counts["learners"] == 2
    assert counts["assignments"] == 1
    assert counts["assignment_progress"] == 2
    assert counts["custom_lessons"] == 1
    assert counts["custom_projects"] == 1
    assert counts["custom_project_checkpoints"] == 1
    assert counts["onboarding_progress"] == 1
    assert counts["learner_notification_state"] == 1

    # Never prints names, tokens, password hashes, or the connection string.
    assert seeded["instructor"]["password_hash"] not in output
    assert seeded["learner_a"]["token"] not in output
    assert seeded["learner_a"]["display_name"] not in output
    assert "user:pass" not in output
    assert "Dry run only" in output


def test_dry_run_refuses_missing_source():
    with pytest.raises(migrate_tool.MigrationError):
        migrate_tool.migrate("/nonexistent/classroom.db", "postgresql://x/y", dry_run=True)


def test_resolve_destination_refuses_missing_or_non_postgres_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(migrate_tool.MigrationError):
        migrate_tool._resolve_destination_url(None)
    with pytest.raises(migrate_tool.MigrationError):
        migrate_tool._resolve_destination_url("mysql://user:pass@host/db")
    assert migrate_tool._resolve_destination_url("postgresql://user:pass@host/db")


# ---- real Postgres acceptance test (section 15) ----------------------------------

@pytest.mark.skipif(not REAL_PG_URL, reason="CLASSROOM_TEST_DATABASE_URL not set - real Postgres migration unverified")
def test_full_migration_preserves_everything_and_new_ids_dont_collide(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # This migration inserts explicit historical IDs (id=1 for the first
    # instructor, etc.), so the destination must genuinely be empty - not
    # just schema-initialized - or it collides with rows real usage from
    # another test/run left behind on this shared long-lived database.
    reset_postgres_classroom_data(REAL_PG_URL)
    seeded = _seed_representative_data()
    sqlite_path = _classroom_db_path()

    migrate_tool.migrate(sqlite_path, REAL_PG_URL, dry_run=False)

    monkeypatch.setenv("DATABASE_URL", REAL_PG_URL)
    _storage._pool = None
    _storage._pool_url = None
    _storage._schema_ready_for = None

    migrated_instructor = db.get_instructor(seeded["instructor"]["id"])
    assert migrated_instructor["password_hash"] == seeded["instructor"]["password_hash"]
    assert migrated_instructor["username"] == seeded["instructor"]["username"]

    migrated_learner = db.get_learner(seeded["learner_a"]["id"])
    assert migrated_learner["token"] == seeded["learner_a"]["token"]

    migrated_assignment = db.get_assignment(seeded["assignment"]["id"])
    assert migrated_assignment["expected_concepts"] == ["dictionaries"]

    progress_rows = db.list_progress_for_assignment(seeded["assignment"]["id"])
    assert {r["learner_id"] for r in progress_rows} == {
        seeded["learner_a"]["id"], seeded["learner_b"]["id"],
    }

    project = db.get_custom_project(seeded["project"]["id"])
    assert len(project["checkpoints"]) == 1
    assert project["checkpoints"][0]["check_config"] == {"x": 1}

    # New records after migration must not collide with migrated IDs.
    new_instructor = db.create_instructor("post_migration_teacher", "hash2", "New Teacher")
    assert new_instructor["id"] > seeded["instructor"]["id"]
    new_cohort = db.create_cohort(new_instructor["id"], "Post-migration Cohort")
    assert new_cohort["id"] > seeded["cohort"]["id"]


@pytest.mark.skipif(not REAL_PG_URL, reason="CLASSROOM_TEST_DATABASE_URL not set - real Postgres rollback unverified")
def test_migration_rolls_back_completely_on_a_forced_mid_copy_failure(monkeypatch):
    """Forces a real failure partway through the copy (by dropping one
    destination table after the schema has already been created, so the
    later INSERT into it fails with UndefinedTable) and verifies the
    whole PostgreSQL transaction rolled back - not just the failing
    table - and that the source SQLite file was never touched."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_postgres_classroom_data(REAL_PG_URL)
    _storage.ensure_schema(REAL_PG_URL)  # make sure the schema exists before we damage it

    with psycopg.connect(REAL_PG_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE progress_events CASCADE")

    _seed_representative_data()
    sqlite_path = _classroom_db_path()
    with open(sqlite_path, "rb") as f:
        source_bytes_before = f.read()

    try:
        with pytest.raises(Exception):
            migrate_tool.migrate(sqlite_path, REAL_PG_URL, dry_run=False)

        # Rollback proof: tables that copied successfully BEFORE the
        # failing one must have been rolled back too - not left populated.
        with psycopg.connect(REAL_PG_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                for table in ("instructors", "cohorts", "learners", "assignments"):
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    assert cur.fetchone()[0] == 0, f"{table} was not rolled back"

        # Source SQLite file must be byte-for-byte untouched.
        with open(sqlite_path, "rb") as f:
            assert f.read() == source_bytes_before
    finally:
        # Restore a complete, working schema for any test that runs after
        # this one in the same session.
        reset_postgres_schema_completely(REAL_PG_URL)
        _storage._schema_ready_for = None
        _storage.ensure_schema(REAL_PG_URL)
