"""Acceptance coverage for the simplified learner-to-instructor help loop."""

from __future__ import annotations

import re

from pathlib import Path
import pytest

import app as app_module
from codeup.classroom import db


@pytest.fixture
def instructor():
    return app_module.app.test_client()


@pytest.fixture
def learner():
    return app_module.app.test_client()


def _create_class(instructor):
    instructor.post(
        "/classroom/instructor/register",
        data={"username": "help_flow_teacher", "password": "correct-horse-1", "display_name": "Teacher"},
    )
    response = instructor.post("/classroom/cohorts", data={"name": "Python Beginners"}, follow_redirects=True)
    join_code = re.search(rb'cu-join-code">([A-Z0-9]+)<', response.data).group(1).decode()
    cohort_id = int(re.search(rb'cohorts/(\d+)/ai-toggle"', response.data).group(1))
    return join_code, cohort_id


def test_help_request_carries_latest_code_and_resolves_in_live_dashboard(instructor, learner):
    join_code, cohort_id = _create_class(instructor)
    joined = learner.post(
        "/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"}
    ).get_json()
    learner_id = joined["learner"]["id"]

    snapshot = learner.post(
        "/classroom/live-code/sync",
        json={"code": "name = input('Name? ')\nprint(name)", "output": "", "error": None},
    )
    assert snapshot.get_json()["success"] is True
    created = learner.post(
        "/classroom/help-requests", json={"message": "", "assignment_id": None}
    ).get_json()
    request_id = created["help_request"]["id"]

    dashboard = instructor.get(f"/classroom/cohorts/{cohort_id}/live")
    assert b"Help requests" in dashboard.data
    assert b"Amir" in dashboard.data
    assert f"/classroom/cohorts/{cohort_id}/learners/{learner_id}/live-code".encode() in dashboard.data
    assert b"Start helping" in dashboard.data
    assert b"Mark resolved" in dashboard.data

    helping = instructor.post(f"/classroom/help-requests/{request_id}/helping")
    assert helping.status_code == 302
    assert helping.headers["Location"].endswith(f"/classroom/cohorts/{cohort_id}/live")
    assert learner.get("/classroom/ide/summary").get_json()["help_request"]["status"] == "helping"

    live_code = instructor.get(f"/classroom/cohorts/{cohort_id}/learners/{learner_id}/live-code")
    assert b"name = input" in live_code.data

    resolved = instructor.post(f"/classroom/help-requests/{request_id}/resolve")
    assert resolved.status_code == 302
    assert resolved.headers["Location"].endswith(f"/classroom/cohorts/{cohort_id}/live")
    assert learner.get("/classroom/ide/summary").get_json()["help_request"] is None
    assert db.get_help_request(request_id)["status"] == "resolved"


def test_learner_help_widget_is_one_click_and_syncs_before_request():
    source = (Path(__file__).resolve().parent.parent / "static" / "classroom.js").read_text(encoding="utf-8")
    assert "textContent: 'Request instructor help'" in source
    assert "postJsonWithRetry('/classroom/live-code/sync', snapshot)" in source
    assert source.index("postJsonWithRetry('/classroom/live-code/sync', snapshot)") < source.index(
        "fetch('/classroom/help-requests'"
    )
    assert "classroomHelpMessage" not in source[source.index("function renderDashboardPanel"):source.index("function patchDashboardPanel")]
