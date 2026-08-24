"""Robustness tests for the join-from-IDE conversation: the real browser
typed/spoken pathway (join_name carried in the /voice-command payload, not
manually supplied only in test bodies), the pending-join state machine
(WAITING_FOR_CODE / WAITING_FOR_NAME), cancellation, invalid-code and
network-failure recovery, and duplicate/race protection. Session-scoped
pending state lives in the existing per-session memory dict - never the
classroom database - so it can never outlive the session or strand a
learner permanently.
"""

import re

import pytest

import app as app_module
from codeup.classroom import ide_commands, learner_actions


@pytest.fixture
def instructor_client():
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
    return _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)


def _voice(client, text, **body):
    return client.post("/voice-command", json={"text": text, **body}).get_json()


def _joined(client):
    return client.get("/classroom/ide/summary").get_json()


# ---- FLOW A: typed join with the name already known (payload, not test-only) ----

def test_typed_join_uses_join_name_from_the_real_payload_field(instructor_client):
    """This is the exact field static/app.js's buildVoiceCommandPayload now
    sends from the visible Name input - proves the real browser pathway,
    not just a test manually injecting join_name."""
    code = _make_cohort(instructor_client)
    learner = app_module.app.test_client()
    data = _voice(learner, f"join {code}", join_name="Amir")
    assert data["success"] is True
    assert "you joined" in data["message"].lower()
    assert _joined(learner)["learner"]["display_name"] == "Amir"


def test_successful_join_response_signals_panel_refresh(instructor_client):
    """The classroom panel is still showing the Join form at the moment a
    typed/spoken join succeeds - the response must carry a flag so
    static/app.js's handleConfirmedAction() knows to refresh it into the
    joined dashboard without a full page reload."""
    code = _make_cohort(instructor_client)
    learner = app_module.app.test_client()
    data = _voice(learner, f"join {code}", join_name="Amir")
    assert data.get("classroom_refresh") is True


def test_failed_join_does_not_signal_a_refresh(instructor_client):
    learner = app_module.app.test_client()
    data = _voice(learner, "join NOPE99", join_name="Amir")
    assert not data.get("classroom_refresh")


# ---- FLOW C: fully conversational (no join_name field at all) -------------------

def test_conversational_join_code_then_name(instructor_client):
    code = _make_cohort(instructor_client)
    learner = app_module.app.test_client()

    r1 = _voice(learner, f"join {code}")
    assert "what name should i use" in r1["message"].lower()
    assert r1["join_code_hint"] == code
    assert r1["focus_hint"] == "classroomJoinName"

    r2 = _voice(learner, "Amir")
    assert "you joined" in r2["message"].lower()
    assert _joined(learner)["joined"] is True
    assert _joined(learner)["learner"]["display_name"] == "Amir"


def test_conversational_join_prompt_then_code_then_name(instructor_client):
    code = _make_cohort(instructor_client)
    learner = app_module.app.test_client()

    r1 = _voice(learner, "join a cohort")
    assert "class code" in r1["message"].lower()
    assert r1["focus_hint"] == "classroomJoinCode"

    r2 = _voice(learner, code)
    assert "what name" in r2["message"].lower()

    r3 = _voice(learner, "Priya")
    assert "you joined" in r3["message"].lower()
    assert _joined(learner)["learner"]["display_name"] == "Priya"


def test_bare_name_reply_is_never_confused_with_a_join_code(instructor_client):
    """Regression: a name like "Amir" is shaped just like a plausible join
    code (4-8 alnum chars) - it must still be consumed as the NAME while
    WAITING_FOR_NAME, not misfired as some other feature's fuzzy matcher."""
    code = _make_cohort(instructor_client)
    learner = app_module.app.test_client()
    _voice(learner, f"join {code}")
    r = _voice(learner, "Amir")
    assert r["success"] is True
    assert "you joined" in r["message"].lower()


# ---- cancellation -----------------------------------------------------------------

def test_cancel_while_waiting_for_code(instructor_client):
    learner = app_module.app.test_client()
    _voice(learner, "join a cohort")
    r = _voice(learner, "cancel")
    assert "cancelled" in r["message"].lower()
    # No longer stuck: an ordinary command works immediately afterward.
    r2 = _voice(learner, "go to top")
    assert r2["action"] == "go_to_top"


def test_cancel_while_waiting_for_name(instructor_client):
    code = _make_cohort(instructor_client)
    learner = app_module.app.test_client()
    _voice(learner, f"join {code}")
    r = _voice(learner, "never mind")
    assert "cancelled" in r["message"].lower()
    assert _joined(learner)["joined"] is False


def test_cancel_outside_a_pending_join_is_not_globally_captured(instructor_client):
    """"cancel" must only mean "cancel joining" while a join is actually
    pending - outside that, it's whatever the existing pipeline already
    does with it, untouched by the classroom layer."""
    learner = app_module.app.test_client()
    r = _voice(learner, "cancel")
    assert "classroom" not in (r.get("message") or "").lower()


def test_unrelated_utterance_while_waiting_for_code_does_not_get_swallowed(instructor_client):
    learner = app_module.app.test_client()
    _voice(learner, "join a cohort")
    r = _voice(learner, "what is the weather today")
    # Not treated as a code, not stuck - falls through to the normal pipeline
    # (whatever that resolves to, it must not be the join-name prompt).
    assert "what name should i use" not in (r.get("message") or "").lower()
    r2 = _voice(learner, "go to top")
    assert r2["action"] == "go_to_top"


# ---- invalid code recovery ---------------------------------------------------------

def test_invalid_code_then_immediate_retry_uses_the_new_code(instructor_client):
    real_code = _make_cohort(instructor_client)
    learner = app_module.app.test_client()

    _voice(learner, "join WRONG1")
    r_invalid = _voice(learner, "Amir")
    assert "couldn't find a classroom" in r_invalid["message"].lower()

    r_retry = _voice(learner, f"join {real_code}")
    assert "you joined" in r_retry["message"].lower()  # reused remembered "Amir", no re-prompt
    assert _joined(learner)["learner"]["display_name"] == "Amir"


def test_invalid_code_message_never_leaks_internals(instructor_client):
    learner = app_module.app.test_client()
    _voice(learner, "join WRONG1")
    r = _voice(learner, "Amir")
    msg = r["message"].lower()
    assert "traceback" not in msg and "exception" not in msg and "sqlite" not in msg


# ---- network / server failure recovery ----------------------------------------------

def test_network_failure_is_distinguished_from_invalid_code(instructor_client, monkeypatch):
    code = _make_cohort(instructor_client)
    monkeypatch.setattr(
        learner_actions, "join_cohort_by_code",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk I/O error")),
    )
    learner = app_module.app.test_client()
    r = _voice(learner, f"join {code}", join_name="Amir")
    assert "could not join right now" in r["message"].lower()
    assert "couldn't find a classroom" not in r["message"].lower()
    assert "runtimeerror" not in r["message"].lower() and "traceback" not in r["message"].lower()


def test_retry_succeeds_after_a_transient_failure_clears(instructor_client, monkeypatch):
    code = _make_cohort(instructor_client)
    real_join = learner_actions.join_cohort_by_code
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return real_join(*a, **k)

    monkeypatch.setattr(learner_actions, "join_cohort_by_code", flaky)
    learner = app_module.app.test_client()
    _voice(learner, f"join {code}", join_name="Amir")  # fails once, remembers name
    r = _voice(learner, f"join {code}")  # retry - name still known
    assert "you joined" in r["message"].lower()


# ---- duplicate / race protection ----------------------------------------------------

def test_duplicate_sequential_typed_join_does_not_create_two_learners(instructor_client):
    code = _make_cohort(instructor_client)
    learner = app_module.app.test_client()
    _voice(learner, f"join {code}", join_name="Sam")
    r2 = _voice(learner, f"join {code}", join_name="Sam")
    assert "already in a classroom" in r2["message"].lower()
    assert _joined(learner)["learner"]["display_name"] == "Sam"


def test_duplicate_join_api_calls_are_rejected_409(instructor_client):
    code = _make_cohort(instructor_client)
    learner = app_module.app.test_client()
    r1 = learner.post("/classroom/join-api", json={"join_code": code, "display_name": "Priya"})
    r2 = learner.post("/classroom/join-api", json={"join_code": code, "display_name": "Priya Again"})
    assert r1.status_code == 200
    assert r2.status_code == 409
    assert r2.get_json()["error"] == "already_joined"


def test_stale_pending_join_is_cleared_once_already_joined_out_of_band(instructor_client):
    """A learner starts a conversational join, then completes it via the
    visual form (or another tab) before answering - the next utterance must
    not resurrect the old "what name should I use?" prompt."""
    code = _make_cohort(instructor_client)
    learner = app_module.app.test_client()
    r1 = _voice(learner, "join a cohort")
    assert "class code" in r1["message"].lower()

    learner.post("/classroom/join-api", json={"join_code": code, "display_name": "Zara"})

    r2 = _voice(learner, "Zara")  # would have been consumed as a code otherwise
    assert "what name should i use" not in (r2.get("message") or "").lower()
    assert "you joined" not in (r2.get("message") or "").lower()  # not a second join either


# ---- zero Groq calls throughout the entire join conversation ------------------------

def test_join_conversation_never_calls_groq(instructor_client, monkeypatch):
    called = {"groq": False}
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: called.__setitem__("groq", True) or "x")
    code = _make_cohort(instructor_client)
    learner = app_module.app.test_client()

    _voice(learner, "join a cohort")
    _voice(learner, code)
    _voice(learner, "Amir")
    _voice(learner, "what should I do")
    _voice(learner, "go to editor")
    _voice(learner, "what class am I in")

    assert called["groq"] is False


def test_join_prompt_response_is_deterministic_never_groq(monkeypatch):
    called = {"groq": False}
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: called.__setitem__("groq", True) or "x")
    learner = app_module.app.test_client()
    _voice(learner, "join a cohort")
    _voice(learner, "cancel")
    assert called["groq"] is False


# ---- pure matcher: ambiguity heuristics -----------------------------------------------

def test_looks_like_join_code_matches_bare_alnum_tokens():
    assert ide_commands.looks_like_join_code("ABC123")
    assert ide_commands.looks_like_join_code("amir")  # shape-only, deliberately permissive
    assert not ide_commands.looks_like_join_code("join ABC123")  # not a bare token
    assert not ide_commands.looks_like_join_code("")


def test_handle_pending_join_waiting_for_name_ignores_code_shape():
    pending = {"state": "waiting_for_name", "code": "ABC123"}
    response, new_pending = ide_commands.handle_pending_join(
        "Amir", pending, {"learner": None, "join_name": ""},
    )
    assert response is not None
    assert response["success"] is True


def test_handle_pending_join_returns_none_none_when_nothing_pending():
    response, new_pending = ide_commands.handle_pending_join("anything", None, {})
    assert response is None
    assert new_pending is None
