from codeup.classroom import db
import app as app_module


def _make_instructor_with_cohort(username, cohort_name):
    client = app_module.app.test_client()
    client.post(
        "/classroom/instructor/register",
        data={"username": username, "password": "correct-horse-1", "display_name": username},
    )
    instructor = db.get_instructor_by_username(username)
    cohort = db.create_cohort(instructor["id"], cohort_name)
    return client, instructor, cohort


def _join_learner(cohort, name="Learner"):
    client = app_module.app.test_client()
    response = client.post(
        "/classroom/join-api",
        json={"join_code": cohort["join_code"], "display_name": name},
    )
    assert response.status_code == 200
    return client


def test_learner_cannot_access_another_cohorts_custom_lesson():
    _client_a, instructor_a, cohort_a = _make_instructor_with_cohort("lesson_owner_a", "Cohort A")
    _client_b, instructor_b, cohort_b = _make_instructor_with_cohort("lesson_owner_b", "Cohort B")
    lesson_b = db.create_custom_lesson(
        cohort_b["id"], instructor_b["id"],
        title="Private lesson", objective="private", explanation="private explanation",
        starter_code="print('private')", instructions="private instructions",
        expected_concepts=["print output"], challenge="print something",
        quiz_question="Private?", quiz_choices=["Yes", "No"], quiz_answer_index=0,
    )
    learner_a = _join_learner(cohort_a, "Amir")
    module_id = f"custom:{lesson_b['id']}"

    assert learner_a.get(f"/classroom/curriculum/{module_id}/context").status_code == 404
    assert learner_a.post(
        f"/classroom/curriculum/{module_id}/attempt", json={"code": "print('private')"}
    ).status_code == 404
    assert learner_a.post(
        f"/classroom/curriculum/{module_id}/challenge", json={"code": "print('private')"}
    ).status_code == 404
    assert learner_a.get(f"/classroom/curriculum/{module_id}/open").headers["Location"].endswith("/classroom/curriculum")


def test_learner_cannot_access_or_save_another_cohorts_custom_project():
    _client_a, instructor_a, cohort_a = _make_instructor_with_cohort("project_owner_a", "Cohort A")
    _client_b, instructor_b, cohort_b = _make_instructor_with_cohort("project_owner_b", "Cohort B")
    project_b = db.create_custom_project(
        cohort_b["id"], instructor_b["id"], title="Private project",
        instructions="private instructions", starter_code="print('private')",
        expected_concepts=["print output"],
        checkpoints=[{"label": "Print", "check_type": "contains_print", "check_config": {}}],
    )
    learner_a = _join_learner(cohort_a, "Priya")
    project_id = f"custom:{project_b['id']}"

    assert learner_a.get(f"/classroom/projects/{project_id}/context").status_code == 404
    assert learner_a.post(
        f"/classroom/projects/{project_id}/save", json={"code": "print('private')"}
    ).status_code == 404
    assert learner_a.get(f"/classroom/custom-projects/{project_b['id']}/open").headers["Location"].endswith("/classroom/learner")


def test_help_request_cannot_reference_another_cohorts_assignment():
    _client_a, instructor_a, cohort_a = _make_instructor_with_cohort("help_owner_a", "Cohort A")
    _client_b, instructor_b, cohort_b = _make_instructor_with_cohort("help_owner_b", "Cohort B")
    assignment_b = db.create_assignment(
        cohort_b["id"], "Private assignment", "private", "", None, [], "FULL", status="published"
    )
    learner_a = _join_learner(cohort_a, "Sam")

    response = learner_a.post(
        "/classroom/help-requests",
        json={"message": "I need help", "assignment_id": assignment_b["id"]},
    )
    assert response.status_code == 200
    help_request = response.get_json()["help_request"]
    assert help_request["cohort_id"] == cohort_a["id"]
    assert help_request["assignment_id"] is None


def test_repeated_help_request_returns_existing_open_request():
    _client, _instructor, cohort = _make_instructor_with_cohort("help_repeat_owner", "Help Cohort")
    learner = _join_learner(cohort, "Mina")

    first = learner.post("/classroom/help-requests", json={"message": "First"}).get_json()["help_request"]
    second = learner.post("/classroom/help-requests", json={"message": "Second"}).get_json()["help_request"]

    assert second["id"] == first["id"]
    assert len(db.list_help_requests(cohort["id"], status="open")) == 1
