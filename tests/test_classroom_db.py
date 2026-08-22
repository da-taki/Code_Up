from codeup.classroom import db


def test_create_instructor_and_lookup():
    instructor = db.create_instructor("teach1", "hashed", "Ms Rao")
    assert instructor["id"]
    assert db.get_instructor_by_username("teach1")["id"] == instructor["id"]
    assert db.get_instructor_by_username("nope") is None


def test_cohort_lifecycle_and_join_code_unique():
    instructor = db.create_instructor("teach2", "hashed", "Mr Lee")
    cohort_a = db.create_cohort(instructor["id"], "Python Beginners")
    cohort_b = db.create_cohort(instructor["id"], "Python Advanced")
    assert cohort_a["join_code"] != cohort_b["join_code"]
    assert len(cohort_a["join_code"]) == 6

    found = db.get_cohort_by_join_code(cohort_a["join_code"])
    assert found["id"] == cohort_a["id"]

    renamed = db.rename_cohort(cohort_a["id"], instructor["id"], "Python Beginners 2026")
    assert renamed["name"] == "Python Beginners 2026"

    archived = db.set_cohort_status(cohort_a["id"], instructor["id"], "archived")
    assert archived["status"] == "archived"
    assert db.get_cohort_by_join_code(cohort_a["join_code"]) is None  # only active cohorts join-able

    cohorts = db.list_cohorts_for_instructor(instructor["id"])
    assert {c["id"] for c in cohorts} == {cohort_a["id"], cohort_b["id"]}


def test_learner_join_and_membership_persists():
    instructor = db.create_instructor("teach3", "hashed", "Ms Rao")
    cohort = db.create_cohort(instructor["id"], "Python Beginners")
    learner = db.join_cohort(cohort["id"], "Amir")
    assert learner["cohort_id"] == cohort["id"]
    assert learner["token"]

    by_token = db.get_learner_by_token(learner["token"])
    assert by_token["id"] == learner["id"]
    assert db.get_learner_by_token("bogus-token") is None

    members = db.list_learners_for_cohort(cohort["id"])
    assert len(members) == 1
    assert members[0]["display_name"] == "Amir"


def test_assignment_progress_status_transitions():
    instructor = db.create_instructor("teach4", "hashed", "Ms Rao")
    cohort = db.create_cohort(instructor["id"], "Python Beginners")
    learner = db.join_cohort(cohort["id"], "Priya")
    assignment = db.create_assignment(
        cohort["id"], "Student Marks Program", "Build it", "marks = {}",
        None, ["dictionaries", "variables"], "EXPLANATIONS_ONLY",
    )
    assert assignment["expected_concepts"] == ["dictionaries", "variables"]

    progress = db.get_or_create_progress(assignment["id"], learner["id"])
    assert progress["status"] == "not_started"
    assert progress["code"] == "marks = {}"  # seeded from starter code

    progress = db.save_progress_code(assignment["id"], learner["id"], "marks = {'A': 1}")
    assert progress["status"] == "in_progress"

    progress = db.save_progress_code(
        assignment["id"], learner["id"], "marks = {'A': 1}\nprint(1/0)",
        ran=True, run_ok=False, error="ZeroDivisionError",
    )
    assert progress["run_count"] == 1
    assert progress["success_run_count"] == 0
    assert progress["last_error"] == "ZeroDivisionError"

    progress = db.save_progress_code(
        assignment["id"], learner["id"], "marks = {'A': 1}\nprint(1)",
        ran=True, run_ok=True,
    )
    assert progress["run_count"] == 2
    assert progress["success_run_count"] == 1
    assert progress["last_error"] is None

    final = db.submit_assignment(assignment["id"], learner["id"], "marks = {'A': 1}\nprint(1)")
    assert final["status"] == "submitted"
    assert final["submitted_at"]

    rows = db.list_progress_for_assignment(assignment["id"])
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Priya"


def test_help_requests_open_resolve_cancel():
    instructor = db.create_instructor("teach5", "hashed", "Ms Rao")
    cohort = db.create_cohort(instructor["id"], "Python Beginners")
    learner = db.join_cohort(cohort["id"], "Zayn")

    hr = db.create_help_request(cohort["id"], learner["id"], None, "I am stuck on loops")
    assert hr["status"] == "open"
    open_list = db.list_help_requests(cohort["id"], status="open")
    assert len(open_list) == 1

    resolved = db.resolve_help_request(hr["id"])
    assert resolved["status"] == "resolved"
    assert db.list_help_requests(cohort["id"], status="open") == []

    hr2 = db.create_help_request(cohort["id"], learner["id"], None, "another question")
    cancelled = db.cancel_help_request(hr2["id"], learner["id"])
    assert cancelled["status"] == "cancelled"
    # cancelling someone else's request should not work
    hr3 = db.create_help_request(cohort["id"], learner["id"], None, "third question")
    other_learner = db.join_cohort(cohort["id"], "Other")
    result = db.cancel_help_request(hr3["id"], other_learner["id"])
    assert result["status"] == "open"


def test_project_progress_tracks_checkpoints():
    instructor = db.create_instructor("teach6", "hashed", "Ms Rao")
    cohort = db.create_cohort(instructor["id"], "Python Beginners")
    learner = db.join_cohort(cohort["id"], "Kai")

    progress = db.get_or_create_project_progress(learner["id"], "student_marks")
    assert progress["checkpoints_completed"] == []

    updated = db.save_project_progress(
        learner["id"], "student_marks", code="marks = {}", checkpoints_completed=["dictionary"],
    )
    assert updated["checkpoints_completed"] == ["dictionary"]


def test_concept_progress_state_and_events():
    instructor = db.create_instructor("teach7", "hashed", "Ms Rao")
    cohort = db.create_cohort(instructor["id"], "Python Beginners")
    learner = db.join_cohort(cohort["id"], "Sam")

    db.log_event(learner["id"], cohort["id"], "run_success", {"foo": "bar"})
    events = db.list_events_for_learner(learner["id"])
    assert len(events) == 1
    assert events[0]["payload"] == {"foo": "bar"}

    state = db.set_concept_state(learner["id"], "loops", "introduced")
    assert state["state"] == "introduced"
    state = db.set_concept_state(learner["id"], "loops", "practised")
    assert state["state"] == "practised"
    assert state["evidence_count"] == 2

    summary = db.get_concept_progress(learner["id"])
    assert summary["loops"]["state"] == "practised"
