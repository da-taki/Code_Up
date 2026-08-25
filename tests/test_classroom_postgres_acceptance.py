"""Real-PostgreSQL classroom workflow acceptance test (spec section 14).

Runs the full instructor/learner workflow end-to-end directly against a
real PostgreSQL database (not SQLite, not a mock) when
CLASSROOM_TEST_DATABASE_URL is set to a reachable, disposable Postgres
connection string:

    CLASSROOM_TEST_DATABASE_URL=postgresql://user:pass@host/db \\
        pytest tests/test_classroom_postgres_acceptance.py -v

SKIPPED (not failed) when that variable is absent - which is the normal
case in this environment (no local Postgres server, no Docker). A skip
here means real-Postgres storage behavior has NOT been verified; see
docs/CLASSROOM_PRODUCTION_STORAGE.md and the persistence-pass report for
exactly what that leaves unconfirmed.
"""

from __future__ import annotations

import os

import pytest

from codeup.classroom import _storage, db, reports

REAL_PG_URL = os.environ.get("CLASSROOM_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not REAL_PG_URL, reason="CLASSROOM_TEST_DATABASE_URL not set - real Postgres workflow unverified"
)


@pytest.fixture(autouse=True)
def _use_real_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", REAL_PG_URL)
    _storage._pool = None
    _storage._pool_url = None
    _storage._schema_ready_for = None
    yield
    if _storage._pool is not None:
        _storage._pool.close()
    _storage._pool = None
    _storage._pool_url = None
    _storage._schema_ready_for = None


def _reconnect():
    """Simulate an app restart/new worker: drop the pool and force schema
    re-verification on the next call, without losing any data (schema
    version table + real rows live in Postgres, not in process memory)."""
    if _storage._pool is not None:
        _storage._pool.close()
    _storage._pool = None
    _storage._pool_url = None
    _storage._schema_ready_for = None


def test_full_classroom_workflow_against_real_postgres():
    # 1. initialize blank DB (schema created lazily on first connect)
    assert _storage.backend_name() == "postgres"
    version_before = _storage.schema_version()
    assert version_before is not None and version_before >= 1

    # 2. create instructor
    instructor = db.create_instructor("pg_accept_teacher", "hashed-pw", "Ms Accept")
    assert instructor["id"]

    # 3. create cohort
    cohort = db.create_cohort(instructor["id"], "Acceptance Cohort")
    assert cohort["join_code"]

    # 4. join at least 2 learners
    learner_a = db.join_cohort(cohort["id"], "Learner A")
    learner_b = db.join_cohort(cohort["id"], "Learner B")
    assert learner_a["id"] != learner_b["id"]
    assert db.get_cohort_by_join_code(cohort["join_code"])["id"] == cohort["id"]

    # 5. create + publish assignment
    assignment = db.create_assignment(
        cohort["id"], "PG Assignment", "instructions", "x = 1",
        None, ["variables"], "FULL",
    )
    published = db.publish_assignment(assignment["id"])
    assert published["status"] == "published"

    # 6. autosave learner code
    db.get_or_create_progress(assignment["id"], learner_a["id"])
    saved = db.save_progress_code(assignment["id"], learner_a["id"], "x = 2")
    assert saved["status"] == "in_progress"

    # 7. run/update progress
    ran = db.save_progress_code(
        assignment["id"], learner_a["id"], "x = 2\nprint(x)", ran=True, run_ok=True,
    )
    assert ran["run_count"] == 1
    assert ran["success_run_count"] == 1

    # 8. submit
    submitted = db.submit_assignment(assignment["id"], learner_b["id"], "x = 3\nprint(x)")
    assert submitted["status"] == "submitted"

    # 9. help request
    hr = db.create_help_request(cohort["id"], learner_a["id"], assignment["id"], "stuck")
    assert hr["status"] == "open"
    db.mark_help_request_helping(hr["id"], "on it")
    resolved = db.resolve_help_request(hr["id"], "fixed")
    assert resolved["status"] == "resolved"

    # 10. curriculum progress
    db.set_curriculum_position(learner_a["id"], "loops_module", "attempt")
    db.upsert_module_stage(learner_a["id"], "loops_module", "example")
    db.record_quiz_result(learner_a["id"], "loops_module", 3, 5)
    state = db.get_curriculum_state(learner_a["id"])
    assert state["current_module_id"] == "loops_module"

    # 11. custom lesson
    lesson = db.create_custom_lesson(
        cohort["id"], instructor["id"], title="Custom Lesson", objective="obj",
        explanation="exp", starter_code="", instructions="do it",
        expected_concepts=["loops"], challenge="challenge",
    )
    assert db.get_custom_lesson(lesson["id"])["title"] == "Custom Lesson"

    # 12. custom project/checkpoints
    project = db.create_custom_project(
        cohort["id"], instructor["id"], title="Custom Project", instructions="build",
        starter_code="", expected_concepts=["functions"],
        checkpoints=[{"label": "Step 1", "check_type": "contains_print", "check_config": {}}],
    )
    fetched_project = db.get_custom_project(project["id"])
    assert len(fetched_project["checkpoints"]) == 1
    pid = f"custom:{project['id']}"
    db.get_or_create_project_progress(learner_a["id"], pid)
    db.save_project_progress(learner_a["id"], pid, code="print('ok')", checkpoints_completed=["Step 1"])

    # 13. instructor live-summary/event reads
    db.log_event(learner_a["id"], cohort["id"], "run_success", {"n": 1})
    events = db.list_events_for_cohort(cohort["id"], limit=50)
    assert any(e["kind"] == "run_success" for e in events)

    # 14. report/stat queries
    report = reports.build_learner_report(learner_a["id"])
    assert report is not None
    cohort_report = reports.build_cohort_report(cohort["id"])
    assert any(r["learner_id"] == learner_a["id"] for r in cohort_report["rows"])

    # 15. restart/reconnect (drop pool, force schema re-check on next use)
    _reconnect()

    # 16. verify all data still exists
    assert db.get_instructor(instructor["id"])["username"] == "pg_accept_teacher"
    assert db.get_cohort(cohort["id"])["name"] == "Acceptance Cohort"
    assert {row["id"] for row in db.list_learners_for_cohort(cohort["id"])} == {
        learner_a["id"], learner_b["id"],
    }
    assert db.get_assignment(assignment["id"])["status"] == "published"
    progress_rows = db.list_progress_for_assignment(assignment["id"])
    assert {r["learner_id"] for r in progress_rows} == {learner_a["id"], learner_b["id"]}
    assert db.get_help_request(hr["id"])["status"] == "resolved"
    assert db.get_curriculum_state(learner_a["id"])["current_module_id"] == "loops_module"
    assert db.get_custom_lesson(lesson["id"]) is not None
    assert db.get_custom_project(project["id"]) is not None
    assert db.list_project_progress(learner_a["id"])
    assert _storage.schema_version() == version_before


def test_two_learners_join_at_roughly_the_same_time():
    import threading

    instructor = db.create_instructor("pg_race_teacher", "hashed-pw", "T")
    cohort = db.create_cohort(instructor["id"], "Race Cohort")
    results, errors = [], []

    def _join(i):
        try:
            results.append(db.join_cohort(cohort["id"], f"Racer {i}"))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=_join, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len({r["id"] for r in results}) == 6


def test_join_code_collision_retries_instead_of_500(monkeypatch):
    instructor = db.create_instructor("pg_collide_teacher", "hashed-pw", "T")
    taken = db.create_cohort(instructor["id"], "Existing")
    codes = iter([taken["join_code"], "FRESH2"])
    monkeypatch.setattr(db, "new_join_code", lambda conn, length=6: next(codes))
    new_cohort = db.create_cohort(instructor["id"], "New")
    assert new_cohort["join_code"] == "FRESH2"


def test_duplicate_progress_initialization_does_not_error():
    import threading

    instructor = db.create_instructor("pg_dup_teacher", "hashed-pw", "T")
    cohort = db.create_cohort(instructor["id"], "Dup Cohort")
    learner = db.join_cohort(cohort["id"], "Dup Learner")
    assignment = db.create_assignment(
        cohort["id"], "Dup Assignment", "i", "code", None, [], "FULL",
    )
    results, errors = [], []

    def _get_or_create():
        try:
            results.append(db.get_or_create_progress(assignment["id"], learner["id"]))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=_get_or_create) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len({r["id"] for r in results}) == 1
