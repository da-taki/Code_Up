"""Regression checks for the /ide Classroom disclosure redesign and its
background live-sync loop (feature/ide-classroom-progressive-disclosure,
second pass).

The sync loop, DOM diffing, and focus/disclosure-state preservation are
client-side behavior in static/classroom.js with no JS test runner in this
repo (see test_ide_accessibility_semantics.py's
test_classroom_js_has_no_local_live_regions for the established pattern this
file follows: static/source-level assertions, not a substitute for exercising
a real browser). The end-to-end instructor-publishes/learner-sees-it flow was
verified manually in a real browser across two contexts - see the PR/commit
description for that walkthrough. What's checked here is the code-level
contract that makes that behavior possible and keeps it from regressing:
single polling loop, in-flight guard, visibility pausing, targeted (not
full-teardown) DOM patches, and the announce-once-per-assignment guard.
"""

import re

import pytest

import app as app_module

CLASSROOM_JS = open("static/classroom.js", encoding="utf-8").read()
APP_JS = open("static/app.js", encoding="utf-8").read()


def _fn_body(name):
    """Extract one top-level `function name(...) { ... }` body via brace
    counting (regex alone can't handle nested braces reliably)."""
    m = re.search(r"function " + re.escape(name) + r"\s*\([^)]*\)\s*\{", CLASSROOM_JS)
    assert m, f"function {name} not found in classroom.js"
    start = m.end()
    depth = 1
    i = start
    while depth > 0:
        if CLASSROOM_JS[i] == "{":
            depth += 1
        elif CLASSROOM_JS[i] == "}":
            depth -= 1
        i += 1
    return CLASSROOM_JS[start:i]


# ---- disclosure defaults --------------------------------------------------

def test_anonymous_classroom_closed_by_default():
    """renderJoinPanel's outer <details> must not be created with open:true -
    the anonymous /ide state shows only the "Classroom" summary line."""
    body = _fn_body("renderJoinPanel")
    details_line = re.search(r"el\('details', \{[^}]*\}\)", body).group(0)
    assert "open" not in details_line


def test_joined_classroom_collapsed_by_default():
    """renderDashboardPanel's outer <details> must also default closed -
    joined learners see the compact "Classroom · <cohort> · N pending" line,
    not the full panel, until they open it."""
    body = _fn_body("renderDashboardPanel")
    details_line = re.search(r"el\('details', \{[^}]*\}\)", body).group(0)
    assert "open" not in details_line


def test_single_classroom_disclosure_shared_across_states():
    """Both states build one #classroomDetails/#classroomPanelHeading pair
    (the same disclosure "transforms" per the spec), not two competing
    disclosures."""
    for fn in ("renderJoinPanel", "renderDashboardPanel"):
        body = _fn_body(fn)
        assert "id: 'classroomDetails'" in body
        assert "id: 'classroomPanelHeading'" in body


def test_join_command_reveal_wired_through_app_js():
    """"join a classroom"/"go to classroom" etc. reach into a collapsed
    disclosure via window._classroomReveal before app.js moves focus -
    unchanged from the first progressive-disclosure pass, still required now
    that the join form lives one level deeper (inside the shared disclosure)."""
    assert "window._classroomReveal = function" in CLASSROOM_JS
    assert "window._classroomReveal === 'function') window._classroomReveal(targetId)" in APP_JS
    assert "window._classroomReveal === 'function') window._classroomReveal(payload.focus_hint)" in APP_JS


def test_successful_join_swaps_disclosure_state():
    """A successful join re-fetches and re-renders through the normal
    dashboard path (join content disappears, joined summary appears) rather
    than patching the anonymous form in place."""
    body = _fn_body("doJoin")
    assert "fetchContextAndRender()" in body


# ---- sync lifecycle ---------------------------------------------------------

def test_sync_starts_only_once_joined():
    """startClassroomSync() must only be reachable from a joined branch -
    the dashboard fetch's `if (data.joined)` and the non-dashboard seed
    fetch's `if (!data || !data.success || !data.joined) return;` guard."""
    assert re.search(r"if \(data\.joined\) \{\s*renderDashboardPanel\(panel, data\);\s*seedSyncBaseline\(data\);\s*startClassroomSync\(\);", CLASSROOM_JS)
    assert "if (!data || !data.success || !data.joined) return;" in CLASSROOM_JS


def test_leaving_classroom_stops_sync():
    """applyClassroomSync must stop the loop and fall back to a full
    re-render when a poll discovers joined flipped to false (removed/left
    via another tab or command)."""
    body = _fn_body("applyClassroomSync")
    assert "stopClassroomSync();" in body
    assert "fetchContextAndRender();" in body


def test_no_overlapping_sync_requests():
    """One in-flight guard shared by the timer loop, the immediate triggers,
    and the manual dashboard refresh - requestClassroomSync bails out before
    fetching if a request is already outstanding, and fetchContextAndRender's
    dashboard branch sets/clears the same flag around its own fetch."""
    req_body = _fn_body("requestClassroomSync")
    assert "syncState.inFlight" in req_body
    assert re.search(r"if \(!syncState\.active \|\| document\.hidden \|\| syncState\.inFlight\) return;", req_body)
    assert "syncState.inFlight = true;\n      fetch('/classroom/ide/summary')" in CLASSROOM_JS


def test_hidden_tab_does_not_poll():
    """requestClassroomSync bails out while document.hidden, and the
    visibilitychange handler clears the interval on hide so no timer fires
    while backgrounded."""
    req_body = _fn_body("requestClassroomSync")
    assert "document.hidden" in req_body
    assert re.search(r"if \(document\.hidden\) \{\s*if \(syncState\.timer\) \{ clearInterval\(syncState\.timer\)", CLASSROOM_JS)


def test_visibility_and_focus_trigger_immediate_sync():
    """Both visibilitychange (tab shown again) and window focus trigger an
    immediate sync - required for the demo's rapid teacher-tab/learner-tab
    switching, not just the 8-10s interval."""
    assert "document.addEventListener('visibilitychange', function () {" in CLASSROOM_JS
    assert "window.addEventListener('focus', function () {" in CLASSROOM_JS
    assert CLASSROOM_JS.count("requestClassroomSync({ immediate: true })") >= 3  # visibilitychange, focus, disclosure-toggle


def test_single_polling_interval_configured():
    """Exactly one interval constant/timer mechanism - no second competing
    poll loop."""
    assert "const SYNC_INTERVAL_MS = 9000;" in CLASSROOM_JS
    assert CLASSROOM_JS.count("setInterval(") == 2  # startClassroomSync + the visibilitychange resume path, same callback


def test_network_sync_failure_is_silent():
    """A failed background poll must not announce, error, or touch the
    panel - only reset the in-flight/timestamp bookkeeping so the next
    normal opportunity can retry."""
    req_body = _fn_body("requestClassroomSync")
    catch_block = req_body.split(".catch(function () {")[1]
    assert "announce(" not in catch_block
    assert "innerHTML" not in catch_block
    assert "syncState.inFlight = false;" in catch_block


# ---- announce-once and DOM-patch discipline ----------------------------------

def test_new_assignment_detection_and_single_announcement():
    """A newly-seen assignment id in 'new' state announces once via the
    existing centralized announce(); already-known ids (seeded at sync
    start, or already announced) never re-trigger it."""
    body = _fn_body("applyClassroomSync")
    assert "if (!announced[a.id]) {" in body
    assert "announced[a.id] = true;" in body
    assert "if (a.state === 'new') announce('New assignment: ' + a.title + '.');" in body


def test_baseline_seeds_already_known_assignments():
    """Assignments present the moment sync starts are pre-marked as
    'announced' so page-load state is never (re-)spoken as a live event -
    only something that newly appears after this point is a candidate."""
    body = _fn_body("seedSyncBaseline")
    assert "(data.assignments || []).forEach(function (a) { seen[a.id] = true; });" in body


def test_help_status_change_announced_once():
    """Transitioning into 'helping' announces exactly once via the
    centralized announce(); the check is gated on the previous status so a
    later unchanged poll doesn't repeat it."""
    body = _fn_body("applyClassroomSync")
    assert "if (newHelpStatus === 'helping' && syncState.lastHelpStatus !== 'helping') {" in body
    assert "announce('Your instructor is helping you now.');" in body


def test_no_new_aria_live_or_duplicate_announcement_channel():
    """Live-sync additions must not introduce a second announcement/live-
    region mechanism - same rule test_ide_accessibility_semantics.py already
    enforces for the rest of classroom.js, re-checked here since this is new
    code in the same file."""
    assert not re.search(r"setAttribute\(\s*['\"]aria-live['\"]", CLASSROOM_JS)
    assert not re.search(r"setAttribute\(\s*['\"]role['\"]\s*,\s*['\"]status['\"]", CLASSROOM_JS)


def test_background_sync_never_replaces_active_item_panel():
    """Outside the dashboard (an assignment/project/lesson is the active
    content), applyClassroomSync must skip patchDashboardPanel entirely -
    the announcement can still fire, but nothing about the active panel is
    touched."""
    body = _fn_body("applyClassroomSync")
    assert "if (mode === 'dashboard') patchDashboardPanel(previous, data);" in body


def test_patch_function_cannot_touch_whole_panel():
    """patchDashboardPanel takes (previous, data) only - no reference to the
    top-level panel/section element - so it is structurally unable to do a
    panel.innerHTML='' full rebuild; it can only reach specific sub-
    containers by id."""
    assert "function patchDashboardPanel(previous, data) {" in CLASSROOM_JS
    body = _fn_body("patchDashboardPanel")
    assert "panel.innerHTML" not in body
    assert ".focus()" not in body  # never steals focus


def test_disclosure_open_state_preserved_across_assignment_patch():
    """When the assignments list changes, the nested "Show all assignments"
    <details> is rebuilt with its previous open/closed state carried
    forward, not reset to closed."""
    body = _fn_body("patchDashboardPanel")
    assert "const keepOpen = existingDetails ? existingDetails.open : false;" in body
    assert "buildAssignmentsBodyNodes(data, keepOpen)" in body


def test_help_container_only_rebuilt_on_status_change():
    """The help widget (and any in-progress typed draft inside it) is only
    replaced when help_request's fingerprint actually changes - an unrelated
    poll (e.g. a new assignment) must not touch it, or a learner's
    in-progress help text would be silently discarded mid-poll."""
    body = _fn_body("patchDashboardPanel")
    idx = body.index("helpFingerprint(previous")
    window = body[idx:idx + 320]
    assert "classroomHelpContainer" in window
    assert "appendHelpWidget(helpContainer" in window


def test_fingerprints_ignore_irrelevant_fields():
    """Fingerprints are built from specific, meaningful fields only (id/
    state/title, checkpoints, module identity) - never a raw JSON.stringify
    of the whole payload, which would treat incidental server fields as
    "changed" and over-trigger rebuilds."""
    assert "function assignmentsFingerprint(list) { return JSON.stringify((list || []).map(function (a) { return [a.id, a.state, a.title]; })); }" in CLASSROOM_JS
    assert "function moduleFingerprint(m) { return m ? JSON.stringify([m.module_id, m.index, m.total, m.title]) : 'none'; }" in CLASSROOM_JS
    assert "function helpFingerprint(hr) { return hr ? JSON.stringify([hr.id, hr.status]) : 'none'; }" in CLASSROOM_JS


# ---- backend data the sync loop depends on (already covered end-to-end in
# test_classroom_ide_integration.py; these two just pin the exact field
# names patchDashboardPanel/applyClassroomSync read from the response) -----

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


def _make_cohort(instructor_client, name="Python Beginners", username="synctest_instr"):
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": username, "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": name}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    return join_code, cohort_id


def test_summary_endpoint_carries_the_fields_the_client_diffs_on(instructor_client, learner_client):
    join_code, cohort_id = _make_cohort(instructor_client)
    learner_client.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})
    summary = learner_client.get("/classroom/ide/summary").get_json()
    assert summary["joined"] is True
    for key in ("cohort", "assignment_counts", "assignments", "projects", "module", "help_request"):
        assert key in summary


def test_summary_reflects_a_newly_published_assignment_immediately():
    """The client's polling loop is only as live as this endpoint - confirm
    a freshly published assignment shows up on the very next GET, with no
    caching layer to go stale."""
    instructor = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor, username="synctest_instr2")
    learner = app_module.app.test_client()
    learner.post("/classroom/join-api", json={"join_code": join_code, "display_name": "Amir"})

    before = learner.get("/classroom/ide/summary").get_json()
    assert before["assignment_counts"]["remaining"] == 0

    r = instructor.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": "Student Marks Program", "instructions": "x", "starter_code": "", "ai_policy": "FULL"},
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor.post(f"/classroom/assignments/{assignment_id}/publish")

    after = learner.get("/classroom/ide/summary").get_json()
    assert after["assignment_counts"]["remaining"] == 1
    assert after["assignments"][0]["state"] == "new"
    assert after["assignments"][0]["title"] == "Student Marks Program"
