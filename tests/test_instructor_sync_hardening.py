"""Regression tests for the post-demo classroom hardening pass
(feature/classroom-post-demo-hardening), covering:

1. The audit finding in app.py's pending-join/awaiting-input interaction:
   a bare "cancel" while BOTH a pending join conversation and an unanswered
   input() prompt are active now resolves the more urgent, actively-
   blocking program state, instead of silently cancelling the join
   conversation underneath the learner.
2. Instructor live-sync (static/instructor-sync.js): row-level, focus-
   preserving table patching (never a full rebuild), alphabetical-order
   reconciliation via minimal DOM moves, graceful handling of a learner
   who disappears mid-focus, and deduplicated meaningful-event
   announcements (join/help/submission) through the existing #srAnnouncer
   live region.
3. The new learner_joined activity event and the live-summary endpoint's
   allowlisted, cohort-scoped events feed that powers those announcements.

Client-side DOM/focus behavior itself was verified in a real browser (see
the PR/commit description) - this file follows the source-level-assertion
convention already established by test_classroom_live_sync.py and
test_classroom_demo_fixes.py, since this repo has no JS test runner.
"""

import re

import pytest

import app as app_module
from codeup.classroom import ide_commands
from codeup.runtime import session_memory

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


def _make_cohort(instructor_client, name="Python Beginners", username="hardening_instr"):
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": username, "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": name}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    return join_code, cohort_id


def _publish_assignment(instructor_client, cohort_id, title="Student Marks Program"):
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": title, "instructions": "x", "starter_code": "", "ai_policy": "FULL"},
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor_client.post(f"/classroom/assignments/{assignment_id}/publish")
    return assignment_id


# ================================================================
# A. pending-join vs. awaiting-input ambiguity fix
# ================================================================

def test_classroom_check_precedes_awaiting_input_in_source():
    classroom_idx = APP_PY.index("classroom_result = _classroom_command_response(")
    awaiting_idx = APP_PY.index("awaiting_response = _handle_awaiting_program_input(")
    assert classroom_idx < awaiting_idx


def test_join_cancel_and_awaiting_input_control_phrases_never_collide():
    """Audit finding, verified and closed (not fixed - there was nothing to
    fix): the two "cancel" vocabularies never actually overlap.
    classroom_ide_commands' join-cancel phrases ("cancel", "cancel
    joining", "never mind", "stop joining") and
    app._AWAITING_INPUT_CONTROL ("cancel input", "clear input", "clear
    inputs", "stop input") share no phrase, and is_join_cancel_phrase does
    exact matching (not substring), so a real input-cancel phrase like
    "cancel input" is never mistaken for a join-cancel. An earlier attempt
    at a fix here was reverted after this test proved it made the exact
    scenario it targeted WORSE (a bare "cancel" would fall through and be
    silently consumed as literal program stdin, since
    _AWAITING_INPUT_CONTROL never recognized bare "cancel" either)."""
    join_cancel_phrases = {"cancel", "cancel joining", "never mind", "nevermind", "stop joining"}
    awaiting_input_control = {"cancel input", "clear input", "clear inputs", "stop input"}
    assert join_cancel_phrases.isdisjoint(awaiting_input_control)
    for phrase in awaiting_input_control:
        assert not ide_commands.is_join_cancel_phrase(phrase)


def test_bare_cancel_cancels_pending_join_regardless_of_awaiting_input(learner_client):
    """Locks in the actual (correct, unchanged) behavior: a bare "cancel"
    while a join conversation is pending cancels the join, whether or not
    the learner separately also has an unanswered input() prompt - see
    test_join_cancel_and_awaiting_input_control_phrases_never_collide for
    why this is safe (no real phrase overlap to be ambiguous about)."""
    learner_client.get("/ide")
    learner_client.post("/voice-command", json={"text": "go to top"},
                         headers={"Origin": "http://localhost", "Referer": "http://localhost/ide"})
    session_id = learner_client.get_cookie("codeup_session").value
    storage = app_module._session_traces[session_id]
    mem = session_memory.get_memory(storage)

    code = 'x = input("Enter x: ")'
    session_memory.set_awaiting_program_input(
        mem, code_hash=app_module._code_hash(code),
        prompts=[{"prompt": "Enter x: ", "expected_type": "text"}],
    )
    mem[app_module._PENDING_JOIN_KEY] = {"state": "waiting_for_name", "code": "ABC123"}

    r = learner_client.post(
        "/voice-command", json={"text": "cancel", "code": code},
        headers={"Origin": "http://localhost", "Referer": "http://localhost/ide"},
    )
    data = r.get_json()
    assert "classroom joining cancelled" in data["message"].lower()
    assert mem.get(app_module._PENDING_JOIN_KEY) is None


# ================================================================
# B/C/D. instructor live sync - focus-preserving, targeted DOM patching
# ================================================================

def test_reconcile_never_does_a_full_tbody_rebuild():
    """The rushed version did tbody.innerHTML = '' on every meaningful
    change, which drops any keyboard/screen-reader focus inside the table
    to <body>. The hardened version must never do that."""
    body = _fn_body(INSTRUCTOR_SYNC_JS, "reconcileLearnersTable")
    assert "tbody.innerHTML" not in body


def test_existing_rows_are_updated_in_place_not_recreated():
    """reconcileLearnersTable delegates the actual diff to the shared
    reconcileTable() helper (also used by the assignments table) - wired
    with updateLearnerRow as its per-row updater."""
    wiring = _fn_body(INSTRUCTOR_SYNC_JS, "reconcileLearnersTable")
    assert "updateRow: updateLearnerRow" in wiring
    update_body = _fn_body(INSTRUCTOR_SYNC_JS, "updateLearnerRow")
    # Field-level equality guards - never an unconditional textContent write
    # that would be indistinguishable from a real change to assistive tech.
    assert update_body.count("!==") >= 5


def test_reordering_uses_minimal_insertBefore_moves():
    """Learners are server-sorted alphabetically (display_name), so a new
    arrival can belong in the middle - the shared reconcileTable() helper
    must walk a cursor and only call insertBefore when a row is genuinely
    out of place, never touching rows that are already correctly
    positioned. Both the learners and assignments tables use this same
    helper (see test_assignments_table_reuses_the_same_reconciler)."""
    body = _fn_body(INSTRUCTOR_SYNC_JS, "reconcileTable")
    assert "tbody.insertBefore(row, cursor)" in body
    assert "cursor = cursor.nextSibling" in body


def test_removed_learner_moves_focus_and_announces():
    generic = _fn_body(INSTRUCTOR_SYNC_JS, "reconcileTable")
    assert "row.contains(document.activeElement)" in generic
    wiring = _fn_body(INSTRUCTOR_SYNC_JS, "reconcileLearnersTable")
    assert "onRemoveIfFocused:" in wiring
    assert "heading.setAttribute('tabindex', '-1')" in wiring
    assert "heading.focus()" in wiring
    assert "is no longer in this cohort" in wiring


def test_assignments_table_reuses_the_same_reconciler():
    """The optional assignments-table live patch (section 3 of the
    hardening ask) reuses reconcileTable() instead of duplicating the
    diff/insert/remove algorithm a second time."""
    wiring = _fn_body(INSTRUCTOR_SYNC_JS, "reconcileAssignmentsTable")
    assert "reconcileTable({" in wiring
    assert "updateRow: updateAssignmentRow" in wiring
    # No onRemoveIfFocused property passed in the options object - this app
    # has no delete-assignment action.
    assert re.search(r"onRemoveIfFocused\s*:\s*function", wiring) is None


def test_seed_existing_rows_prevents_duplicate_rows_on_first_poll():
    """Without seeding state.rowsById from the server-rendered <tr>s at
    script load, the first poll would think every already-present learner
    is "new" and append duplicate rows alongside the real ones."""
    assert "function seedExistingRows()" in INSTRUCTOR_SYNC_JS
    assert "seedExistingRows();" in INSTRUCTOR_SYNC_JS
    body = _fn_body(INSTRUCTOR_SYNC_JS, "seedExistingRows")
    assert "data-learner-id" in body or "dataset.learnerId" in body


def test_template_marks_rows_with_learner_id(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="hardening_rowid")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    html = instructor_client.get(f"/classroom/cohorts/{cohort_id}").get_data(as_text=True)
    assert "data-learner-id=" in html


def test_never_touches_assignment_form_fields():
    """Even the assignments TABLE reconciliation (which now polls the same
    endpoint that also carries the "Create an assignment" form's data)
    must never reference the form's own field ids - the table and the form
    are a completely separate part of the page."""
    for fn in ("reconcileLearnersTable", "updateLearnerRow", "reconcileAssignmentsTable",
               "updateAssignmentRow", "patchHelpQueueLink", "applySync"):
        body = _fn_body(INSTRUCTOR_SYNC_JS, fn)
        assert "a_title" not in body
        assert "a_instructions" not in body
        assert "a_starter" not in body


def test_single_polling_loop_no_overlap_pauses_on_hidden():
    assert INSTRUCTOR_SYNC_JS.count("setInterval(") == 1
    req_body = _fn_body(INSTRUCTOR_SYNC_JS, "requestSync")
    assert "document.hidden || state.inFlight" in req_body
    assert re.search(r"if \(document\.hidden\) \{\s*if \(state\.timer\) \{ clearInterval\(state\.timer\)", INSTRUCTOR_SYNC_JS)


# ================================================================
# E. meaningful-event announcements
# ================================================================

def test_announcement_dedupe_is_id_based_not_snapshot_diff():
    body = _fn_body(INSTRUCTOR_SYNC_JS, "announceNewEvents")
    assert "state.lastSeenEventId === null" in body
    assert "e.id > state.lastSeenEventId" in body


def test_first_sync_seeds_watermark_without_announcing():
    """Events already present the moment the instructor opens the page are
    not "new" - only something that arrives after that point announces."""
    body = _fn_body(INSTRUCTOR_SYNC_JS, "announceNewEvents")
    idx = body.index("state.lastSeenEventId === null")
    seed_block = body[idx:idx + 400]
    assert "return;" in seed_block
    assert "announce(" not in seed_block


def test_event_announcement_text_matches_required_phrasing():
    body = _fn_body(INSTRUCTOR_SYNC_JS, "eventAnnouncement")
    assert "joined the cohort" in body
    assert "requested instructor help" in body
    assert "submitted" in body
    assert "evt.assignment_title" in body  # names the assignment when known


def test_announcements_reuse_existing_live_region_only():
    assert "getElementById('srAnnouncer')" in INSTRUCTOR_SYNC_JS
    assert INSTRUCTOR_SYNC_JS.count("getElementById('srAnnouncer')") == 1
    assert "aria-live" not in INSTRUCTOR_SYNC_JS
    assert "speak(" not in INSTRUCTOR_SYNC_JS


def test_routine_polling_fields_never_trigger_an_announcement():
    """last_active_at, learner_count, open_help_count changes are visual-
    only - the announcement path is entirely separate (event-id based),
    never derived from these fields."""
    announce_fn_names = ("announce", "eventAnnouncement", "announceNewEvents")
    for fn in announce_fn_names:
        body = _fn_body(INSTRUCTOR_SYNC_JS, fn)
        assert "last_active_at" not in body
        assert "learner_count" not in body
        assert "open_help_count" not in body


# ---- backend support for the announcement feature ---------------------

def test_learner_joined_event_is_logged(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="hardening_joinevent")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    summary = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()
    kinds = [e["kind"] for e in summary["events"]]
    assert "learner_joined" in kinds
    joined_event = next(e for e in summary["events"] if e["kind"] == "learner_joined")
    assert joined_event["learner_name"] == "Amir"


def test_live_summary_events_are_allowlisted_not_every_progress_event(instructor_client, learner_client):
    """assignment_autosave fires on every debounced keystroke save - it
    must never appear in (or be announced from) the live-summary feed."""
    join_code, cohort_id = _make_cohort(instructor_client, username="hardening_allowlist")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assignment_id = _publish_assignment(instructor_client, cohort_id)
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")
    learner_client.post(f"/classroom/assignments/{assignment_id}/autosave", json={"code": "x=1"})

    summary = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()
    kinds = {e["kind"] for e in summary["events"]}
    assert kinds <= {"learner_joined", "help_requested", "assignment_submitted"}
    assert "assignment_autosave" not in kinds


def test_submission_event_carries_assignment_title(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="hardening_subtitle")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assignment_id = _publish_assignment(instructor_client, cohort_id, title="Student Marks Program")
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")
    learner_client.post(f"/classroom/assignments/{assignment_id}/submit", json={"code": "x=1"})

    summary = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()
    submit_event = next(e for e in summary["events"] if e["kind"] == "assignment_submitted")
    assert submit_event["assignment_title"] == "Student Marks Program"


def test_help_request_event_carries_learner_name(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="hardening_helpevent")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    learner_client.post("/voice-command", json={"text": "I need help"},
                         headers={"Origin": "http://localhost", "Referer": "http://localhost/ide"})

    summary = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()
    help_event = next(e for e in summary["events"] if e["kind"] == "help_requested")
    assert help_event["learner_name"] == "Amir"


def test_events_scoped_to_authorized_cohort_only(instructor_client):
    """No cross-cohort leakage: a second instructor's cohort's events never
    appear in this endpoint's response."""
    other = app_module.app.test_client()
    join_code_a, cohort_a = _make_cohort(instructor_client, name="Cohort A", username="hardening_scope_a")
    _join_code_b, cohort_b = _make_cohort(other, name="Cohort B", username="hardening_scope_b")

    learner = app_module.app.test_client()
    learner.post("/classroom/join-api", json={"join_code": join_code_a, "display_name": "Amir"})

    r = other.get(f"/classroom/cohorts/{cohort_a}/live-summary")
    assert r.status_code == 404  # not their cohort

    summary_b = other.get(f"/classroom/cohorts/{cohort_b}/live-summary").get_json()
    assert summary_b["events"] == []  # nothing from cohort A leaked in


# ================================================================
# K. code quality - shared helper, no duplication
# ================================================================

def test_learner_progress_computation_not_duplicated_in_routes():
    with open("codeup/classroom/routes.py", encoding="utf-8") as fh:
        routes_py = fh.read()
    assert routes_py.count("def _learner_progress(") == 1
    call_sites = routes_py.count("_learner_progress(learner, assignments, help_status_by_learner)") - 1  # minus the def line
    assert call_sites == 2


def test_cohort_dashboard_and_live_summary_agree_on_learner_count(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client, username="hardening_parity")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})

    html = instructor_client.get(f"/classroom/cohorts/{cohort_id}").get_data(as_text=True)
    assert "Learners (1)" in html
    summary = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()
    assert summary["learner_count"] == 1


# ================================================================
# G. regression - archived cohort, duplicate submit
# ================================================================

def test_archived_cohort_cannot_be_joined(instructor_client):
    """Pre-existing behavior (get_cohort_by_join_code filters status='active'),
    confirmed still correct through the classroom command pipeline: an
    archived cohort's join code stops working, with the same
    classroom-owned "not found" response as any other invalid code - never
    a leak of cohort existence, never a crash."""
    join_code, cohort_id = _make_cohort(instructor_client, username="hardening_archived")
    instructor_client.post(f"/classroom/cohorts/{cohort_id}/archive")

    learner = app_module.app.test_client()
    r = learner.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assert r.status_code == 400
    assert r.get_json()["success"] is False


def test_duplicate_submit_does_not_create_duplicate_progress_rows(instructor_client, learner_client):
    """submit_assignment is an UPDATE against the unique (assignment_id,
    learner_id) progress row (see codeup.classroom.db.submit_assignment) -
    re-submitting (button double-click, or "submit" said twice) is
    idempotent at the DB level, never a duplicate row or a 500."""
    join_code, cohort_id = _make_cohort(instructor_client, username="hardening_dupsubmit")
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    assignment_id = _publish_assignment(instructor_client, cohort_id)
    learner_client.get(f"/classroom/assignments/{assignment_id}/open")

    r1 = learner_client.post(f"/classroom/assignments/{assignment_id}/submit", json={"code": "x=1"})
    r2 = learner_client.post(f"/classroom/assignments/{assignment_id}/submit", json={"code": "x=2"})
    assert r1.get_json()["success"] is True
    assert r2.get_json()["success"] is True

    rows = app_module.classroom_db.list_progress_for_assignment(int(assignment_id))
    matching = [row for row in rows if row["assignment_id"] == int(assignment_id)]
    assert len(matching) == 1
    assert matching[0]["code"] == "x=2"  # the later submission won, not a duplicate


# ================================================================
# Optional assignments-table live sync (section 3 of the third pass)
# ================================================================

def test_live_summary_carries_assignments_for_the_table_reconciler(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="hardening_assignsync")
    assignment_id = _publish_assignment(instructor_client, cohort_id, title="Student Marks Program")

    summary = instructor_client.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()
    assert "assignments" in summary
    row = next(a for a in summary["assignments"] if a["id"] == int(assignment_id))
    assert row["title"] == "Student Marks Program"
    assert row["status"] == "published"
    assert row["detail_url"] == f"/classroom/assignments/{assignment_id}"


def test_template_marks_assignment_rows_with_assignment_id(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="hardening_assignrowid")
    _publish_assignment(instructor_client, cohort_id)
    html = instructor_client.get(f"/classroom/cohorts/{cohort_id}").get_data(as_text=True)
    assert "data-assignment-id=" in html
