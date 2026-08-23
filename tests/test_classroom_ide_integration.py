"""End-to-end tests for the IDE classroom integration: joining a cohort
directly from /ide (typed command AND the JSON join panel endpoint),
notification new/pending/overdue lifecycle, the deterministic classroom
command pipeline (assignments, curriculum, projects, help, AI policy,
submission, "what should I do"), cross-learner isolation, and confirmation
that plain non-classroom CodeUp usage is completely unaffected.

Uses the Flask test client the same way a browser would, mirroring
test_classroom_acceptance_flow.py's style. Never makes a real Groq request -
every command exercised here is matched deterministically in
codeup.classroom.ide_commands before any AI capability check runs.
"""

import re

import pytest

import app as app_module


@pytest.fixture
def instructor_client():
    return app_module.app.test_client()


@pytest.fixture
def learner_client():
    return app_module.app.test_client()


def _extract(pattern, data):
    match = re.search(pattern, data)
    assert match, f"pattern not found: {pattern}"
    return match.group(1).decode()


def _make_cohort(instructor_client, name="Python Beginners", username="msrao"):
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": username, "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": name}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    return join_code, cohort_id


def _publish_assignment(instructor_client, cohort_id, title="Student Marks Program", ai_policy="EXPLANATIONS_ONLY"):
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={
            "title": title, "instructions": "Store marks in a dictionary, compute the average.",
            "starter_code": "marks = {}\n", "expected_concepts": "dictionaries", "ai_policy": ai_policy,
        },
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor_client.post(f"/classroom/assignments/{assignment_id}/publish")
    return assignment_id


def _voice(client, text, **body):
    return client.post("/voice-command", json={"text": text, **body})


# ---- no-cohort IDE usage stays fully functional --------------------------------

def test_ide_summary_no_cohort_reports_not_joined():
    client = app_module.app.test_client()
    r = client.get("/classroom/ide/summary")
    data = r.get_json()
    assert data["success"] is True
    assert data["joined"] is False
    assert "join" in data["orientation_message"].lower() or "classroom code" in data["orientation_message"].lower()


def test_non_classroom_voice_commands_still_work_without_any_cohort():
    client = app_module.app.test_client()
    r = _voice(client, "go to top")
    data = r.get_json()
    assert data["success"] is True
    assert data["action"] == "go_to_top"


def test_classroom_phrase_with_no_cohort_gives_join_prompt_not_error():
    client = app_module.app.test_client()
    r = _voice(client, "what should I do")
    data = r.get_json()
    assert data["success"] is True
    assert "not in a classroom" in data["message"].lower()


# ---- joining from the IDE --------------------------------------------------------

def test_join_via_json_api_sets_cookie_and_returns_cohort(instructor_client, learner_client):
    join_code, _cohort_id = _make_cohort(instructor_client)
    r = learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    data = r.get_json()
    assert data["success"] is True
    assert data["cohort"]["name"] == "Python Beginners"
    assert "cu_learner_token" in r.headers.get("Set-Cookie", "")

    summary = learner_client.get("/classroom/ide/summary").get_json()
    assert summary["joined"] is True
    assert summary["learner"]["display_name"] == "Amir"


def test_join_via_invalid_code_gives_accessible_error(instructor_client, learner_client):
    _make_cohort(instructor_client)
    r = learner_client.post("/classroom/join-api", json={"join_code": "NOPE99", "display_name": "Amir"})
    assert r.status_code == 400
    data = r.get_json()
    assert data["success"] is False
    assert "database" not in data["message"].lower()  # no internal detail leakage


def test_join_via_voice_command_two_steps(instructor_client, learner_client):
    join_code, _cohort_id = _make_cohort(instructor_client)
    r = _voice(learner_client, f"join {join_code}")
    assert r.get_json()["success"] is True
    assert "name" in r.get_json()["message"].lower()

    r = _voice(learner_client, f"join {join_code}", join_name="Amir")
    data = r.get_json()
    assert "joined python beginners" in data["message"].lower()

    summary = learner_client.get("/classroom/ide/summary").get_json()
    assert summary["joined"] is True


def test_joining_twice_does_not_duplicate_learner(instructor_client, learner_client):
    join_code, _cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    r = learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir Again"})
    assert r.status_code == 409
    assert r.get_json()["error"] == "already_joined"


def test_returning_learner_restores_context_without_rejoining(instructor_client, learner_client):
    join_code, _cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    # Simulate closing and reopening the IDE: a fresh GET with the same
    # cookie jar (the test client persists cookies across requests) must
    # NOT show the join panel again.
    summary = learner_client.get("/classroom/ide/summary").get_json()
    assert summary["joined"] is True
    assert summary["learner"]["display_name"] == "Amir"


def test_leave_classroom_returns_to_join_state(instructor_client, learner_client):
    join_code, _cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    r = learner_client.post("/classroom/leave", json={})
    assert r.get_json()["success"] is True
    summary = learner_client.get("/classroom/ide/summary").get_json()
    assert summary["joined"] is False


# ---- assignment notification lifecycle (new -> pending -> seen) ----------------

def test_zero_assignments_summary(instructor_client, learner_client):
    join_code, _cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    summary = learner_client.get("/classroom/ide/summary").get_json()
    assert summary["assignment_counts"]["remaining"] == 0
    assert "no assignments left" in summary["welcome_message"].lower() or "welcome back" in summary["welcome_message"].lower()


def test_one_new_assignment_is_classified_new(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    _publish_assignment(instructor_client, cohort_id)
    summary = learner_client.get("/classroom/ide/summary").get_json()
    assert summary["assignment_counts"]["new"] == 1
    assert summary["assignments"][0]["state"] == "new"


def test_multiple_pending_and_new_assignments(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    _publish_assignment(instructor_client, cohort_id, title="First")
    # Learner "sees" the assignment list once.
    _voice(learner_client, "open my assignments")
    _publish_assignment(instructor_client, cohort_id, title="Second")
    summary = learner_client.get("/classroom/ide/summary").get_json()
    counts = summary["assignment_counts"]
    assert counts["new"] == 1  # only the second (published after last seen)
    assert counts["pending"] == 1  # the first, already seen
    assert counts["remaining"] == 2


def test_new_becomes_seen_after_reading_assignments(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    _publish_assignment(instructor_client, cohort_id)
    before = learner_client.get("/classroom/ide/summary").get_json()
    assert before["assignment_counts"]["new"] == 1

    _voice(learner_client, "open my assignments")

    after = learner_client.get("/classroom/ide/summary").get_json()
    assert after["assignment_counts"]["new"] == 0
    assert after["assignment_counts"]["pending"] == 1


def test_overdue_classification(instructor_client, learner_client, monkeypatch):
    join_code, cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={
            "title": "Overdue One", "instructions": "x", "starter_code": "",
            "expected_concepts": "", "ai_policy": "FULL", "due_date": "2020-01-01",
        },
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor_client.post(f"/classroom/assignments/{assignment_id}/publish")

    summary = learner_client.get("/classroom/ide/summary").get_json()
    assert summary["assignment_counts"]["overdue"] == 1
    assert summary["assignments"][0]["state"] == "overdue"


# ---- deterministic classroom commands ------------------------------------------

def test_what_should_i_do_prioritizes_new_assignment(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    _publish_assignment(instructor_client, cohort_id, title="Student Marks Program")
    r = _voice(learner_client, "what should I do")
    assert "Student Marks Program" in r.get_json()["message"]


def test_ai_policy_command_reports_restrictions(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assignment_id = _publish_assignment(instructor_client, cohort_id, ai_policy="EXPLANATIONS_ONLY")
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")  # sets the assignment cookie
    r = _voice(learner_client, "what can AI do")
    message = r.get_json()["message"].lower()
    assert "disabled" in message
    assert "explanations" in message or "error explanations" in message


def test_blocked_ai_capability_never_reaches_groq(instructor_client, learner_client, monkeypatch):
    called = {"groq": False}
    monkeypatch.setattr(
        app_module, "call_gemini",
        lambda *a, **k: called.__setitem__("groq", True) or "should not run",
    )
    join_code, cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assignment_id = _publish_assignment(instructor_client, cohort_id, ai_policy="OFF")
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")  # sets the assignment cookie

    r = learner_client.post("/mentor/chat", json={"code": "x = 1", "message": "write this for me", "mode": "general"})
    data = r.get_json()
    assert data["success"] is True
    assert "turned off" in data["reply"].lower()
    assert called["groq"] is False


def test_allowed_ai_capability_reaches_the_ai_call(instructor_client, learner_client, monkeypatch):
    called = {"groq": False}
    monkeypatch.setattr(
        app_module, "call_gemini",
        lambda *a, **k: called.__setitem__("groq", True) or "A reply.",
    )
    join_code, cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assignment_id = _publish_assignment(instructor_client, cohort_id, ai_policy="FULL")
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")

    r = learner_client.post("/mentor/chat", json={"code": "x = 1", "message": "what does this mean", "mode": "general"})
    assert r.get_json()["success"] is True
    assert called["groq"] is True


def test_submit_via_voice_command_uses_same_backend_path(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assignment_id = _publish_assignment(instructor_client, cohort_id)
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")

    r = _voice(learner_client, "submit my assignment", code="marks = {'Amir': 78}\n")
    data = r.get_json()
    assert data["success"] is True
    assert "submitted successfully" in data["message"].lower()

    # Confirm it actually persisted via the exact same submission the panel's
    # Submit button and the classic route both use.
    ctx = learner_client.get(f"/classroom/assignments/{assignment_id}/context").get_json()
    assert ctx["progress"]["status"] == "submitted"


def test_help_request_and_status_and_cancel_via_commands(instructor_client, learner_client):
    join_code, _cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})

    r = _voice(learner_client, "I need help")
    assert "sent to your instructor" in r.get_json()["message"].lower()

    r = _voice(learner_client, "is my teacher helping me")
    assert "waiting" in r.get_json()["message"].lower()

    r = _voice(learner_client, "cancel my help request")
    assert "cancelled" in r.get_json()["message"].lower()

    r = _voice(learner_client, "is my teacher helping me")
    assert "don't have an open help request" in r.get_json()["message"].lower()


def test_project_checkpoint_feedback_is_deterministic_and_humane(instructor_client, learner_client):
    join_code, _cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    r = learner_client.post("/classroom/projects/student_marks/save", json={"code": "marks = {'a': 1}\n"})
    data = r.get_json()
    assert data["newly_completed"] == ["dictionary"]
    assert "checkpoint_completed" not in data["feedback"]
    assert "next" in data["feedback"].lower()


def test_project_hint_command_is_deterministic_zero_groq(instructor_client, learner_client, monkeypatch):
    called = {"groq": False}
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: called.__setitem__("groq", True) or "x")
    join_code, _cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    learner_client.get("/classroom/projects/student_marks/open")  # sets project cookie
    r = _voice(learner_client, "give me a hint")
    assert r.get_json()["success"] is True
    assert called["groq"] is False


def test_navigation_focus_command_returns_dom_target(instructor_client, learner_client):
    join_code, _cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    r = _voice(learner_client, "go to editor")
    data = r.get_json()
    assert data["action"] == "focus_target"
    assert data["target"] == "__editor__"


# ---- cross-learner isolation -----------------------------------------------------

def test_learner_cannot_see_another_learners_help_status(instructor_client):
    join_code, _cohort_id = _make_cohort(instructor_client)
    amir = app_module.app.test_client()
    priya = app_module.app.test_client()
    amir.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    priya.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Priya"})

    _voice(amir, "I need help")
    priya_status = _voice(priya, "is my teacher helping me").get_json()["message"]
    assert "don't have an open help request" in priya_status.lower()


def test_learner_cannot_open_another_cohorts_assignment(instructor_client):
    join_code_a, cohort_a = _make_cohort(instructor_client, name="Cohort A", username="teacherA")
    join_code_b, cohort_b = _make_cohort(instructor_client, name="Cohort B", username="teacherB")
    assignment_b = _publish_assignment(instructor_client, cohort_b, title="Cohort B Assignment")

    learner_a = app_module.app.test_client()
    learner_a.post("/classroom/join-api", json={"join_code": join_code_a, "display_name": "Amir"})

    r = learner_a.get(f"/classroom/assignments/{assignment_b}/context")
    assert r.status_code == 404

    summary = learner_a.get("/classroom/ide/summary").get_json()
    assert summary["assignment_counts"]["remaining"] == 0  # sees nothing from cohort B
