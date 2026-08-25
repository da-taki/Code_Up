"""Regression tests for the accessible-form-validation cleanup
(feature/classroom-post-demo-hardening, third pass): create cohort, rename
cohort, create assignment, create custom lesson, create custom project all
used to silently no-op on a missing required field - no error, no
indication anything happened, nothing for a blind instructor to hear.

Each now re-renders its own originating page (never a redirect to a blank
one) with:
  - a visible error message near the field
  - aria-invalid="true" on ONLY the invalid field
  - aria-describedby pointing at the error text
  - data-focus-on-error, picked up by static/form-error-focus.js to move
    keyboard focus there and mirror the error into the existing #srAlert
    region (never a new live region)
  - every other field's previously-typed value preserved

Real-browser keyboard verification (focus landing correctly, #srAlert
firing, sibling forms on the same page staying untouched) was done
separately - this file follows the established server-response-content-
assertion convention (see test_classroom_hardening.py, test_classroom_
accessibility.py) since a fresh Flask test client per test is the fastest,
most precise way to pin the exact HTML/attribute contract.
"""

import re

import pytest
from lxml import html as lxml_html

import app as app_module

FORM_ERROR_FOCUS_JS = open("static/form-error-focus.js", encoding="utf-8").read()


@pytest.fixture
def instructor_client():
    return app_module.app.test_client()


def _extract(pattern, data):
    match = re.search(pattern, data)
    assert match, f"pattern not found: {pattern}"
    return match.group(1).decode()


def _make_cohort(instructor_client, name="Python Beginners", username="formval_instr"):
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": username, "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": name}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    return join_code, cohort_id


def _assert_accessible_error(tree, field_id, error_text_fragment):
    field = tree.xpath(f'//*[@id="{field_id}"]')
    assert field, f"field #{field_id} not found"
    field = field[0]
    assert field.get("aria-invalid") == "true"
    described_by = field.get("aria-describedby")
    assert described_by, "field missing aria-describedby"
    assert field.get("data-focus-on-error") is not None
    error_el = tree.xpath(f'//*[@id="{described_by}"]')
    assert error_el, f"no element with id={described_by} for aria-describedby to point at"
    assert error_text_fragment.lower() in error_el[0].text_content().lower()


def _assert_no_stray_invalid_fields(tree, *exempt_ids):
    invalid = tree.xpath('//*[@aria-invalid="true"]')
    stray = [el.get("id") for el in invalid if el.get("id") not in exempt_ids]
    assert not stray, f"unexpected aria-invalid fields: {stray}"


# ================================================================
# create cohort
# ================================================================

def test_empty_cohort_name_shows_accessible_error(instructor_client):
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": "formval_cohort", "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": "   "})
    assert r.status_code == 200  # re-rendered, not redirected to a blank page
    tree = lxml_html.fromstring(r.data)
    _assert_accessible_error(tree, "cohort_name", "cohort name is required")
    _assert_no_stray_invalid_fields(tree, "cohort_name")


def test_empty_cohort_name_does_not_create_a_cohort(instructor_client):
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": "formval_cohort_nocreate", "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    before = instructor_client.get("/classroom/instructor").data
    instructor_client.post("/classroom/cohorts", data={"name": ""})
    after = instructor_client.get("/classroom/instructor").data
    assert before.count(b"cu-join-code") == after.count(b"cu-join-code")


def test_valid_cohort_name_still_creates_and_redirects(instructor_client):
    instructor_client.post(
        "/classroom/instructor/register",
        data={"username": "formval_cohort_ok", "password": "correct-horse-1", "display_name": "Ms Rao"},
        follow_redirects=True,
    )
    r = instructor_client.post("/classroom/cohorts", data={"name": "Real Cohort"})
    assert r.status_code == 302  # unchanged success path: redirect
    listing = instructor_client.get("/classroom/instructor").data
    assert b"Real Cohort" in listing


# ================================================================
# rename cohort
# ================================================================

def test_empty_rename_shows_accessible_error_and_preserves_original_name(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, name="Original Name", username="formval_rename")
    r = instructor_client.post(f"/classroom/cohorts/{cohort_id}/rename", data={"name": "  "})
    assert r.status_code == 200
    tree = lxml_html.fromstring(r.data)
    _assert_accessible_error(tree, "rename_input", "cohort name is required")
    _assert_no_stray_invalid_fields(tree, "rename_input")
    # The cohort's actual name is untouched in the DB.
    listing = instructor_client.get("/classroom/instructor").data
    assert b"Original Name" in listing


def test_rename_error_does_not_mark_the_sibling_assignment_form_invalid(instructor_client):
    """rename_cohort and create_assignment share the same page - a rename
    failure must never bleed an aria-invalid/error onto the unrelated
    assignment-title field, and vice versa."""
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_rename_sibling")
    r = instructor_client.post(f"/classroom/cohorts/{cohort_id}/rename", data={"name": ""})
    tree = lxml_html.fromstring(r.data)
    _assert_no_stray_invalid_fields(tree, "rename_input")
    a_title = tree.xpath('//*[@id="a_title"]')
    assert a_title and a_title[0].get("aria-invalid") is None


def test_valid_rename_still_redirects(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_rename_ok")
    r = instructor_client.post(f"/classroom/cohorts/{cohort_id}/rename", data={"name": "New Name"})
    assert r.status_code == 302


# ================================================================
# create assignment
# ================================================================

def test_empty_assignment_title_shows_accessible_error_and_preserves_other_fields(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_assignment")
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={
            "title": "",
            "instructions": "Store marks and compute the average.",
            "starter_code": "marks = {}",
            "expected_concepts": "dictionaries, loops",
            "ai_policy": "HINTS_ONLY",
            "is_assessment": "on",
        },
    )
    assert r.status_code == 200
    tree = lxml_html.fromstring(r.data)
    _assert_accessible_error(tree, "a_title", "assignment title is required")
    _assert_no_stray_invalid_fields(tree, "a_title")

    instructions = tree.xpath('//*[@id="a_instructions"]')[0].text_content()
    assert "Store marks and compute the average." in instructions
    starter = tree.xpath('//*[@id="a_starter"]')[0].text_content()
    assert "marks = {}" in starter
    assert tree.xpath('//*[@id="a_concepts"]')[0].get("value") == "dictionaries, loops"
    selected_option = tree.xpath('//*[@id="a_policy"]/option[@selected]')
    assert selected_option and selected_option[0].get("value") == "HINTS_ONLY"
    assert tree.xpath('//*[@id="a_assessment"]')[0].get("checked") is not None


def test_empty_assignment_title_does_not_create_an_assignment(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_assignment_nocreate")
    instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": "", "instructions": "x", "starter_code": ""},
    )
    dashboard = instructor_client.get(f"/classroom/cohorts/{cohort_id}").data
    assert b"No assignments yet." in dashboard


def test_valid_assignment_title_still_creates_and_redirects(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_assignment_ok")
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": "Real Assignment", "instructions": "x", "starter_code": "", "ai_policy": "FULL"},
    )
    assert r.status_code == 302
    assert "/classroom/assignments/" in r.headers["Location"]


# ================================================================
# create custom lesson
# ================================================================

def test_empty_lesson_title_shows_accessible_error_and_preserves_other_fields(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_lesson")
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/lessons",
        data={"title": "", "objective": "Understand dictionaries", "explanation": "A dict maps keys to values."},
    )
    assert r.status_code == 200
    tree = lxml_html.fromstring(r.data)
    _assert_accessible_error(tree, "l_title", "lesson title is required")
    _assert_no_stray_invalid_fields(tree, "l_title")
    assert tree.xpath('//*[@id="l_objective"]')[0].get("value") == "Understand dictionaries"
    assert "A dict maps keys to values." in tree.xpath('//*[@id="l_explanation"]')[0].text_content()


def test_empty_lesson_title_does_not_create_a_lesson(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_lesson_nocreate")
    instructor_client.post(f"/classroom/cohorts/{cohort_id}/lessons", data={"title": ""})
    listing = instructor_client.get(f"/classroom/cohorts/{cohort_id}/lessons").data
    assert b"You haven't created any lessons yet." in listing


def test_valid_lesson_title_still_creates_and_redirects(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_lesson_ok")
    r = instructor_client.post(f"/classroom/cohorts/{cohort_id}/lessons", data={"title": "Real Lesson"})
    assert r.status_code == 302


# ================================================================
# create custom project
# ================================================================

def test_empty_project_title_shows_accessible_error_and_preserves_checkpoints(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_project")
    r = instructor_client.post(
        f"/classroom/cohorts/{cohort_id}/projects",
        data={
            "title": "",
            "instructions": "Build a calculator.",
            "checkpoint_label": ["Add the numbers", "", "", "", ""],
            "checkpoint_type": ["contains_operator", "contains_call", "contains_call", "contains_call", "contains_call"],
            "checkpoint_config": ["+", "", "", "", ""],
        },
    )
    assert r.status_code == 200
    tree = lxml_html.fromstring(r.data)
    _assert_accessible_error(tree, "p_title", "guided project title is required")
    _assert_no_stray_invalid_fields(tree, "p_title")
    assert "Build a calculator." in tree.xpath('//*[@id="p_instructions"]')[0].text_content()
    assert tree.xpath('//*[@id="cp_label_0"]')[0].get("value") == "Add the numbers"
    assert tree.xpath('//*[@id="cp_config_0"]')[0].get("value") == "+"


def test_empty_project_title_does_not_create_a_project(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_project_nocreate")
    instructor_client.post(f"/classroom/cohorts/{cohort_id}/projects", data={"title": ""})
    listing = instructor_client.get(f"/classroom/cohorts/{cohort_id}/projects").data
    assert b"You haven't created any guided projects yet." in listing


def test_valid_project_title_still_creates_and_redirects(instructor_client):
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_project_ok")
    r = instructor_client.post(f"/classroom/cohorts/{cohort_id}/projects", data={"title": "Real Project"})
    assert r.status_code == 302


# ================================================================
# shared: form-error-focus.js
# ================================================================

def test_form_error_focus_script_is_generic_and_reused_everywhere(instructor_client):
    """One tiny shared script, not five copies - included on every page
    that can show one of these errors."""
    _join_code, cohort_id = _make_cohort(instructor_client, username="formval_scripttag")
    for path in (
        "/classroom/instructor",
        f"/classroom/cohorts/{cohort_id}",
        f"/classroom/cohorts/{cohort_id}/lessons",
        f"/classroom/cohorts/{cohort_id}/projects",
    ):
        html = instructor_client.get(path).get_data(as_text=True)
        assert 'src="/static/form-error-focus.js"' in html


def test_form_error_focus_never_creates_a_new_live_region():
    assert "getElementById('srAlert')" in FORM_ERROR_FOCUS_JS
    assert not re.search(r"setAttribute\(\s*['\"]aria-live['\"]", FORM_ERROR_FOCUS_JS)
    assert FORM_ERROR_FOCUS_JS.count("getElementById('srAlert')") == 1


def test_form_error_focus_only_acts_when_an_error_is_present():
    """No error on the page (the normal case) - the script must be a
    complete no-op, never touching focus or #srAlert."""
    assert "document.querySelector('[data-focus-on-error]')" in FORM_ERROR_FOCUS_JS
    assert "if (!invalid) return;" in FORM_ERROR_FOCUS_JS
