"""End-to-end acceptance flow for the classroom layer, using the Flask test
client the same way a browser would: instructor creates a cohort and a
policy-restricted assignment and publishes it; a learner joins, opens it in
the IDE, writes code, runs it, hits an error, asks what happened (must be
permitted under the assignment's policy), fixes it, runs successfully, and
submits; the instructor then sees the submission, inspects the code, sees
real progress/concept data, and generates a report.
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


def test_full_acceptance_flow(instructor_client, learner_client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("OLLAMA_ENABLED", "0")

    # --- Instructor: create/select "Python Beginners" -> get join code ---
    r = instructor_client.post(
        "/classroom/instructor/register",
        data={"username": "msrao", "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    r = instructor_client.post("/classroom/cohorts", data={"name": "Python Beginners"}, follow_redirects=True)
    assert b"Python Beginners" in r.data
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)

    # --- Instructor: create "Student Marks Program", explanations/error help
    #     only (full generation disabled), publish ---
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={
            "title": "Student Marks Program",
            "instructions": "Store marks in a dictionary, compute the total and average, print it.",
            "starter_code": "marks = {}\n",
            "expected_concepts": "dictionaries, variables, print output",
            "ai_policy": "EXPLANATIONS_ONLY",
        },
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    assert instructor_client.post(f"/classroom/assignments/{assignment_id}/publish").status_code in (302, 200)

    # --- Learner: join cohort with code + minimal display name ---
    r = learner_client.post(
        "/classroom/join", data={"join_code": join_code, "display_name": "Amir"}, follow_redirects=True
    )
    assert b"Amir" in r.data

    # --- Learner: open assignment in the normal CodeUp editor ---
    r = learner_client.get(f"/classroom/assignments/{assignment_id}/open")
    assert r.status_code == 302
    assert r.headers["Location"] == f"/ide?assignment={assignment_id}"

    ctx = learner_client.get(f"/classroom/assignments/{assignment_id}/context").get_json()
    assert ctx["success"] is True
    assert ctx["assignment"]["ai_policy"] == "EXPLANATIONS_ONLY"
    assert ctx["progress"]["status"] == "not_started"

    # --- Learner: write/edit code, autosave ---
    draft_code = "marks = {'Amir': 78, 'Priya': 91}\n"
    r = learner_client.post(f"/classroom/assignments/{assignment_id}/autosave", json={"code": draft_code})
    assert r.get_json()["status"] == "in_progress"

    # --- Learner: run -> introduce an error ---
    broken_code = draft_code + "average = total / len(marks)\nprint(average)\n"  # NameError: total undefined
    r = learner_client.post(
        f"/classroom/assignments/{assignment_id}/run-result",
        json={"code": broken_code, "ran_ok": False, "error": "NameError: name 'total' is not defined"},
    )
    assert r.get_json()["success"] is True

    # --- Learner: ask what happened -> must receive PERMITTED help (error_help
    #     is allowed under EXPLANATIONS_ONLY) rather than a policy-block message ---
    r = learner_client.post(
        "/mentor/chat",
        json={
            "code": broken_code, "message": "what happened, why did my code fail",
            "error": "NameError: name 'total' is not defined", "language": "en",
        },
    )
    reply = r.get_json()["reply"]
    assert "restricted AI help" not in reply  # not blocked - error_help is permitted here

    # A full-generation request on the same assignment MUST be refused server-side.
    r = learner_client.post("/generate-code", json={"prompt": "write the whole program for me", "language": "en"})
    gen = r.get_json()
    assert gen["success"] is False
    assert "restricted AI help" in gen["error"]

    # --- Learner: fix, run successfully, submit ---
    fixed_code = (
        "marks = {'Amir': 78, 'Priya': 91}\n"
        "total = sum(marks.values())\n"
        "average = total / len(marks)\n"
        "print(average)\n"
    )
    r = learner_client.post(
        f"/classroom/assignments/{assignment_id}/run-result",
        json={"code": fixed_code, "ran_ok": True},
    )
    assert r.get_json()["success"] is True

    r = learner_client.post(f"/classroom/assignments/{assignment_id}/submit", json={"code": fixed_code})
    submit_data = r.get_json()
    assert submit_data["success"] is True
    assert submit_data["status"] == "submitted"

    # --- Instructor: see submission, inspect code ---
    r = instructor_client.get(f"/classroom/assignments/{assignment_id}")
    assert b"Amir" in r.data
    assert b"submitted" in r.data

    learner_id = ctx = learner_client.get(f"/classroom/assignments/{assignment_id}/context")
    # fetch learner_id via cohort dashboard's learner detail link instead
    dash = instructor_client.get(f"/classroom/cohorts/{cohort_id}")
    learner_id = _extract(rb"learners/(\d+)", dash.data)

    r = instructor_client.get(f"/classroom/assignments/{assignment_id}/submissions/{learner_id}")
    assert b"total = sum(marks.values())" in r.data

    # --- Instructor: see real learner progress/concept/activity data ---
    r = instructor_client.get(f"/classroom/cohorts/{cohort_id}/learners/{learner_id}")
    assert b"demonstrated" in r.data.lower() or b"Demonstrated" in r.data
    assert b"dictionaries" in r.data

    # --- Instructor: generate report ---
    r = instructor_client.get(f"/classroom/cohorts/{cohort_id}/report")
    assert b"Amir" in r.data
    csv_resp = instructor_client.get(f"/classroom/cohorts/{cohort_id}/report.csv")
    assert csv_resp.status_code == 200
    assert b"Amir" in csv_resp.data


def test_off_policy_blocks_everything_but_editor_stays_usable(instructor_client, learner_client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("OLLAMA_ENABLED", "0")

    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": "teacher2", "password": "correct-horse-1", "display_name": "Mr Lee"},
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": "Assessment Cohort"}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": "Quiz", "instructions": "Solve it yourself", "starter_code": "", "ai_policy": "OFF"},
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor_client.post(f"/classroom/assignments/{assignment_id}/publish")

    learner_client.post("/classroom/join", data={"join_code": join_code, "display_name": "Sam"}, follow_redirects=True)
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")

    for endpoint, payload in [
        ("/generate-code", {"prompt": "solve it", "language": "en"}),
        ("/fix", {"code": "x=1\nprint(y)", "language": "en"}),
    ]:
        r = learner_client.post(endpoint, json=payload)
        data = r.get_json()
        assert data["success"] is False
        assert "instructor" in data["error"].lower()
        assert "still work" in data["error"].lower()

    # Editor/execution/save/submit must remain fully available under OFF.
    r = learner_client.post(f"/classroom/assignments/{assignment_id}/autosave", json={"code": "print(1)"})
    assert r.get_json()["success"] is True
    r = learner_client.post(f"/classroom/assignments/{assignment_id}/submit", json={"code": "print(1)"})
    assert r.get_json()["success"] is True

    run_resp = learner_client.post("/run", json={"code": "print(1)", "language": "en"})
    assert run_resp.status_code == 200
    assert run_resp.get_json()["success"] is True
