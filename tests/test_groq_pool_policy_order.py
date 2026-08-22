"""Verifies the request flow required by the reliability pass: classroom AI
policy is evaluated BEFORE any Groq pool capacity (a key slot or a queue
slot) is ever touched. A blocked request must consume zero pool resources."""

import re

import pytest

import app as app_module
from codeup.providers import groq_pool


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


def test_policy_blocked_request_never_calls_the_pool(instructor_client, learner_client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-this-test")
    monkeypatch.setenv("OLLAMA_ENABLED", "0")

    calls = []
    real_call_with_pool = groq_pool.call_with_pool

    def spy_call_with_pool(*args, **kwargs):
        calls.append(True)
        return real_call_with_pool(*args, **kwargs)

    monkeypatch.setattr(groq_pool, "call_with_pool", spy_call_with_pool)
    monkeypatch.setattr(app_module, "groq_pool", groq_pool)

    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": "policyorder1", "password": "correct-horse-1", "display_name": "T"},
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": "C"}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": "A", "instructions": "i", "starter_code": "", "ai_policy": "OFF"},
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor_client.post(f"/classroom/assignments/{assignment_id}/publish")

    learner_client.post("/classroom/join", data={"join_code": join_code, "display_name": "Amir"}, follow_redirects=True)
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")

    before = groq_pool.status()

    r = learner_client.post("/generate-code", json={"prompt": "write it", "language": "en"})
    data = r.get_json()
    assert data["success"] is False  # blocked by policy

    r2 = learner_client.post("/fix", json={"code": "x=1\nprint(y)", "language": "en"})
    data2 = r2.get_json()
    assert data2["success"] is False

    learner_client.post("/analyze", json={"code": "x=1", "language": "en"})
    # explain is also OFF under the OFF preset

    after = groq_pool.status()

    assert calls == []  # the pool's own call_with_pool was NEVER invoked
    assert after["active_requests"] == 0
    assert after["queued_requests"] == 0
    # no key state should have been touched by these blocked requests
    assert after["key_rows"] == before["key_rows"]


def test_allowed_request_does_reach_the_pool(instructor_client, learner_client, monkeypatch):
    """Sanity check for the test above: when a capability IS allowed, the
    pool is reached (and - since no real key works here - fails cleanly
    without ever leaking active/queued state)."""
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("OLLAMA_ENABLED", "0")

    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": "policyorder2", "password": "correct-horse-1", "display_name": "T"},
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": "C"}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": "A", "instructions": "i", "starter_code": "", "ai_policy": "FULL"},
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor_client.post(f"/classroom/assignments/{assignment_id}/publish")

    learner_client.post("/classroom/join", data={"join_code": join_code, "display_name": "Sam"}, follow_redirects=True)
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")

    r = learner_client.post("/analyze", json={"code": "x=1", "language": "en"})
    assert r.status_code == 200  # never breaks the IDE even with no key configured

    after = groq_pool.status()
    assert after["active_requests"] == 0
    assert after["queued_requests"] == 0


# ---- diagnostic route access control + secret safety ---------------------------

def test_learner_cannot_access_groq_diagnostics(instructor_client, learner_client):
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": "diagaccess1", "password": "correct-horse-1", "display_name": "T"},
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": "C"}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)

    learner_client.post("/classroom/join", data={"join_code": join_code, "display_name": "Amir"}, follow_redirects=True)
    r = learner_client.get("/classroom/admin/groq-status", follow_redirects=False)
    assert r.status_code in (302, 401)
    assert b"groq-key-" not in r.data


def test_anonymous_cannot_access_groq_diagnostics():
    anon = app_module.app.test_client()
    r = anon.get("/classroom/admin/groq-status", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_diagnostics_contain_no_secret_values(instructor_client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "totally-real-secret-abc123")
    monkeypatch.setenv("GROQ_API_KEY_2", "another-secret-xyz789")
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": "diagaccess2", "password": "correct-horse-1", "display_name": "T"},
    )
    r = instructor_client.get("/classroom/admin/groq-status")
    assert r.status_code == 200
    assert b"totally-real-secret-abc123" not in r.data
    assert b"another-secret-xyz789" not in r.data
