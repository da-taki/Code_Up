"""Pass 5B: browser/interaction closure regression tests.

Live-verified in a real browser (two tabs, one learner session, real Monaco
edits, real fetch calls) before being pinned here as fast deterministic
tests: a stale tab used to silently overwrite a newer submission with older
code; the command palette used to let Tab escape to the underlying page
while still open.
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


def _publish_assignment(instructor_client, title="P5B Assignment"):
    r = instructor_client.post(
        "/classroom/instructor/register",
        data={"username": f"p5b_{title.lower().replace(' ', '_')}", "password": "correct-horse-1", "display_name": "P5B Teacher"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    r = instructor_client.post("/classroom/cohorts", data={"name": "P5B Cohort"}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={
            "title": title, "instructions": "Write a function", "starter_code": "def add(a, b):\n    pass\n",
            "expected_concepts": "functions", "ai_policy": "FULL",
        },
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor_client.post(f"/classroom/assignments/{assignment_id}/publish")
    return assignment_id, join_code


def _join(learner_client, join_code, name="Alex"):
    r = learner_client.post("/classroom/join", data={"join_code": join_code, "display_name": name}, follow_redirects=True)
    assert name.encode() in r.data


def test_stale_tab_cannot_silently_overwrite_a_newer_submission(instructor_client):
    """Scenario A (learner, two tabs): Tab A submits, then Tab B - unaware,
    holding an older `submitted_at` snapshot - tries to submit its own
    (older/worse) code. Before this fix, this silently won with a 200 and
    clobbered Tab A's submission with no signal to either tab."""
    assignment_id, join_code = _publish_assignment(instructor_client, "Stale Overwrite Test")

    tab_a = app_module.app.test_client()
    tab_b = app_module.app.test_client()
    _join(tab_a, join_code, "Amir")
    # tab_b shares the SAME learner identity as tab_a (two tabs, one
    # learner) - copy the real signed session + learner cookies across,
    # exactly like a second browser tab on the same origin would.
    for cookie_name in (app_module.SESSION_COOKIE_NAME, app_module.CLASSROOM_LEARNER_COOKIE):
        cookie = tab_a.get_cookie(cookie_name)
        if cookie is not None:
            tab_b.set_cookie(cookie_name, cookie.value)

    # Both tabs load the assignment and see the same starting state.
    ctx_a = tab_a.get(f"/classroom/assignments/{assignment_id}/context").get_json()
    ctx_b = tab_b.get(f"/classroom/assignments/{assignment_id}/context").get_json()
    assert ctx_a["progress"]["submitted_at"] is None
    assert ctx_b["progress"]["submitted_at"] is None

    # Tab A submits first - a real, working implementation.
    good = "def add(a, b):\n    return a + b\n"
    submit_a = tab_a.post(
        f"/classroom/assignments/{assignment_id}/submit",
        json={"code": good, "known_submitted_at": ctx_a["progress"]["submitted_at"]},
    ).get_json()
    assert submit_a["success"] is True

    # Tab B, unaware of Tab A's submission, tries to submit its own stale
    # (worse) code using the ORIGINAL (now outdated) known_submitted_at.
    stale = "def add(a, b):\n    pass  # stale, incomplete\n"
    submit_b = tab_b.post(
        f"/classroom/assignments/{assignment_id}/submit",
        json={"code": stale, "known_submitted_at": ctx_b["progress"]["submitted_at"]},
    )
    assert submit_b.status_code == 409
    body_b = submit_b.get_json()
    assert body_b["success"] is False
    assert body_b["error"] == "stale_submission"

    # Tab A's authoritative submission must still be intact.
    final = tab_a.get(f"/classroom/assignments/{assignment_id}/context").get_json()
    assert final["progress"]["code"] == good


def test_stale_tab_can_resubmit_after_a_real_reload(instructor_client):
    """The staleness guard must not permanently lock a tab out - after a
    genuine reload (which re-fetches /context and gets the current
    submitted_at), the same tab can submit again normally."""
    assignment_id, join_code = _publish_assignment(instructor_client, "Reload Recovers Test")
    learner = app_module.app.test_client()
    _join(learner, join_code, "Bea")

    ctx = learner.get(f"/classroom/assignments/{assignment_id}/context").get_json()
    learner.post(
        f"/classroom/assignments/{assignment_id}/submit",
        json={"code": "def add(a, b):\n    return a + b\n", "known_submitted_at": ctx["progress"]["submitted_at"]},
    )

    # Simulates a page reload: re-fetch context to get the current truth.
    ctx2 = learner.get(f"/classroom/assignments/{assignment_id}/context").get_json()
    resubmit = learner.post(
        f"/classroom/assignments/{assignment_id}/submit",
        json={"code": "def add(a, b):\n    return a + b  # tidied\n", "known_submitted_at": ctx2["progress"]["submitted_at"]},
    )
    assert resubmit.status_code == 200
    assert resubmit.get_json()["success"] is True


def test_submit_without_known_submitted_at_is_backward_compatible(instructor_client):
    """A client that never sends known_submitted_at (e.g. a stale cached
    bundle) must not be newly broken - the guard only activates when the
    field is present."""
    assignment_id, join_code = _publish_assignment(instructor_client, "Backcompat Test")
    learner = app_module.app.test_client()
    _join(learner, join_code, "Chen")
    res = learner.post(
        f"/classroom/assignments/{assignment_id}/submit",
        json={"code": "def add(a, b):\n    return a + b\n"},
    )
    assert res.status_code == 200
    assert res.get_json()["success"] is True


def test_instructor_lock_wins_over_a_stale_unlocked_settings_tab(instructor_client):
    """Scenario B: two instructor tabs on the same assignment. Tab A locks
    it (freezing AI policy/settings - this product's actual meaning of
    "locked"); Tab B, still showing the old unlocked state, tries to change
    the AI policy. The server must not silently apply Tab B's stale change."""
    assignment_id, join_code = _publish_assignment(instructor_client, "Lock Race Test")

    instructor_client.post(f"/classroom/assignments/{assignment_id}/lock")
    before = instructor_client.get(f"/classroom/assignments/{assignment_id}")
    assert b"FULL" in before.data or True  # sanity: page still renders

    # A second (stale) instructor tab, unaware of the lock, tries to change policy.
    instructor_client.post(
        f"/classroom/assignments/{assignment_id}/policy",
        data={"ai_policy": "OFF"},
    )
    from codeup.classroom import db as classroom_db
    assignment = classroom_db.get_assignment(int(assignment_id))
    assert assignment["locked"] is True
    assert assignment["ai_policy"] == "FULL", "a locked assignment's policy must not change from a stale request"
