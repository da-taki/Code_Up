"""Vision-Aid build: classroom's one job is (1) a simple class-and-student
AI ON/OFF toggle and (2) letting an instructor see each enrolled learner's
live current code. No assignments, grading, or LMS surface involved.

Follows the exact instructor_client/learner_client + _make_cohort pattern
already established in test_classroom_ide_integration.py, and the exact
call_gemini-monkeypatch pattern already used there to prove an AI capability
check genuinely never reaches the AI provider when blocked.
"""

import re
from pathlib import Path

import pytest

import app as app_module
from codeup.classroom import ai_toggle, db as classroom_db

STATIC_APP = Path("static/app.js").read_text(encoding="utf-8")
CLASSROOM_JS = Path("static/classroom.js").read_text(encoding="utf-8")


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


def _make_cohort(instructor_client, name="Python Beginners", username="msrao_va"):
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": username, "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": name}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)/ai-toggle"', r.data)
    return join_code, int(cohort_id)


def _join(learner_client, join_code, name="Amir"):
    r = learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": name})
    return r.get_json()["learner"]["id"]


def _voice(client, text, **body):
    return client.post("/voice-command", json={"text": text, **body})


def _mentor_chat(client, message="write this for me", code="x = 1"):
    return client.post("/mentor/chat", json={"code": code, "message": message, "mode": "general"})


def _mock_groq(monkeypatch):
    called = {"hit": False}
    monkeypatch.setattr(
        app_module, "call_gemini",
        lambda *a, **k: called.__setitem__("hit", True) or "A reply.",
    )
    return called


# ---- codeup.classroom.ai_toggle: pure logic --------------------------------

def test_effective_ai_enabled_anonymous_ide_is_always_true():
    assert ai_toggle.effective_ai_enabled(None, None) is True


@pytest.mark.parametrize(
    "class_on,learner_on,expected",
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ],
)
def test_effective_ai_enabled_precedence(class_on, learner_on, expected):
    cohort = {"ai_enabled": class_on}
    learner = {"ai_enabled": learner_on}
    assert ai_toggle.effective_ai_enabled(cohort, learner) is expected


# ---- the four class/student combinations, end to end -----------------------

def _set_class_ai(instructor_client, cohort_id, enabled):
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/ai-toggle", json={"enabled": enabled},
    )
    assert r.get_json()["success"] is True


def _set_learner_ai(instructor_client, cohort_id, learner_id, enabled):
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/learners/{learner_id}/ai-toggle", json={"enabled": enabled},
    )
    assert r.get_json()["success"] is True


def test_class_on_learner_on_ai_works(instructor_client, learner_client, monkeypatch):
    called = _mock_groq(monkeypatch)
    join_code, cohort_id = _make_cohort(instructor_client, username="combo_on_on")
    learner_id = _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, True)
    _set_learner_ai(instructor_client, cohort_id, learner_id, True)

    r = _mentor_chat(learner_client, message="what does this mean")
    assert r.get_json()["success"] is True
    assert called["hit"] is True


def test_class_on_learner_off_ai_blocked(instructor_client, learner_client, monkeypatch):
    called = _mock_groq(monkeypatch)
    join_code, cohort_id = _make_cohort(instructor_client, username="combo_on_off")
    learner_id = _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, True)
    _set_learner_ai(instructor_client, cohort_id, learner_id, False)

    r = _mentor_chat(learner_client)
    data = r.get_json()
    assert data["success"] is True
    assert "disabled" in data["reply"].lower()
    assert called["hit"] is False


def test_class_off_learner_on_ai_blocked(instructor_client, learner_client, monkeypatch):
    called = _mock_groq(monkeypatch)
    join_code, cohort_id = _make_cohort(instructor_client, username="combo_off_on")
    learner_id = _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, False)
    _set_learner_ai(instructor_client, cohort_id, learner_id, True)

    r = _mentor_chat(learner_client)
    data = r.get_json()
    assert data["success"] is True
    assert "disabled" in data["reply"].lower()
    assert called["hit"] is False


def test_class_off_learner_off_ai_blocked(instructor_client, learner_client, monkeypatch):
    called = _mock_groq(monkeypatch)
    join_code, cohort_id = _make_cohort(instructor_client, username="combo_off_off")
    learner_id = _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, False)
    _set_learner_ai(instructor_client, cohort_id, learner_id, False)

    r = _mentor_chat(learner_client)
    data = r.get_json()
    assert data["success"] is True
    assert "disabled" in data["reply"].lower()
    assert called["hit"] is False


# ---- AI-off never touches deterministic accessibility features -------------

def test_deterministic_commands_work_while_class_ai_is_off(instructor_client, learner_client, monkeypatch):
    called = _mock_groq(monkeypatch)
    join_code, cohort_id = _make_cohort(instructor_client, username="deterministic")
    _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, False)

    for text, payload in (
        ("run", {"code": "print('hi')\n"}),
        ("where am i", {"code": "print('hi')\n", "cursor_line": 1}),
        ("read errors only", {"error": "NameError: name 'x' is not defined", "code": "print(x)\n"}),
    ):
        r = _voice(learner_client, text, **payload)
        data = r.get_json()
        assert data.get("success") is not False, (text, data)
        assert data.get("action") not in (None, "unknown"), (text, data)
    assert called["hit"] is False


def test_all_core_accessibility_paths_make_zero_provider_calls_when_ai_is_off(
    instructor_client, learner_client, monkeypatch,
):
    provider_calls = []

    def fail_provider(*args, **kwargs):
        provider_calls.append((args, kwargs))
        raise AssertionError("AI-off deterministic path reached an AI provider")

    for name in (
        "call_gemini",
        "call_gemini_capability",
        "call_conversation_orchestrator_ai",
        "_call_ollama",
    ):
        monkeypatch.setattr(app_module, name, fail_provider)

    join_code, cohort_id = _make_cohort(instructor_client, username="all_core_ai_off")
    learner_id = _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, False)
    _set_learner_ai(instructor_client, cohort_id, learner_id, False)
    code = "for item in range(2):\n    print(item)\n"

    for text, payload, useful_words in (
        ("read output", {"code": code, "output": "0\n1\n"}, ("read_output",)),
        ("read line 2", {"code": code, "cursor_line": 2}, ("read_line",)),
        ("read line 2 exactly", {"code": code, "cursor_line": 2}, ("read_line",)),
        ("explain line 2", {"code": code, "cursor_line": 2}, ("line 2", "print")),
        ("where am i", {"code": code, "cursor_line": 2}, ("where_am_i",)),
        ("read around me", {"code": code, "cursor_line": 2}, ("line 1", "line 2")),
        ("why is this indented", {"code": code, "cursor_line": 2}, ("indent", "loop")),
        ("what contains this line", {"code": code, "cursor_line": 2}, ("for", "loop")),
        ("stop speaking", {}, ("stop_speaking",)),
        ("go to line 2", {"code": code, "cursor_line": 1}, ("goto_line", "navigate")),
    ):
        data = _voice(learner_client, text, **payload).get_json()
        assert data.get("action") not in (None, "unknown"), (text, data)
        assert "AI help is currently disabled" not in str(data), (text, data)
        local_text = " ".join(
            str(data.get(key) or "")
            for key in ("action", "intent", "line", "message", "speech", "output")
        ).lower()
        assert any(word in local_text for word in useful_words), (text, data)

    for text, expected_action in (
        ("explain my code", "analyze"),
        ("analyze deeper", "analyze_deep"),
    ):
        data = _voice(learner_client, text, code=code, cursor_line=2).get_json()
        assert data.get("action") == expected_action, (text, data)
        assert "AI help is currently disabled" not in str(data), (text, data)

    basic = learner_client.post("/analyze", json={"code": code}).get_json()
    deep = learner_client.post("/analyze-deep", json={"code": code}).get_json()
    assert "Line 1:" in basic["analysis"] and "Line 2:" in basic["analysis"]
    assert basic["structural_source"] == "ast-tokenize"
    assert "Line 1:" in deep["analysis"] and "Line 2:" in deep["analysis"]
    assert "colon says that an indented block follows" in deep["analysis"]
    assert deep["structural_source"] == "ast-tokenize"

    blocked = _voice(
        learner_client,
        "what is a metaclass",
        code=code,
        cursor_line=2,
    ).get_json()
    assert blocked["message"] == "AI help is currently disabled by your instructor."
    assert blocked["speech"] == "AI help is currently disabled by your instructor."
    assert provider_calls == []

    run = learner_client.post(
        "/run", json={"code": "name = input('Name: ')\nprint(name)\n"},
    ).get_json()
    assert run.get("action") == "request_program_input"
    assert provider_calls == []


def test_run_and_output_work_while_class_ai_is_off(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="run_while_off")
    _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, False)

    r = learner_client.post("/run", json={"code": "print('hello')\n"})
    data = r.get_json()
    assert data.get("success") is True
    assert "hello" in (data.get("output") or data.get("stdout") or "")


def test_voice_triggered_ai_command_respects_policy(instructor_client, learner_client, monkeypatch):
    called = _mock_groq(monkeypatch)
    join_code, cohort_id = _make_cohort(instructor_client, username="voice_policy")
    _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, False)

    r = _voice(learner_client, "write this for me", code="x = 1\n", source="voice")
    assert r.get_json().get("success") is not False
    assert called["hit"] is False


def test_typed_ai_command_respects_policy(instructor_client, learner_client, monkeypatch):
    called = _mock_groq(monkeypatch)
    join_code, cohort_id = _make_cohort(instructor_client, username="typed_policy")
    _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, False)

    r = _voice(learner_client, "write this for me", code="x = 1\n", source="typed")
    assert r.get_json().get("success") is not False
    assert called["hit"] is False


def test_direct_endpoint_attempt_respects_policy(instructor_client, learner_client, monkeypatch):
    """A learner cannot bypass the instructor's setting by hitting the raw
    AI endpoint directly (not through a typed/voice command) - the gate is
    server-side in _ai_capability_check, not a frontend button removal."""
    called = _mock_groq(monkeypatch)
    join_code, cohort_id = _make_cohort(instructor_client, username="direct_policy")
    _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, False)

    r = _mentor_chat(learner_client, message="explain this")
    assert r.get_json().get("success") is True
    assert called["hit"] is False


def test_ai_disabled_message_is_not_alarming(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="friendly_msg")
    _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, False)

    data = _mentor_chat(learner_client).get_json()
    assert "instructor" in data["reply"].lower()
    assert "error" not in data["reply"].lower()


# ---- instructor controls ----------------------------------------------------

def test_new_cohort_defaults_class_ai_to_off(instructor_client):
    """An instructor explicitly opts in - AI is not quietly on from the
    moment a class is created."""
    _join_code, cohort_id = _make_cohort(instructor_client, username="default_off")
    cohort = classroom_db.get_cohort(cohort_id)
    assert bool(cohort["ai_enabled"]) is False


def test_new_cohort_ai_off_blocks_ai_but_not_deterministic_features(instructor_client, learner_client, monkeypatch):
    called = _mock_groq(monkeypatch)
    join_code, _cohort_id = _make_cohort(instructor_client, username="default_off_effect")
    _join(learner_client, join_code)

    blocked = _mentor_chat(learner_client)
    assert "disabled" in blocked.get_json()["reply"].lower()
    assert called["hit"] is False

    r = _voice(learner_client, "run", code="print('hi')\n")
    assert r.get_json().get("success") is not False


def test_instructor_can_change_class_ai_setting(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="class_toggle")
    _set_class_ai(instructor_client, cohort_id, False)
    cohort = classroom_db.get_cohort(cohort_id)
    assert cohort["ai_enabled"] in (0, False)
    _set_class_ai(instructor_client, cohort_id, True)
    cohort = classroom_db.get_cohort(cohort_id)
    assert bool(cohort["ai_enabled"]) is True


def test_instructor_can_change_individual_learner_ai_setting(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="learner_toggle")
    learner_id = _join(learner_client, join_code)
    _set_learner_ai(instructor_client, cohort_id, learner_id, False)
    learner = classroom_db.get_learner(learner_id)
    assert learner["ai_enabled"] in (0, False)


def test_one_learners_ai_setting_does_not_affect_another(instructor_client, learner_client, monkeypatch):
    called = _mock_groq(monkeypatch)
    other_client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor_client, username="isolation")
    amir_id = _join(learner_client, join_code, name="Amir")
    _join(other_client, join_code, name="Priya")

    _set_class_ai(instructor_client, cohort_id, True)  # isolate the per-learner toggle being tested
    _set_learner_ai(instructor_client, cohort_id, amir_id, False)

    blocked = _mentor_chat(learner_client)
    assert "disabled" in blocked.get_json()["reply"].lower()

    called["hit"] = False
    allowed = _mentor_chat(other_client, message="what does this mean")
    assert allowed.get_json()["success"] is True
    assert called["hit"] is True


def test_unrelated_instructor_cannot_modify_another_cohorts_ai_settings(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="owner_instructor")
    learner_id = _join(learner_client, join_code)
    _set_class_ai(instructor_client, cohort_id, True)  # the real owner's deliberate setting

    other_instructor = app_module.app.test_client()
    other_instructor.post(
        "/classroom/instructor/register",
        data={"username": "rival_instructor", "password": "correct-horse-1", "display_name": "Mr Rival"},
        follow_redirects=True,
    )
    r = other_instructor.post(f"/classroom/cohorts/{cohort_id}/ai-toggle", json={"enabled": False})
    assert r.status_code == 404

    r2 = other_instructor.post(
        f"/classroom/cohorts/{cohort_id}/learners/{learner_id}/ai-toggle", json={"enabled": False},
    )
    assert r2.status_code == 404

    # The real owner's setting is untouched by the failed attempts.
    cohort = classroom_db.get_cohort(cohort_id)
    assert bool(cohort["ai_enabled"]) is True


# ---- student instructor-visibility notice -----------------------------------

_NOTICE_TEXT = (
    "While connected to this class, your instructor can view your current "
    "code and recent program output to help you during lessons."
)


def test_join_form_discloses_instructor_visibility_before_joining():
    """Shown in the join form itself - before the learner submits it, not
    buried in a separate consent screen or only after joining."""
    idx = CLASSROOM_JS.index("function renderJoinPanel(panel, data) {")
    body = CLASSROOM_JS[idx:idx + 3000]
    assert "classroomVisibilityNotice" in body
    assert _NOTICE_TEXT in body
    # It is concise - a single sentence, not a wall of consent-screen text.
    assert _NOTICE_TEXT.count(".") == 1


def test_typed_join_flow_discloses_visibility_before_completing(learner_client):
    """The typed/voice "join <code>" conversational path (Phase 8 parity)
    must disclose the same thing at the same point - right before the
    learner's name completes the join, not after."""
    r = learner_client.post("/voice-command", json={"text": "join ABCDEF"})
    data = r.get_json()
    assert "instructor can view your current code" in data["message"]
    assert "instructor can view your current code" in data["speech"]


def test_notice_is_brief_not_a_consent_screen():
    """One short sentence, not a multi-paragraph disclosure - the actual
    length is what keeps this from becoming "a giant consent screen"."""
    assert len(_NOTICE_TEXT) < 160


# ---- live code view ----------------------------------------------------------

def test_learner_code_reaches_instructor_live_view(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="live_view")
    learner_id = _join(learner_client, join_code)

    learner_client.post("/classroom/live-code/sync", json={"code": "print('hello')\n"})

    r = instructor_client.get(f"/classroom/cohorts/{cohort_id}/learners/{learner_id}/live-code")
    assert r.status_code == 200
    assert "print(&#39;hello&#39;)" in r.get_data(as_text=True) or "print('hello')" in r.get_data(as_text=True)


def test_run_updates_last_run_and_output(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="run_updates")
    learner_id = _join(learner_client, join_code)

    learner_client.post("/classroom/live-code/sync", json={
        "code": "print('done')\n", "ran": True, "output": "done\n", "error": None,
    })

    learner = classroom_db.get_learner(learner_id)
    assert learner["last_run_at"] is not None
    assert learner["last_output"] == "done\n"


def test_instructor_can_select_a_learner_from_the_live_list(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="select_learner")
    learner_id = _join(learner_client, join_code, name="Sara")

    r = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-students")
    data = r.get_json()
    assert data["success"] is True
    ids = [row["id"] for row in data["learners"]]
    assert learner_id in ids
    row = next(row for row in data["learners"] if row["id"] == learner_id)
    assert row["display_name"] == "Sara"
    assert "live_code_url" in row


def test_unjoined_learner_cannot_sync_live_code():
    anon = app_module.app.test_client()
    r = anon.post("/classroom/live-code/sync", json={"code": "print(1)\n"})
    assert r.status_code == 401


def test_unrelated_instructor_cannot_inspect_the_learner(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="owner2")
    learner_id = _join(learner_client, join_code)
    learner_client.post("/classroom/live-code/sync", json={"code": "secret_code = 1\n"})

    other_instructor = app_module.app.test_client()
    other_instructor.post(
        "/classroom/instructor/register",
        data={"username": "rival2", "password": "correct-horse-1", "display_name": "Rival Two"},
        follow_redirects=True,
    )
    r = other_instructor.get(f"/classroom/cohorts/{cohort_id}/learners/{learner_id}/live-code")
    assert r.status_code in (302, 404)
    assert "secret_code" not in r.get_data(as_text=True)

    r2 = other_instructor.get(f"/classroom/cohorts/{cohort_id}/live-students")
    assert r2.status_code == 404


def test_anonymous_ide_usage_never_touches_classroom_ai_gate(monkeypatch):
    """A plain, non-cohort /ide learner (the common case) must never be
    affected by anything in this module."""
    called = _mock_groq(monkeypatch)
    anon = app_module.app.test_client()
    r = anon.post("/mentor/chat", json={"code": "x = 1", "message": "what does this mean", "mode": "general"})
    assert r.get_json()["success"] is True
    assert called["hit"] is True


# ---- live-code sync cadence: ~2s debounce on edits, immediate on Run,  -----
# ---- a separate ~30s heartbeat in between ----------------------------------
#
# There is no JS test runner in this repo (see test_classroom_live_sync.py's
# established convention), so the debounce *timing* is proven at the source
# level - the constant, and that it is a clearTimeout/setTimeout pair (a
# debounce, not a per-keystroke call) - while the resulting *behavior*
# (final code wins, Run is immediate, isolation, authorization) is proven
# end to end against the real Flask routes below.

def test_debounce_interval_is_about_two_seconds():
    assert "const CLASSROOM_SYNC_DEBOUNCE_MS = 2000;" in STATIC_APP


def test_editor_changes_are_debounced_not_sent_per_keystroke():
    """onDidChangeModelContent clears any pending timer before scheduling a
    new one - N rapid keystrokes inside the debounce window collapse into
    at most one sync call, not N."""
    start = STATIC_APP.index("editor.onDidChangeModelContent(() => {")
    block = STATIC_APP[start:start + 900]
    assert "clearTimeout(_classroomSyncDebounce);" in block
    assert "_classroomSyncDebounce = setTimeout(() => {" in block
    assert "window._classroomOnAutosave" in block
    assert "CLASSROOM_SYNC_DEBOUNCE_MS" in block


def test_run_success_and_error_both_sync_immediately_not_via_debounce():
    """Both the success and error run-result hooks call
    window._classroomOnRunResult directly (bypassing the debounce
    entirely) - Run must never wait ~2s to reach the instructor's view."""
    assert STATIC_APP.count("window._classroomOnRunResult(") >= 3


def test_heartbeat_is_separate_from_the_debounced_code_sync():
    """The ~30s interval still exists (AUTOSAVE_INTERVAL_MS, unchanged) but
    now only pings a lightweight heartbeat - the code-sync call it used to
    make was moved to the 2s debounce, not duplicated here."""
    start = STATIC_APP.index("function startAutosave() {")
    body = STATIC_APP[start:start + 1400]
    assert "window._classroomOnHeartbeat" in body
    assert "AUTOSAVE_INTERVAL_MS" in body
    # The interval body itself no longer calls the code-sync hook - only
    # the debounce in onDidChangeModelContent does.
    assert "window._classroomOnAutosave" not in body


def test_classroom_js_heartbeat_hits_the_lightweight_endpoint():
    assert "function onHeartbeat() {" in CLASSROOM_JS
    assert "'/classroom/live-code/heartbeat'" in CLASSROOM_JS
    assert "window._classroomOnHeartbeat = onHeartbeat;" in CLASSROOM_JS


# ---- end-to-end sync behavior against the real routes ----------------------

def test_final_code_snapshot_is_what_gets_synced(instructor_client, learner_client):
    """Simulates what the debounce produces in practice: only the settled,
    final value after a burst of edits is ever sent - never an
    intermediate keystroke-by-keystroke value."""
    join_code, cohort_id = _make_cohort(instructor_client, username="final_snapshot")
    learner_id = _join(learner_client, join_code)

    for partial in ("p", "pr", "pri", "print(", "print('hi", "print('hi')"):
        learner_client.post("/classroom/live-code/sync", json={"code": partial})

    learner = classroom_db.get_learner(learner_id)
    assert learner["current_code"] == "print('hi')"


def test_run_sync_is_immediate_and_carries_output(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="run_immediate")
    learner_id = _join(learner_client, join_code)

    learner_client.post("/classroom/live-code/sync", json={"code": "print('typing...')"})
    learner_client.post("/classroom/live-code/sync", json={
        "code": "print('done')", "ran": True, "output": "done\n",
    })

    learner = classroom_db.get_learner(learner_id)
    assert learner["current_code"] == "print('done')"
    assert learner["last_output"] == "done\n"
    assert learner["last_run_at"] is not None


def test_one_learners_code_sync_cannot_affect_another(instructor_client, learner_client):
    """Rapid syncs from one learner must never be visible on another
    learner's record, even when both are mid-edit at the same time."""
    other_client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor_client, username="code_isolation")
    amir_id = _join(learner_client, join_code, name="Amir")
    priya_id = _join(other_client, join_code, name="Priya")

    learner_client.post("/classroom/live-code/sync", json={"code": "amir_code = 1"})
    other_client.post("/classroom/live-code/sync", json={"code": "priya_code = 2"})
    learner_client.post("/classroom/live-code/sync", json={"code": "amir_code = 'final'"})

    amir = classroom_db.get_learner(amir_id)
    priya = classroom_db.get_learner(priya_id)
    assert amir["current_code"] == "amir_code = 'final'"
    assert priya["current_code"] == "priya_code = 2"


def test_heartbeat_endpoint_requires_a_joined_learner():
    anon = app_module.app.test_client()
    r = anon.post("/classroom/live-code/heartbeat", json={})
    assert r.status_code == 401


def test_heartbeat_touches_presence_without_touching_code(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="heartbeat_only")
    learner_id = _join(learner_client, join_code)
    learner_client.post("/classroom/live-code/sync", json={"code": "print('hi')"})

    learner_client.post("/classroom/live-code/heartbeat", json={})

    learner = classroom_db.get_learner(learner_id)
    assert learner["current_code"] == "print('hi')"  # untouched by the heartbeat
    assert learner["heartbeat_at"] is not None


def test_instructor_authorization_still_enforced_for_live_code_view(instructor_client, learner_client):
    """Re-affirms cross-instructor isolation specifically in the context of
    the faster sync cadence - a quicker sync must not mean a weaker gate."""
    join_code, cohort_id = _make_cohort(instructor_client, username="auth_with_fast_sync")
    learner_id = _join(learner_client, join_code)
    learner_client.post("/classroom/live-code/sync", json={"code": "top_secret = True"})

    rival = app_module.app.test_client()
    rival.post(
        "/classroom/instructor/register",
        data={"username": "rival_fast_sync", "password": "correct-horse-1", "display_name": "Rival"},
        follow_redirects=True,
    )
    r = rival.get(f"/classroom/cohorts/{cohort_id}/learners/{learner_id}/live-code")
    assert r.status_code in (302, 404)
    assert "top_secret" not in r.get_data(as_text=True)


# ---- normal instructor navigation: sign in -> create/choose cohort -> ------
# ---- live student dashboard -> select learner -> inspect code/output -------

def test_creating_a_cohort_lands_on_the_live_dashboard_not_the_legacy_one(instructor_client):
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": "front_door", "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": "Python Beginners"}, follow_redirects=True)
    assert r.status_code == 200
    assert b"Class details" in r.data  # cohort_live_dashboard.html's own heading
    assert b"AI assistance for class" in r.data
    assert b"Join code:" in r.data


def test_cohort_list_links_to_the_live_dashboard(instructor_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="front_door_list")
    r = instructor_client.get("/classroom/instructor")
    assert f'/classroom/cohorts/{cohort_id}/live"'.encode() in r.data


def test_full_instructor_navigation_flow_reaches_a_selected_learners_code(instructor_client, learner_client):
    """Sign in -> create cohort -> land on live dashboard -> select learner
    -> inspect current code/output, without ever being redirected through
    the legacy assignments/reports dashboard."""
    r = instructor_client.post(
        "/classroom/instructor/register",
        data={"username": "full_flow", "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": "Python Beginners"}, follow_redirects=True)
    cohort_id = int(_extract(rb'cohorts/(\d+)/ai-toggle"', r.data))
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    assert b"Students (0)" in r.data

    learner_id = _join(learner_client, join_code, name="Sara")
    learner_client.post("/classroom/live-code/sync", json={"code": "print('hi')", "ran": True, "output": "hi\n"})

    live = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live")
    assert b"Sara" in live.data
    assert b"Students (1)" in live.data

    code_view = instructor_client.get(f"/classroom/cohorts/{cohort_id}/learners/{learner_id}/live-code")
    assert b"print(" in code_view.data
    assert b"hi" in code_view.data


def test_legacy_dashboard_is_dormant_and_not_linked_from_the_live_flow(instructor_client):
    """The old route remains compatible but is absent from the normal flow."""
    join_code, cohort_id = _make_cohort(instructor_client, username="legacy_reachable")
    legacy = instructor_client.get(f"/classroom/cohorts/{cohort_id}")
    assert legacy.status_code == 200
    assert b"Cohort at a glance" in legacy.data  # the old rich dashboard's own heading

    live = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live")
    assert b"Classic dashboard" not in live.data


def test_live_dashboard_never_shows_assignments_reports_or_lms_chrome(instructor_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="no_lms_surface")
    r = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live")
    low = r.data.lower()
    for banned in (b"assignment", b"help queue", b"guided project", b"due date", b"report"):
        assert banned not in low
