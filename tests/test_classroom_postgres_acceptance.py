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

from codeup.classroom import _storage, db, learner_actions, reports
from tests._postgres_test_support import reset_postgres_classroom_data

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
    # This module shares one real, long-lived database with every other
    # CLASSROOM_TEST_DATABASE_URL-gated test file/run. Reset the data
    # tables (never classroom_schema_version) before each test so IDs
    # start from 1 and no test can see another test's rows, regardless of
    # run order.
    reset_postgres_classroom_data(REAL_PG_URL)
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
    # 1. create instructor
    assert _storage.backend_name() == "postgres"
    version_before = _storage.schema_version()
    assert version_before is not None and version_before >= 1
    instructor = db.create_instructor("pg_accept_teacher", "hashed-pw", "Ms Accept")
    assert instructor["id"]

    # 2. create cohort
    cohort = db.create_cohort(instructor["id"], "Acceptance Cohort")

    # 3. generate join code
    assert cohort["join_code"]
    assert db.get_cohort_by_join_code(cohort["join_code"])["id"] == cohort["id"]

    # 4 + 5. join learner A, join learner B - through learner_actions, the
    # exact function the real join route/IDE command calls, so the
    # "learner_joined" activity event (item 17) is exercised for real.
    join_a = learner_actions.join_cohort_by_code(cohort["join_code"], "Learner A")
    join_b = learner_actions.join_cohort_by_code(cohort["join_code"], "Learner B")
    assert join_a["success"] and join_b["success"]
    learner_a, learner_b = join_a["learner"], join_b["learner"]
    assert learner_a["id"] != learner_b["id"]

    # 6. create assignment
    assignment = db.create_assignment(
        cohort["id"], "PG Assignment", "instructions", "x = 1",
        None, ["variables"], "FULL",
    )

    # 7. configure assignment AI permissions
    policy_updated = db.update_assignment_policy(assignment["id"], "EXPLANATIONS_ONLY")
    assert policy_updated["ai_policy"] == "EXPLANATIONS_ONLY"
    settings_updated = db.update_assignment_settings(assignment["id"], {"cap_hints": True, "cap_fix": False})
    assert settings_updated["capability_settings"] == {"cap_hints": True, "cap_fix": False}
    # restore a permissive policy so the rest of the workflow isn't gated
    db.update_assignment_policy(assignment["id"], "FULL")

    # 8. publish assignment
    published = db.publish_assignment(assignment["id"])
    assert published["status"] == "published"

    # 9. open assignment for learner
    opened = db.get_or_create_progress(assignment["id"], learner_a["id"])
    assert opened["status"] == "not_started"

    # 10. autosave code
    saved = db.save_progress_code(assignment["id"], learner_a["id"], "x = 2")
    assert saved["status"] == "in_progress"

    # 11. record a run
    ran = db.save_progress_code(
        assignment["id"], learner_a["id"], "x = 2\nprint(1/0)", ran=True, run_ok=False, error="ZeroDivisionError",
    )
    assert ran["run_count"] == 1
    assert ran["last_error"] == "ZeroDivisionError"

    # 12. record a successful run
    ran_ok = db.save_progress_code(
        assignment["id"], learner_a["id"], "x = 2\nprint(x)", ran=True, run_ok=True,
    )
    assert ran_ok["run_count"] == 2
    assert ran_ok["success_run_count"] == 1
    assert ran_ok["last_error"] is None

    # 13. update progress (already covered by the autosave/run calls above -
    # re-fetch to confirm the latest state persisted)
    current = db.get_progress(assignment["id"], learner_a["id"])
    assert current["code"] == "x = 2\nprint(x)"

    # 14. submit assignment - through learner_actions (item 18: submission event)
    submitted = learner_actions.submit_current_assignment(learner_b, assignment, "x = 3\nprint(x)")
    assert submitted["status"] == "submitted"

    # 15. create help request - through learner_actions (help-request state)
    hr = learner_actions.send_help_request(learner_a, "stuck on loops", assignment["id"])
    assert hr["status"] == "open"

    # 16. read instructor live-summary
    live_events = db.list_events_for_cohort(cohort["id"], limit=50)
    assert live_events  # at least the events logged above are visible

    # 17. verify learner join event
    join_events = db.list_events_for_learner(learner_a["id"], limit=50)
    assert any(e["kind"] == "learner_joined" for e in join_events)

    # 18. verify submission event
    submit_events = db.list_events_for_learner(learner_b["id"], limit=50)
    assert any(e["kind"] == "assignment_submitted" for e in submit_events)

    # 19. verify help-request state
    assert db.get_help_request(hr["id"])["status"] == "open"

    # 20. mark help request as helping
    db.mark_help_request_helping(hr["id"], "on it")
    assert db.get_help_request(hr["id"])["status"] == "helping"

    # 21. resolve help request
    resolved = db.resolve_help_request(hr["id"], "fixed")
    assert resolved["status"] == "resolved"

    # 22. update curriculum progress
    db.set_curriculum_position(learner_a["id"], "loops_module", "attempt")
    db.upsert_module_stage(learner_a["id"], "loops_module", "example")
    state = db.get_curriculum_state(learner_a["id"])
    assert state["current_module_id"] == "loops_module"

    # 23. update module progress
    module_row = db.record_quiz_result(learner_a["id"], "loops_module", 3, 5)
    assert module_row["quiz_score"] == 3 and module_row["quiz_total"] == 5

    # 24. update concept progress
    concept_state = db.set_concept_state(learner_a["id"], "loops", "practised")
    assert concept_state["state"] == "practised"
    assert db.get_concept_progress(learner_a["id"])["loops"]["state"] == "practised"

    # 25. create custom lesson
    lesson = db.create_custom_lesson(
        cohort["id"], instructor["id"], title="Custom Lesson", objective="obj",
        explanation="exp", starter_code="", instructions="do it",
        expected_concepts=["loops"], challenge="challenge",
    )
    assert db.get_custom_lesson(lesson["id"])["title"] == "Custom Lesson"

    # 26 + 27. create custom guided project + project checkpoints
    project = db.create_custom_project(
        cohort["id"], instructor["id"], title="Custom Project", instructions="build",
        starter_code="", expected_concepts=["functions"],
        checkpoints=[{"label": "Step 1", "check_type": "contains_print", "check_config": {}}],
    )
    fetched_project = db.get_custom_project(project["id"])
    assert len(fetched_project["checkpoints"]) == 1

    # 28. update guided-project progress
    pid = f"custom:{project['id']}"
    db.get_or_create_project_progress(learner_a["id"], pid)
    project_progress = db.save_project_progress(
        learner_a["id"], pid, code="print('ok')", checkpoints_completed=["Step 1"],
    )
    assert project_progress["checkpoints_completed"] == ["Step 1"]

    # 29. generate/read learner report
    report = reports.build_learner_report(learner_a["id"])
    assert report is not None

    # 30. read instructor/cohort statistics
    cohort_report = reports.build_cohort_report(cohort["id"])
    assert any(r["learner_id"] == learner_a["id"] for r in cohort_report["rows"])
    assert any(r["learner_id"] == learner_b["id"] for r in cohort_report["rows"])

    # Mandatory reconnect test: close every connection, force a fresh
    # process-level pool/schema check, and verify everything survived.
    _reconnect()

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
    assert db.get_concept_progress(learner_a["id"])["loops"]["state"] == "practised"
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

    threads = [threading.Thread(target=_join, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len({r["id"] for r in results}) == 8
    assert len({r["token"] for r in results}) == 8
    assert len(db.list_learners_for_cohort(cohort["id"])) == 8


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

    threads = [threading.Thread(target=_get_or_create) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(results) == 8
    assert len({r["id"] for r in results}) == 1
    assert len(db.list_progress_for_assignment(assignment["id"])) == 1


def test_instructor_read_while_learner_submits():
    """Concurrency scenario C: an instructor's live-summary read and a
    learner's submission happen at roughly the same time. Neither should
    error, and the submission must become visible to a read that starts
    after it completes."""
    import threading

    instructor = db.create_instructor("pg_readwrite_teacher", "hashed-pw", "T")
    cohort = db.create_cohort(instructor["id"], "ReadWrite Cohort")
    learner = db.join_cohort(cohort["id"], "Submitter")
    assignment = db.create_assignment(
        cohort["id"], "ReadWrite Assignment", "i", "code", None, [], "FULL",
    )
    db.publish_assignment(assignment["id"])

    read_errors, write_errors = [], []
    stop = threading.Event()

    def _poll_live_summary():
        while not stop.is_set():
            try:
                db.list_progress_for_assignment(assignment["id"])
                db.list_learners_for_cohort(cohort["id"])
            except Exception as exc:  # pragma: no cover
                read_errors.append(exc)

    reader = threading.Thread(target=_poll_live_summary)
    reader.start()
    try:
        for _ in range(10):
            try:
                db.submit_assignment(assignment["id"], learner["id"], "print('final')")
            except Exception as exc:  # pragma: no cover
                write_errors.append(exc)
    finally:
        stop.set()
        reader.join()

    assert not read_errors
    assert not write_errors
    final = db.get_progress(assignment["id"], learner["id"])
    assert final["status"] == "submitted"
    assert final["submitted_code"] == "print('final')"


def test_help_request_visible_while_instructor_polling():
    """Concurrency scenario D: a help request created while an instructor
    is repeatedly polling the open-help-requests list must become visible
    without errors or a torn read."""
    import threading

    instructor = db.create_instructor("pg_help_poll_teacher", "hashed-pw", "T")
    cohort = db.create_cohort(instructor["id"], "Help Poll Cohort")
    learner = db.join_cohort(cohort["id"], "Help Asker")

    poll_errors = []
    seen_it = threading.Event()
    stop = threading.Event()

    def _poll():
        while not stop.is_set():
            try:
                open_requests = db.list_help_requests(cohort["id"], status="open")
                if any(r["learner_id"] == learner["id"] for r in open_requests):
                    seen_it.set()
            except Exception as exc:  # pragma: no cover
                poll_errors.append(exc)

    poller = threading.Thread(target=_poll)
    poller.start()
    try:
        hr = db.create_help_request(cohort["id"], learner["id"], None, "need help")
        seen_it.wait(timeout=5)
    finally:
        stop.set()
        poller.join()

    assert not poll_errors
    assert seen_it.is_set()
    assert db.get_help_request(hr["id"])["status"] == "open"


def test_two_autosaves_same_learner_assignment_last_write_wins():
    """Concurrency scenario E: confirms the existing (pre-Postgres,
    unchanged) last-write-wins autosave behavior - not a redesign, just a
    check that Postgres doesn't change this semantic."""
    import threading

    instructor = db.create_instructor("pg_autosave_teacher", "hashed-pw", "T")
    cohort = db.create_cohort(instructor["id"], "Autosave Cohort")
    learner = db.join_cohort(cohort["id"], "Autosaver")
    assignment = db.create_assignment(
        cohort["id"], "Autosave Assignment", "i", "", None, [], "FULL",
    )
    db.get_or_create_progress(assignment["id"], learner["id"])

    errors = []

    def _save(code):
        try:
            db.save_progress_code(assignment["id"], learner["id"], code)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    t1 = threading.Thread(target=_save, args=("code from tab 1",))
    t2 = threading.Thread(target=_save, args=("code from tab 2",))
    t1.start()
    t1.join()
    t2.start()
    t2.join()

    assert not errors
    final = db.get_progress(assignment["id"], learner["id"])
    assert final["code"] == "code from tab 2"  # the later write wins, as before
