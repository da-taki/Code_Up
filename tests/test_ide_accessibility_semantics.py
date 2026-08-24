"""Semantic-placement checks for /ide, written for the accessibility audit
on feature/accessibility-semantic-placement-audit.

These test SEMANTICS, not "does an attribute exist": a bounded, intentional
set of named regions (not landmark spam), no redundant ARIA roles that
native HTML already provides, a clean heading hierarchy, and no duplicate
announcement channels in static/classroom.js. This is code-level/DOM
validation - it does not substitute for a real NVDA/JAWS pass (see the
audit's final report for what still needs one).
"""

import re

import pytest
from lxml import html as lxml_html

import app as app_module


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _tree(client):
    html = client.get("/ide").get_data(as_text=True)
    return lxml_html.fromstring(html), html


# ---- landmarks: bounded and intentional, not spammed ---------------------------

def test_exactly_one_main(client):
    tree, _ = _tree(client)
    mains = tree.xpath("//main")
    assert len(mains) == 1
    assert mains[0].get("id") == "mainContent"
    assert mains[0].get("aria-labelledby") == "pageTitle"


def test_header_relies_on_native_banner_semantics_not_redundant_aria(client):
    """A <header> that is a direct child of <body> (not nested inside
    article/aside/main/nav/section) already computes to the banner landmark
    natively - role="banner" here would be redundant ARIA restating what
    native HTML already provides."""
    tree, _ = _tree(client)
    headers = tree.xpath("/html/body/header")
    assert len(headers) == 1
    assert headers[0].get("role") is None


def test_tutorial_overlay_relies_on_native_complementary_semantics(client):
    """A top-level <aside> (not nested in article/main/nav/section as its
    sectioning ancestor) already computes to the complementary landmark
    natively - role="complementary" here would be redundant."""
    tree, _ = _tree(client)
    overlay = tree.xpath('//aside[@id="tutorialOverlay"]')
    assert overlay
    assert overlay[0].get("role") is None


def test_no_explicit_role_region_anywhere(client):
    """Every region-like landmark in /ide is an implicitly-named <section>
    (region role granted natively by having an accessible name), never an
    explicit role="region" bolted onto a div. Keeps region placement a
    deliberate, auditable decision instead of markup sprawl."""
    _, html = _tree(client)
    assert 'role="region"' not in html


@pytest.mark.parametrize("section_id_or_class,heading_id,expected_heading_text", [
    ("cu-editor-wrapper", "codeEditorHeading", "Code editor"),
    ("cu-output-section", "programOutputHeading", "Program output"),
    ("cu-voice-console", "command-input-label", "Commands"),
])
def test_justified_regions_are_named_sections(client, section_id_or_class, heading_id, expected_heading_text):
    """Code editor, Program output, and Commands are the three IDE regions
    explicitly worth NVDA/JAWS region-navigation (a blind learner jumping
    straight to "Commands" or "Program output" is a real, common need) -
    each is a <section> named by its own visible heading, per HTML-AAM's
    "named section -> region role" mapping."""
    tree, _ = _tree(client)
    sections = tree.xpath(f'//section[contains(@class, "{section_id_or_class}")]')
    assert sections, f"no <section> found for {section_id_or_class}"
    section = sections[0]
    assert section.get("aria-labelledby") == heading_id
    heading = tree.xpath(f'//*[@id="{heading_id}"]')
    assert heading and heading[0].tag in ("h1", "h2", "h3")
    assert (heading[0].text or "").strip() == expected_heading_text


def test_learning_tools_and_structure_panel_are_asides_not_regions(client):
    """Learning tools (snippets/project files/program inputs) and the code
    structure navigator are genuinely complementary to the main editing
    workflow - <aside> with aria-labelledby is correct; they should not
    additionally claim role="region" (redundant with the native aside
    mapping) nor lose their accessible name."""
    tree, _ = _tree(client)
    learning_tools = tree.xpath('//aside[contains(@class, "cu-snippets")]')
    assert learning_tools and learning_tools[0].get("aria-labelledby") == "learningToolsHeading"
    assert learning_tools[0].get("role") is None
    structure = tree.xpath('//aside[@id="structurePanel"]')
    assert structure and structure[0].get("aria-labelledby") == "structure-heading"


def test_individual_toolbar_buttons_are_not_landmarks(client):
    """Sanity check against landmark spam: none of the small, frequent
    controls (run/analyze/fix/save/etc.) are wrapped in their own named
    region - only the few genuinely-justified areas are."""
    tree, _ = _tree(client)
    for btn_id in ("runBtn", "analyzeBtn", "fixBtn", "saveBtn", "voiceButton"):
        btn = tree.xpath(f'//*[@id="{btn_id}"]')
        assert btn
        # A button's own ancestor sections are the 3 justified ones only -
        # never a bespoke single-button region.
        ancestor_sections = btn[0].xpath('ancestor::section[@aria-labelledby]')
        for sec in ancestor_sections:
            assert sec.get("aria-labelledby") in (
                "codeEditorHeading", "command-input-label",
            ), f"unexpected region ancestor for {btn_id}: {sec.get('aria-labelledby')}"


# ---- heading hierarchy ------------------------------------------------------------

def test_heading_hierarchy_has_exactly_one_h1_and_no_skipped_levels(client):
    tree, _ = _tree(client)
    headings = tree.xpath("//h1|//h2|//h3|//h4|//h5|//h6")
    levels = [int(h.tag[1]) for h in headings]
    assert levels.count(1) == 1, "exactly one h1 expected"
    assert levels[0] == 1
    for prev, cur in zip(levels, levels[1:]):
        assert cur <= prev + 1, f"heading level jumped from h{prev} to h{cur}: {levels}"


def test_mentor_transcript_is_a_peer_section_not_nested_under_program_inputs(client):
    """Regression: Mentor transcript is a top-level functional area (like
    Program output/Program inputs/Commands), not a subsection of whichever
    heading happens to precede it in DOM order - it must be h2, matching
    its siblings, not h3."""
    tree, _ = _tree(client)
    heading = tree.xpath('//*[@id="mentor-transcript-heading"]')
    assert heading and heading[0].tag == "h2"


# ---- duplicate transcript / no unused focus targets -----------------------------

def test_no_duplicate_element_ids(client):
    tree, _ = _tree(client)
    ids = tree.xpath("//*/@id")
    seen = set()
    dupes = set()
    for i in ids:
        (dupes if i in seen else seen).add(i)
    assert not dupes, f"duplicate ids: {dupes}"


def test_output_log_and_command_input_are_reachable_focus_targets(client):
    """These are the two static, always-present focus targets referenced by
    codeup.classroom.ide_commands.NAV_TARGETS ("go to output" / "go to
    command box") - both must be real, focusable controls, not placeholders."""
    tree, _ = _tree(client)
    output = tree.xpath('//*[@id="output"]')
    assert output and output[0].get("tabindex") == "0"
    voice_text = tree.xpath('//*[@id="voiceText"]')
    assert voice_text and voice_text[0].tag == "input"


# ---- classroom.js: single announcement channel ------------------------------------

def test_classroom_js_has_no_local_live_regions():
    """Every classroom.js status paragraph is visible plain text; the
    single announcement channel is the centralized announce() helper
    (speak() + the page's one #srAnnouncer), never a second, competing
    aria-live region duplicating the same message (see the accessibility
    audit's duplicate-announcement fix). Checked as actual attribute/
    setAttribute usage, not a bare substring, since explanatory comments
    are allowed to mention "aria-live" when documenting why it was removed."""
    with open("static/classroom.js", encoding="utf-8") as fh:
        src = fh.read()
    assert not re.search(r"setAttribute\(\s*['\"]aria-live['\"]", src)
    assert not re.search(r"setAttribute\(\s*['\"]role['\"]\s*,\s*['\"]status['\"]", src)
    assert not re.search(r"['\"]aria-live['\"]\s*:", src)  # el(tag, {ariaLive/aria-live: ...}) form


@pytest.mark.parametrize("status_var,announce_context", [
    ("classroomHelpStatus", "Could not send the help request"),
    ("classroomGuidedStatus", "Could not reach the AI mentor"),
    ("classroomSubmitStatus", "Could not submit"),
    ("classroomAttemptStatus", "Could not check your attempt"),
    ("classroomChallengeStatus", "Could not check the challenge"),
])
def test_status_widget_failure_messages_are_still_announced(status_var, announce_context):
    """These five status paragraphs lost their local aria-live region (see
    above) specifically because every branch already calls announce() with
    the same text - this proves the failure-path message specifically
    (the one case that previously relied on the live region alone) still
    reaches announce(), so removing the duplicate didn't silently regress
    screen-reader coverage on network failures."""
    with open("static/classroom.js", encoding="utf-8") as fh:
        src = fh.read()
    idx = src.index(announce_context)
    # The matching announce(...) call is the very next one after the
    # message text is assigned, within a small window (same catch block).
    window = src[idx:idx + 200]
    assert "announce(" in window, f"{status_var}: {announce_context!r} is not followed by announce()"
