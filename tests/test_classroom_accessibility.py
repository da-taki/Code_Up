"""Static/code-level accessibility regression checks for every new classroom
screen: semantic landmarks, heading hierarchy, label associations, live
regions, table headers, and keyboard-operable controls (no clickable divs).

This is NOT a substitute for testing with a real screen reader - it checks
what can be verified from the rendered HTML: correct semantics, not actual
announcement behavior. See the final summary for what still needs a human
NVDA/JAWS/VoiceOver pass.
"""

import re

import pytest
from lxml import html as lxml_html

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


def _make_cohort(client, username):
    client.post(
        "/classroom/instructor/register",
        data={"username": username, "password": "correct-horse-1", "display_name": "Teacher"},
    )
    r = client.post("/classroom/cohorts", data={"name": "Cohort"}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    return join_code, cohort_id


def _tree(response):
    return lxml_html.fromstring(response.data)


def _assert_base_landmarks(tree):
    """Every classroom page shares _base.html: skip link, both live regions,
    a banner header, and a single main landmark labelled by the heading."""
    assert tree.xpath('//nav[contains(@aria-label, "Skip")]//a[@href="#mainContent"]')
    sr_announcer = tree.xpath('//*[@id="srAnnouncer"]')
    assert sr_announcer and sr_announcer[0].get("aria-live") == "polite"
    sr_alert = tree.xpath('//*[@id="srAlert"]')
    assert sr_alert and sr_alert[0].get("aria-live") == "assertive"
    assert tree.xpath('//header[@role="banner"]')
    main = tree.xpath('//main[@id="mainContent"]')
    assert len(main) == 1
    assert main[0].get("aria-labelledby") == "pageTitle"
    assert tree.xpath('//h1[@id="pageTitle"]')


def _assert_every_input_has_a_label(tree):
    """Every text/password/date/number/checkbox/radio/textarea/select has an
    associated <label for=...>, or is itself wrapped/labelled - no bare
    unlabelled form controls."""
    labelled_ids = set(tree.xpath('//label/@for'))
    controls = tree.xpath(
        '//input[@type!="hidden" and @type!="submit" and @type!="button"]'
        '|//textarea|//select'
    )
    unlabelled = []
    for c in controls:
        cid = c.get("id")
        has_label = bool(cid and cid in labelled_ids)
        has_aria = bool(c.get("aria-label") or c.get("aria-labelledby"))
        # a control physically inside a <label> also counts
        inside_label = bool(c.xpath('ancestor::label'))
        if not (has_label or has_aria or inside_label):
            unlabelled.append(lxml_html.tostring(c, pretty_print=False)[:120])
    assert not unlabelled, f"unlabelled controls: {unlabelled}"


def _assert_heading_hierarchy_no_skip(tree):
    """h1 exists once; subsequent headings never jump more than one level
    down at a time (e.g. h2 straight to h4)."""
    levels = [int(h.tag[1]) for h in tree.xpath('//h1|//h2|//h3|//h4|//h5|//h6')]
    assert levels, "page has no headings at all"
    assert levels[0] == 1
    for prev, cur in zip(levels, levels[1:]):
        assert cur <= prev + 1, f"heading level jumped from h{prev} to h{cur}: {levels}"


def _assert_tables_have_scoped_headers(tree):
    for table in tree.xpath("//table"):
        ths = table.xpath(".//th")
        assert ths, "table has no <th> headers at all"
        for th in ths:
            assert th.get("scope") in ("col", "row"), f"<th> missing scope attribute: {lxml_html.tostring(th)}"


def _assert_no_clickable_divs(tree):
    """No div/span with an onclick handler standing in for a real button."""
    fake_buttons = tree.xpath('//div[@onclick]|//span[@onclick]')
    assert not fake_buttons, f"found clickable div/span instead of a real control: {fake_buttons}"


def _full_audit(response):
    tree = _tree(response)
    _assert_base_landmarks(tree)
    _assert_every_input_has_a_label(tree)
    _assert_heading_hierarchy_no_skip(tree)
    _assert_tables_have_scoped_headers(tree)
    _assert_no_clickable_divs(tree)
    return tree


# ---- instructor pages -------------------------------------------------------

def test_instructor_login_page_accessible():
    client = app_module.app.test_client()
    _full_audit(client.get("/classroom/instructor/login"))


def test_instructor_dashboard_accessible():
    client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(client, "a11y_instr1")
    _full_audit(client.get("/classroom/instructor"))


def test_cohort_dashboard_accessible():
    client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(client, "a11y_instr2")
    _full_audit(client.get(f"/classroom/cohorts/{cohort_id}"))


def test_assignment_detail_accessible_including_permission_checkboxes():
    client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(client, "a11y_instr3")
    r = client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": "A", "instructions": "i", "starter_code": "", "ai_policy": "FULL"},
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    tree = _full_audit(client.get(f"/classroom/assignments/{assignment_id}"))
    # nine capability checkboxes must each have a real label
    checkboxes = tree.xpath('//input[@type="checkbox" and starts-with(@id, "cap_")]')
    assert len(checkboxes) == 9


def test_help_queue_accessible():
    client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(client, "a11y_instr4")
    _full_audit(client.get(f"/classroom/cohorts/{cohort_id}/help-requests"))


def test_custom_lessons_page_accessible():
    client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(client, "a11y_instr5")
    _full_audit(client.get(f"/classroom/cohorts/{cohort_id}/lessons"))


def test_custom_projects_page_accessible():
    client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(client, "a11y_instr6")
    _full_audit(client.get(f"/classroom/cohorts/{cohort_id}/projects"))


def test_cohort_report_accessible():
    client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(client, "a11y_instr7")
    _full_audit(client.get(f"/classroom/cohorts/{cohort_id}/report"))


# ---- learner pages -----------------------------------------------------------

def test_join_page_accessible():
    client = app_module.app.test_client()
    _full_audit(client.get("/classroom/join"))


def test_learner_home_accessible():
    instructor = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor, "a11y_learner1")
    learner = app_module.app.test_client()
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Amir"}, follow_redirects=True)
    _full_audit(learner.get("/classroom/learner"))


def test_curriculum_home_accessible():
    instructor = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor, "a11y_learner2")
    learner = app_module.app.test_client()
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Amir"}, follow_redirects=True)
    _full_audit(learner.get("/classroom/curriculum"))


def test_quiz_page_accessible_radio_group_has_legend():
    instructor = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor, "a11y_learner3")
    learner = app_module.app.test_client()
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Amir"}, follow_redirects=True)
    tree = _tree(learner.get("/classroom/curriculum/printing/quiz"))
    _assert_base_landmarks(tree)
    fieldsets = tree.xpath("//fieldset")
    assert fieldsets, "quiz radios must be grouped in a fieldset"
    assert fieldsets[0].xpath("./legend"), "fieldset must have a legend naming the question"
    radios = tree.xpath('//input[@type="radio"]')
    assert len(radios) >= 2
    labelled_ids = set(tree.xpath('//label/@for'))
    for r in radios:
        assert r.get("id") in labelled_ids


def test_restart_confirm_pages_have_real_confirm_and_cancel_controls():
    instructor = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor, "a11y_learner4")
    learner = app_module.app.test_client()
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Amir"}, follow_redirects=True)
    tree = _full_audit(learner.get("/classroom/curriculum/restart-module/printing/confirm"))
    assert tree.xpath('//form//button[@type="submit"]')
    assert tree.xpath('//a[contains(@href, "curriculum")]')  # cancel link back to safety


def test_learner_detail_accessible_with_curriculum_table():
    instructor = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor, "a11y_learner5")
    learner = app_module.app.test_client()
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Amir"}, follow_redirects=True)
    learner.post("/classroom/curriculum/printing/attempt", json={"code": 'print("hi")'})

    dash = instructor.get(f"/classroom/cohorts/{cohort_id}")
    learner_id = _extract(rb"learners/(\d+)", dash.data)
    _full_audit(instructor.get(f"/classroom/cohorts/{cohort_id}/learners/{learner_id}"))


# ---- capability-disabled buttons must be understandable, not just greyed out --

def test_disabled_capability_state_is_conveyed_with_aria_disabled_not_hidden():
    """The static server-rendered form checkboxes double as an audit trail:
    unchecking a capability must be readable as an accessible unchecked
    checkbox state, never a purely visual (color-only) indicator."""
    client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(client, "a11y_capstate")
    r = client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": "A", "instructions": "i", "starter_code": "", "ai_policy": "OFF"},
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    tree = _tree(client.get(f"/classroom/assignments/{assignment_id}"))
    checked = tree.xpath('//input[@type="checkbox" and starts-with(@id, "cap_") and @checked]')
    assert checked == []  # OFF preset -> none checked, and that's a real semantic checkbox state


# ---- reflow at high zoom (Pass 5B) ------------------------------------------
# Browser-verified at 320 CSS px (the WCAG 1.4.10 "400% zoom" equivalent): a
# raw ISO timestamp / long unbreakable cell in a wide table used to force the
# entire page wider than the viewport (a flex-item sizing bug in
# .cu-classroom-main, compounded by tables having no scroll container of
# their own), silently clipping columns with no way to reach them. Fixed in
# static/style/classroom.css. This test pins the markup half of that fix
# (every table wrapped) since CSS layout itself isn't unit-testable here.

def _assert_every_table_is_wrapped(tree):
    for table in tree.xpath('//table[contains(@class, "cu-table")]'):
        parent = table.getparent()
        assert parent is not None and "cu-table-wrap" in (parent.get("class") or ""), (
            "a cu-table must be wrapped in a scrollable .cu-table-wrap container "
            "so a wide/unbreakable cell scrolls within itself instead of forcing "
            "the whole page wider at high zoom/reflow"
        )


def test_cohort_dashboard_tables_are_reflow_safe():
    instructor = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor, "reflow_instr1")
    learner = app_module.app.test_client()
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Amir"}, follow_redirects=True)
    tree = _tree(instructor.get(f"/classroom/cohorts/{cohort_id}"))
    _assert_every_table_is_wrapped(tree)


def test_instructor_dashboard_table_is_reflow_safe():
    client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(client, "reflow_instr2")
    _assert_every_table_is_wrapped(_tree(client.get("/classroom/instructor")))


def test_learner_home_tables_are_reflow_safe():
    instructor = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor, "reflow_instr3")
    r = instructor.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": "A", "instructions": "i", "starter_code": "", "ai_policy": "FULL"},
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor.post(f"/classroom/assignments/{assignment_id}/publish")
    learner = app_module.app.test_client()
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Bea"}, follow_redirects=True)
    _assert_every_table_is_wrapped(_tree(learner.get("/classroom/learner")))


def test_learner_detail_tables_are_reflow_safe():
    instructor = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor, "reflow_instr4")
    learner = app_module.app.test_client()
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Chen"}, follow_redirects=True)
    dash = instructor.get(f"/classroom/cohorts/{cohort_id}")
    learner_id = _extract(rb"learners/(\d+)", dash.data)
    _assert_every_table_is_wrapped(_tree(instructor.get(f"/classroom/cohorts/{cohort_id}/learners/{learner_id}")))


def test_help_queue_table_is_reflow_safe():
    instructor = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor, "reflow_instr5")
    learner = app_module.app.test_client()
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Dee"}, follow_redirects=True)
    learner.post("/classroom/help-requests", json={"message": "stuck"})
    _assert_every_table_is_wrapped(_tree(instructor.get(f"/classroom/cohorts/{cohort_id}/help-requests")))


def test_assignment_detail_table_is_reflow_safe():
    client = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(client, "reflow_instr6")
    r = client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": "A", "instructions": "i", "starter_code": "", "ai_policy": "FULL"},
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    _assert_every_table_is_wrapped(_tree(client.get(f"/classroom/assignments/{assignment_id}")))


def test_curriculum_home_table_is_reflow_safe():
    instructor = app_module.app.test_client()
    join_code, cohort_id = _make_cohort(instructor, "reflow_instr7")
    learner = app_module.app.test_client()
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Faye"}, follow_redirects=True)
    _assert_every_table_is_wrapped(_tree(learner.get("/classroom/curriculum")))
