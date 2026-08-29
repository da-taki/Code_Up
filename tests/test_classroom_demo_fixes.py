"""Regression tests for the pre-Incluva-demo classroom command fixes
(feature/demo-classroom-command-fixes):

1. "join <code>" now wins over the "awaiting program input" stdin capture
   in /voice-command, regardless of match order in the pipeline.
2. Opening an assignment reads its title + instructions once (client-side;
   checked via source-level assertions on static/classroom.js, following
   the established pattern in test_ide_accessibility_semantics.py and
   test_classroom_live_sync.py - this repo has no JS test runner).
3. "what should I do?" (and friends) prioritizes an actively-open
   assignment over the general classroom-priority logic.
4. Natural submission phrasing all reaches the same
   learner_actions.submit_current_assignment path.
5. "Back to CodeUp"/"back to ide" after a successful submission.
6. Lightweight instructor-side near-live sync (new
   GET /classroom/cohorts/<id>/live-summary + static/instructor-sync.js).
"""

import re

import pytest

import app as app_module
from codeup.classroom import ide_commands


CLASSROOM_JS = open("static/classroom.js", encoding="utf-8").read()
INSTRUCTOR_SYNC_JS = open("static/instructor-sync.js", encoding="utf-8").read()
APP_PY = open("app.py", encoding="utf-8").read()


def _fn_body(src, name):
    m = re.search(r"function " + re.escape(name) + r"\s*\([^)]*\)\s*\{", src)
    assert m, f"function {name} not found"
    start = m.end()
    depth = 1
    i = start
    while depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[start:i]


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


def _make_cohort(instructor_client, name="Python Beginners", username="demofix_instr"):
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": username, "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": name}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    return join_code, cohort_id


def _publish_assignment(instructor_client, cohort_id, title="Student Marks Program",
                         instructions="Store marks for at least three subjects and calculate the average mark. Print the final average.",
                         ai_policy="FULL"):
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": title, "instructions": instructions, "starter_code": "", "ai_policy": ai_policy},
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor_client.post(f"/classroom/assignments/{assignment_id}/publish")
    return assignment_id


def _voice(client, text, **body):
    return client.post("/voice-command", json={"text": text, **body})


# ================================================================
# 1. JOIN
# ================================================================

JOIN_VARIANTS = [
    "Join P5SQBZ", "join p5sqbz", "Join p5SqBz", "join ABC123",
    "join class P5SQBZ", "class code P5SQBZ", "join cohort P5SQBZ",
]


@pytest.mark.parametrize("phrase", JOIN_VARIANTS)
def test_join_phrase_variants_match_deterministically(phrase):
    matched = ide_commands.match(phrase)
    assert matched is not None, f"{phrase!r} did not match the classroom parser at all"
    intent, slots = matched
    assert intent == "join_with_code"
    assert slots["code"] in {"P5SQBZ", "ABC123"}


def test_join_wins_over_awaiting_program_input(instructor_client, learner_client):
    """The bug: /voice-command checked _handle_awaiting_program_input()
    before the classroom command short-circuit, so any text typed while a
    program was mid-input() - including an unambiguous "join <code>" -
    got captured as stdin and silently re-ran the program instead of
    joining. Reproduces the exact session state and asserts the fix."""
    join_code, _cohort_id = _make_cohort(instructor_client, username="demofix_awaitorder")
    learner_client.get("/ide")
    # Establish a session, then seed "awaiting program input" state exactly
    # as the runtime would after a program calls input().
    learner_client.post("/voice-command", json={"text": "go to top"},
                         headers={"Origin": "http://localhost", "Referer": "http://localhost/ide"})
    from codeup.runtime import session_memory
    session_id = app_module._verify_session_id(learner_client.get_cookie("codeup_session").value)
    storage = app_module._session_traces[session_id]
    mem = session_memory.get_memory(storage)
    code = 'x = input("Enter x: ")'
    session_memory.set_awaiting_program_input(
        mem, code_hash=app_module._code_hash(code),
        prompts=[{"prompt": "Enter x: ", "expected_type": "text"}],
    )

    r = learner_client.post(
        "/voice-command", json={"text": f"Join {join_code}", "code": code},
        headers={"Origin": "http://localhost", "Referer": "http://localhost/ide"},
    )
    data = r.get_json()
    assert data["action"] != "action_sequence", f"join was swallowed as program input: {data}"
    assert "input_concierge" not in data
    assert "name" in data["message"].lower()  # "What name should I use?"


def test_awaiting_program_input_still_works_for_a_normal_answer(instructor_client, learner_client):
    """The reordering must not break the ordinary case: a genuine answer to
    a pending input() prompt (not a classroom-command-shaped phrase) is
    still captured as program input exactly as before."""
    learner_client.get("/ide")
    learner_client.post("/voice-command", json={"text": "go to top"},
                         headers={"Origin": "http://localhost", "Referer": "http://localhost/ide"})
    from codeup.runtime import session_memory
    session_id = app_module._verify_session_id(learner_client.get_cookie("codeup_session").value)
    storage = app_module._session_traces[session_id]
    mem = session_memory.get_memory(storage)
    code = 'x = input("Enter x: ")'
    session_memory.set_awaiting_program_input(
        mem, code_hash=app_module._code_hash(code),
        prompts=[{"prompt": "Enter x: ", "expected_type": "text"}],
    )
    r = learner_client.post(
        "/voice-command", json={"text": "42", "code": code},
        headers={"Origin": "http://localhost", "Referer": "http://localhost/ide"},
    )
    data = r.get_json()
    assert data["action"] == "action_sequence"
    assert data.get("input_concierge") is True


def test_invalid_join_code_gets_classroom_specific_response(learner_client):
    learner_client.get("/ide")
    r = _voice(learner_client, "join ZZZZZZ", join_name="Amir")
    data = r.get_json()
    assert data["success"] is True
    assert "class" in data["message"].lower() or "code" in data["message"].lower()
    assert "programming" not in data["message"].lower()


def test_join_command_never_reaches_groq(instructor_client, learner_client, monkeypatch):
    called = {"groq": False}
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: called.__setitem__("groq", True) or "should not run")
    join_code, _cohort_id = _make_cohort(instructor_client, username="demofix_nogroq")
    learner_client.get("/ide")
    r = _voice(learner_client, f"Join {join_code}", join_name="Amir")
    assert r.get_json()["success"] is True
    assert called["groq"] is False


def test_classroom_check_precedes_awaiting_input_in_source():
    """Structural pin on the fix: the classroom short-circuit's call site
    must appear before _handle_awaiting_program_input's call site in
    app.py's voice() view, not after."""
    classroom_idx = APP_PY.index("classroom_result = _classroom_command_response(")
    awaiting_idx = APP_PY.index("awaiting_response = _handle_awaiting_program_input(")
    assert classroom_idx < awaiting_idx


# ================================================================
# 2. ASSIGNMENT AUTO-READ
# ================================================================

def test_assignment_panel_announces_title_and_instructions_once():
    body = _fn_body(CLASSROOM_JS, "renderAssignmentPanel")
    assert "announce(a.title + '. ' + (a.instructions || 'No instructions were given for this assignment.'));" in body
    # Exactly one call shaped like that - not duplicated anywhere in the function.
    assert body.count("a.title + '. '") == 1


def test_assignment_panel_only_renders_once_per_page_load():
    """renderAssignmentPanel must have exactly one call site (inside
    fetchContextAndRender's single mode==='assignment' branch) so its
    announce() can never fire twice from polling or an unrelated update -
    background classroom sync explicitly skips this panel (see
    test_classroom_live_sync.test_background_sync_never_replaces_active_item_panel)."""
    assert CLASSROOM_JS.count("renderAssignmentPanel(") == 2  # the definition + its one call site


def test_assignment_context_endpoint_carries_instructions_for_the_client_to_read(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="demofix_readassign")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assignment_id = _publish_assignment(instructor_client, cohort_id)
    ctx = learner_client.get(f"/classroom/assignments/{assignment_id}/context").get_json()
    assert ctx["assignment"]["title"] == "Student Marks Program"
    assert "average" in ctx["assignment"]["instructions"].lower()


# ================================================================
# 3. "WHAT SHOULD I DO?" WHILE AN ASSIGNMENT IS OPEN
# ================================================================

WHAT_SHOULD_I_DO_ALIASES = [
    "what should I do", "what should I do?", "what am I supposed to do",
    "what do I need to do", "repeat the assignment", "repeat the instructions",
    "read this assignment",
]


@pytest.mark.parametrize("phrase", WHAT_SHOULD_I_DO_ALIASES)
def test_active_assignment_priority_over_global_logic(instructor_client, learner_client, phrase):
    join_code, cohort_id = _make_cohort(instructor_client, username=f"demofix_wsid_{abs(hash(phrase)) % 10000}")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assignment_id = _publish_assignment(instructor_client, cohort_id)
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")  # sets the assignment cookie

    r = _voice(learner_client, phrase)
    data = r.get_json()
    assert "Student Marks Program" in data["message"]
    assert "average" in data["message"].lower()


def test_what_should_i_do_without_open_assignment_keeps_global_behavior(instructor_client, learner_client):
    """No assignment cookie set (learner never opened one) - falls back to
    the pre-existing classroom-wide priority logic, unchanged."""
    join_code, cohort_id = _make_cohort(instructor_client, username="demofix_wsid_global")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    _publish_assignment(instructor_client, cohort_id, title="Global Priority Assignment")

    r = _voice(learner_client, "what should I do")
    assert "Global Priority Assignment" in r.get_json()["message"]


# ================================================================
# 4. NATURAL SUBMISSION COMMANDS
# ================================================================

SUBMIT_PHRASES = [
    "submit", "submit this", "submit assignment", "submit my assignment",
    "submit this assignment", "submit code", "submit my code",
    "submit program", "submit my program", "turn this in", "turn in assignment",
]


@pytest.mark.parametrize("phrase", SUBMIT_PHRASES)
def test_submit_phrase_matches_submit_assignment_intent(phrase):
    matched = ide_commands.match(phrase)
    assert matched is not None, f"{phrase!r} did not match any classroom intent"
    assert matched[0] == "submit_assignment"


def test_submit_phrases_all_use_the_same_authoritative_path(instructor_client, learner_client):
    """Every variant reaches learner_actions.submit_current_assignment - not
    a duplicated implementation - proven by each producing the same
    persisted 'submitted' status via the same context endpoint."""
    join_code, cohort_id = _make_cohort(instructor_client, username="demofix_submitpath")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assignment_id = _publish_assignment(instructor_client, cohort_id)
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")

    r = _voice(learner_client, "turn this in", code="marks = {'Amir': 88}\n")
    data = r.get_json()
    assert data["success"] is True
    assert "submitted successfully" in data["message"].lower()
    assert data.get("assignment_submitted") is True

    ctx = learner_client.get(f"/classroom/assignments/{assignment_id}/context").get_json()
    assert ctx["progress"]["status"] == "submitted"


def test_submit_with_no_active_assignment_gives_clear_classroom_message(instructor_client, learner_client):
    join_code, _cohort_id = _make_cohort(instructor_client, username="demofix_nosubmit")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    r = _voice(learner_client, "submit program")
    data = r.get_json()
    assert data["success"] is True
    assert "don't have an assignment open to submit" in data["message"].lower()
    assert "programming" not in data["message"].lower()


# ================================================================
# 5. BACK TO CODEUP / IDE
# ================================================================

BACK_TO_IDE_PHRASES = ["back to ide", "return to ide", "go back to ide", "back to codeup", "return to codeup"]


@pytest.mark.parametrize("phrase", BACK_TO_IDE_PHRASES)
def test_back_to_ide_phrases_navigate(phrase):
    matched = ide_commands.match(phrase)
    assert matched is not None
    intent, slots = matched
    assert intent == "back_to_ide"
    response = ide_commands.handle(intent, slots, {"learner": None, "summary": None})
    assert response["action"] == "navigate"
    assert response["url"] == "/ide"


def test_back_to_ide_does_not_require_a_learner(learner_client):
    """Works even for an anonymous session - never gates on classroom
    membership, since it's plain navigation."""
    learner_client.get("/ide")
    r = _voice(learner_client, "back to ide")
    data = r.get_json()
    assert data["action"] == "navigate"
    assert data["url"] == "/ide"


def test_back_to_ide_link_revealed_on_successful_submission():
    body = _fn_body(CLASSROOM_JS, "submitAssignment")
    idx = body.index("statusEl.textContent = 'Assignment submitted.'")
    window = body[idx:idx + 300]
    assert "classroomBackToIdeLink" in window
    assert "backLink.hidden = false" in window


def test_back_to_ide_link_also_revealed_for_typed_submit_command():
    """A "submit" typed/spoken command doesn't go through the Submit
    button's own click handler - app.js's generic response post-processing
    must reveal the same link via the assignment_submitted flag."""
    with open("static/app.js", encoding="utf-8") as fh:
        app_js = fh.read()
    assert "payload.assignment_submitted" in app_js
    assert "classroomBackToIdeLink" in app_js


def test_submission_confirmation_speech_is_not_cut_off_by_navigation():
    """submitAssignment() must never assign window.location - only reveal
    the Back link - so the spoken confirmation is never interrupted by an
    automatic navigation timer."""
    body = _fn_body(CLASSROOM_JS, "submitAssignment")
    assert "location" not in body
    assert "setTimeout" not in body


def test_returning_to_ide_preserves_classroom_membership_and_submission(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="demofix_returnpreserve")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assignment_id = _publish_assignment(instructor_client, cohort_id)
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")
    learner_client.post(f"/classroom/assignments/{assignment_id}/submit", json={"code": "marks = {}\n"})

    r = _voice(learner_client, "back to ide")
    assert r.get_json()["url"] == "/ide"

    summary = learner_client.get("/classroom/ide/summary").get_json()
    assert summary["joined"] is True
    assert summary["assignments"][0]["state"] == "submitted"


# ================================================================
# 6. INSTRUCTOR LIVE SYNC
# ================================================================

def test_live_summary_endpoint_requires_instructor_auth():
    client = app_module.app.test_client()
    r = client.get("/classroom/cohorts/1/live-summary")
    assert r.status_code in (302, 401, 403, 404)


def test_live_summary_rejects_another_instructors_cohort(instructor_client):
    other = app_module.app.test_client()
    _join_code, cohort_id = _make_cohort(instructor_client, name="Cohort A", username="demofix_owner")
    other.post("/classroom/instructor/register",
                data={"username": "demofix_intruder", "password": "correct-horse-1", "display_name": "X"},
                follow_redirects=True)
    r = other.get(f"/classroom/cohorts/{cohort_id}/live-summary")
    assert r.status_code == 404


def test_live_summary_reflects_join_submission_and_help(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="demofix_livesummary")

    before = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()
    assert before["success"] is True
    assert before["learner_count"] == 0
    assert before["open_help_count"] == 0

    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    after_join = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()
    assert after_join["learner_count"] == 1
    assert after_join["learners"][0]["display_name"] == "Amir"

    assignment_id = _publish_assignment(instructor_client, cohort_id)
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")
    learner_client.post(f"/classroom/assignments/{assignment_id}/submit", json={"code": "marks = {}\n"})
    after_submit = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()
    assert after_submit["learners"][0]["assignments_submitted"] == 1

    _voice(learner_client, "I need help")
    after_help = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()
    assert after_help["open_help_count"] == 1


def test_live_summary_reuses_same_fields_as_the_rendered_dashboard(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="demofix_fieldparity")
    r = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-summary")
    assert r.status_code == 200
    assert r.get_json()["success"] is True


def test_instructor_sync_script_included_on_cohort_dashboard(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="demofix_scripttag")
    html = instructor_client.get(f"/classroom/cohorts/{cohort_id}").get_data(as_text=True)
    assert 'src="/static/instructor-sync.js"' in html
    assert f'data-cohort-id="{cohort_id}"' in html
    # Stable ids the script targets must exist in the server-rendered page.
    assert 'id="learnersHeading"' in html
    assert 'id="learnersTableWrap"' in html
    assert 'id="helpQueueLink"' in html


def test_instructor_sync_single_polling_loop_and_in_flight_guard():
    assert "var POLL_INTERVAL_MS = 7000;" in INSTRUCTOR_SYNC_JS
    assert INSTRUCTOR_SYNC_JS.count("setInterval(") == 1
    body = _fn_body(INSTRUCTOR_SYNC_JS, "requestSync")
    assert "state.inFlight" in body
    assert "document.hidden || state.inFlight" in body


def test_instructor_sync_pauses_while_hidden_and_resumes_on_visibility():
    assert "document.addEventListener('visibilitychange'" in INSTRUCTOR_SYNC_JS
    assert re.search(r"if \(document\.hidden\) \{\s*if \(state\.timer\) \{ clearInterval\(state\.timer\)", INSTRUCTOR_SYNC_JS)
    assert "window.addEventListener('focus'" in INSTRUCTOR_SYNC_JS


def test_instructor_sync_never_touches_the_assignment_form():
    """The polling patch functions must only reference the learner-table
    and help-queue ids - never the "Create an assignment" form's field ids
    - so a poll can never discard an instructor's in-progress typed draft."""
    for fn in ("reconcileLearnersTable", "patchHelpQueueLink", "applySync"):
        body = _fn_body(INSTRUCTOR_SYNC_JS, fn)
        assert "a_title" not in body
        assert "a_instructions" not in body
        assert "a_starter" not in body


def test_instructor_sync_only_patches_on_meaningful_change():
    body = _fn_body(INSTRUCTOR_SYNC_JS, "patchHelpQueueLink")
    assert "state.lastOpenHelpCount === data.open_help_count" in body
    heading_body = _fn_body(INSTRUCTOR_SYNC_JS, "reconcileLearnersTable")
    assert "state.lastLearnerCount !== data.learner_count" in heading_body


def test_instructor_sync_reuses_existing_live_region_no_new_one():
    """Post-demo hardening adds meaningful-event announcements (section E),
    but they must reuse the single existing #srAnnouncer already on every
    classroom page - never a second live region, never a TTS/speak engine
    (this page has none)."""
    assert "getElementById('srAnnouncer')" in INSTRUCTOR_SYNC_JS
    assert "speak(" not in INSTRUCTOR_SYNC_JS
    assert not re.search(r"setAttribute\(\s*['\"]aria-live['\"]", INSTRUCTOR_SYNC_JS)
    assert INSTRUCTOR_SYNC_JS.count("getElementById('srAnnouncer')") == 1


def test_instructor_sync_network_failure_is_silent():
    body = _fn_body(INSTRUCTOR_SYNC_JS, "requestSync")
    catch_block = body.split(".catch(function () {")[1]
    assert "innerHTML" not in catch_block
    assert "alert(" not in catch_block
    assert "state.inFlight = false;" in catch_block
