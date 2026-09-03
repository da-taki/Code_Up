"""Regression tests for the UX/accessibility pass that made the student IDE
the default entry point and added an explicit, persisted speech-mode
architecture (Screen Reader Safe / CodeUp Voice) so CodeUp's own voice and a
running NVDA/JAWS/VoiceOver session are never both narrating the same event.

A third "Manual" mode existed briefly during development but was removed:
it was not behaviorally distinguishable from Screen Reader Safe (both keep
automatic speech off and both still let explicit commands like "read output
again" speak), so keeping it around would have been a second control that
could silently drift out of sync with the first - exactly the kind of
duplicated, driftable state this architecture is designed to prevent.

These are code-level/structural checks (following this repo's existing
convention in test_nvda_review_fixes.py of asserting against the actual
source text and DOM, not a jsdom re-implementation of app.js) - they do not
substitute for a real NVDA/JAWS pass. See the manual checklist in the PR
description for what still needs one.
"""

import re
from pathlib import Path

import pytest

import app as app_module

STATIC_APP = Path("static/app.js").read_text(encoding="utf-8")
INDEX_HTML = Path("templates/index.html").read_text(encoding="utf-8")


@pytest.fixture
def client():
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as test_client:
        yield test_client


# ---- startup flow: the editor is the default route --------------------------

def test_root_redirects_straight_into_the_ide(client):
    resp = client.get("/")
    assert resp.status_code in (301, 302, 303, 307, 308)
    assert resp.headers["Location"].rstrip("/").endswith("/ide")


def test_root_redirect_does_not_skip_session_setup(client):
    # A blind learner's very first request must still get a verified session
    # cookie even though "/" no longer renders a page of its own.
    client.get("/")
    cookie = client.get_cookie(app_module.SESSION_COOKIE_NAME)
    raw = getattr(cookie, "value", cookie)
    assert raw
    assert app_module._verify_session_id(raw)


def test_old_marketing_landing_still_exists_at_welcome(client):
    # Nothing was deleted - the marketing page just isn't the thing a
    # student has to click through before they can code.
    resp = client.get("/welcome")
    assert resp.status_code == 200
    assert b'id="root"' in resp.data
    assert b"/static/landing/dist/bundle.js" in resp.data


def test_ide_route_still_serves_the_editor_directly(client):
    resp = client.get("/ide")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="editor"' in html
    assert 'id="mainContent"' in html


# ---- secondary navigation: classroom/instructor/settings stay reachable -----

def test_ide_exposes_a_labelled_secondary_navigation(client):
    html = client.get("/ide").get_data(as_text=True)
    nav_match = re.search(r'<nav\b[^>]*aria-label="More CodeUp features"[^>]*>.*?</nav>', html, re.DOTALL)
    assert nav_match, "secondary navigation nav landmark not found"
    nav_html = nav_match.group(0)
    for href in ("/classroom", "/classroom/instructor", "/accessible-coding-tools", "/accessibility"):
        assert f'href="{href}"' in nav_html, f"secondary nav missing link to {href}"


def test_secondary_nav_link_targets_resolve_without_error(client):
    # Each secondary-nav destination must be a real route that responds
    # gracefully (redirect-to-join/login is fine) rather than 404/500.
    for href in ("/classroom", "/classroom/instructor", "/accessible-coding-tools", "/accessibility"):
        resp = client.get(href)
        assert resp.status_code < 500
        assert resp.status_code != 404


def test_secondary_nav_links_have_a_contrast_safe_color_rule():
    """Regression: the secondary nav's <a> tags don't inherit the nav's own
    inline color (anchors need their own explicit color rule, or the
    browser's default link blue wins) - axe-core found this live at 2.09:1
    contrast against the night-theme panel background, well under the
    4.5:1 AA threshold for normal text."""
    css = Path("static/style/core.css").read_text(encoding="utf-8")
    assert ".cu-secondary-nav a" in css
    assert "var(--text-dim)" in re.search(r"\.cu-secondary-nav a[^}]*\}", css).group(0)


def test_night_theme_primary_buttons_have_readable_text_contrast():
    """Regression: .cu-button-primary (Run, Compile, Submit, Configure...)
    hardcodes white text, which axe-core measured at 2.58:1 against night
    theme's --accent (#ff7a47) - under the 4.5:1 AA threshold. Dark text
    against the same accent clears 7.5:1+, matching the precedent already
    set by .cu-button-voice--paused's own night-theme text-color override."""
    css = Path("static/style/core.css").read_text(encoding="utf-8")
    assert re.search(r"body\.theme-night \.cu-button-primary[^{]*\{\s*color:\s*#1a0800;", css)


def test_secondary_nav_is_not_counted_as_an_extra_named_region():
    # A <nav> landmark is expected and fine; it must not add a 4th
    # aria-labelledby "region" alongside the three deliberately-justified
    # ones (see test_landmark_labels_are_unique_and_regions_are_limited).
    assert 'aria-label="More CodeUp features"' in INDEX_HTML
    nav_tag = re.search(r'<nav[^>]*aria-label="More CodeUp features"[^>]*>', INDEX_HTML).group(0)
    assert "aria-labelledby" not in nav_tag


# ---- explicit speech modes ---------------------------------------------------

# ---- focus audit -------------------------------------------------------------

def test_skip_to_editor_actually_moves_keyboard_focus():
    """Regression, live-verified in a real browser: the "Jump to editor"
    skip link only scrolled #editor into view - a plain <div> with no
    tabindex is not a native focus target for a hash jump, so keyboard
    focus silently stayed on <body>. "Jump to output" worked only because
    #output has tabindex="0". The fix calls Monaco's own editor.focus()
    so the skip link's promise (jump TO the editor, not just near it) is
    actually kept."""
    assert 'querySelector(\'.cu-skip-links a[href="#editor"]\')' in STATIC_APP
    handler_match = re.search(r"skipToEditor\.addEventListener\('click', \(event\) => \{[\s\S]*?\n    \}\);", STATIC_APP)
    assert handler_match, "skip-to-editor click handler not found"
    assert "editor.focus();" in handler_match.group(0)


# ---- axe-core findings (live browser scan against /ide) --------------------

@pytest.mark.parametrize("element_id", ["editor", "structureContent", "snippetList", "projectFileList", "inputsPanelList"])
def test_labelled_generic_divs_have_a_supporting_role(element_id, client):
    """Regression: axe-core's aria-prohibited-attr rule flagged these divs
    live - aria-label/aria-labelledby "is not well supported on a div with
    no valid role attribute" per axe. role="group" is a neutral container
    role that makes the existing label valid without adding a landmark
    (group is not in the landmark role set), without touching Monaco's own
    internal accessibility tree for #editor specifically."""
    html = client.get("/ide").get_data(as_text=True)
    tag_match = re.search(rf'<div id="{element_id}"[^>]*>', html)
    assert tag_match, f"#{element_id} not found"
    assert 'role="group"' in tag_match.group(0)


def test_speech_mode_control_exists_with_exactly_two_options():
    select_match = re.search(r'<select id="speechModeSelect"[\s\S]*?</select>', INDEX_HTML)
    assert select_match, "speechModeSelect control not found"
    select_html = select_match.group(0)
    for value in ("sr-safe", "codeup-voice"):
        assert f'value="{value}"' in select_html
    assert select_html.count("<option") == 2, "speech mode must offer exactly two options, no third Manual mode"
    assert 'id="speechModeDescription"' in INDEX_HTML


def test_speech_modes_are_defined_once_and_reused(client):
    assert "const SPEECH_MODES = ['sr-safe', 'codeup-voice']" in STATIC_APP
    assert "function applySpeechMode(" in STATIC_APP
    assert "function updateSpeechModeUI(" in STATIC_APP
    html = client.get("/ide").get_data(as_text=True)
    assert "speechModeSelect" in html


def test_speech_mode_selector_is_wired_and_persisted():
    assert "speechModeSelect.addEventListener('change', function () { applySpeechMode(this.value); })" in STATIC_APP
    assert "localStorage.setItem(SPEECH_MODE_KEY, mode)" in STATIC_APP


def test_first_visit_defaults_to_screen_reader_safe_not_codeup_voice():
    """The non-negotiable outcome: opening CodeUp for the first time with
    NVDA/JAWS/VoiceOver already running must not immediately start a second,
    competing voice. Regression target: a brand-new visitor (no legacy keys,
    no stored speech mode) must resolve to 'sr-safe', not the historical
    'browser speech on by default' behavior."""
    func_match = re.search(r"function restoreAccessibilityPreferences\(\)[\s\S]*?\n\}", STATIC_APP)
    assert func_match, "restoreAccessibilityPreferences() not found"
    body = func_match.group(0)
    # No stored mode and no legacy "screen reader mode was off" marker must
    # resolve to 'sr-safe' - the ternary's false branch is the conservative
    # default, taken whenever legacyScreenReaderMode is anything but 'false'
    # (including null, i.e. never set - the true-first-visit case).
    assert "storedMode = legacyScreenReaderMode === 'false' ? 'codeup-voice' : 'sr-safe';" in body


def test_returning_users_legacy_toggle_choice_is_migrated_not_silently_reset():
    func_match = re.search(r"function restoreAccessibilityPreferences\(\)[\s\S]*?\n\}", STATIC_APP)
    body = func_match.group(0)
    assert "localStorage.getItem('codeupScreenReaderMode')" in body


def test_only_applySpeechMode_writes_the_derived_state():
    """Single-source-of-truth guard: _screenReaderModeEnabled and
    _browserSpeechEnabled must only ever be assigned inside
    applySpeechMode() (and their `let` declarations) - never by any other
    function - so there is no code path that can leave the mode dropdown
    saying one thing while the flags speak() actually reads say another."""
    assignments = re.findall(r'_screenReaderModeEnabled\s*=[^=]|_browserSpeechEnabled\s*=[^=]', STATIC_APP)
    # Expect exactly: the two `let ... = ...;` declarations, plus the two
    # assignments inside applySpeechMode().
    assert len(assignments) == 4, f"unexpected number of writers to the derived speech flags: {assignments}"
    apply_match = re.search(r"function applySpeechMode\(mode, opts = \{\}\) \{[\s\S]*?\n\}", STATIC_APP)
    assert apply_match
    body = apply_match.group(0)
    assert "_screenReaderModeEnabled = mode !== 'codeup-voice';" in body
    assert "_browserSpeechEnabled = mode === 'codeup-voice';" in body


# ---- explicit narration bypass without double-announcing --------------------

def test_speak_allows_explicit_requests_through_in_quiet_modes():
    func_match = re.search(r"function speak\(text, opts = \{\}\) \{[\s\S]*?\n\}", STATIC_APP)
    assert func_match, "speak() not found"
    body = func_match.group(0)
    assert "opts.explicit === true" in body
    assert "_speechMode !== 'codeup-voice'" in body


def test_explicit_bypass_speaks_once_not_twice():
    """Regression guard for rule #5 (prevent double announcements): when
    explicitBypass is true, speak() must fall straight through to
    VoiceEngine.speak and never also reach the srAnnounce() branch for the
    same event - otherwise a screen-reader user asking for "read output
    again" in Screen Reader Safe mode would hear it once from their AT
    reading the live region and again from CodeUp's own voice."""
    func_match = re.search(r"function speak\(text, opts = \{\}\) \{[\s\S]*?\n\}", STATIC_APP)
    body = func_match.group(0)
    # The only srAnnounce() call in speak() lives inside a guard that
    # explicitly excludes the explicit-bypass case, and returns immediately -
    # it can never run on the same invocation that reaches VoiceEngine.speak.
    guard_match = re.search(
        r"if \(!_browserSpeechEnabled && !explicitBypass\) \{\s*"
        r"if \(opts\.sr !== false\) srAnnounce\([^;]+;\s*"
        r"return;\s*\}",
        body,
    )
    assert guard_match, "srAnnounce() is no longer gated behind !explicitBypass in speak()"


def test_read_output_again_and_repeat_speech_are_marked_explicit():
    speak_output_match = re.search(r"function speakOutput\(\)[\s\S]*?\n\}", STATIC_APP)
    assert speak_output_match and "explicit: true" in speak_output_match.group(0)
    repeat_match = re.search(r"function repeatLastSpeech\(\)[\s\S]*?\n\}", STATIC_APP)
    assert repeat_match and "explicit: true" in repeat_match.group(0)


def test_run_error_alert_does_not_duplicate_into_codeup_voice_mode():
    """Regression: runCode()'s error branch used to call
    speak(shortError, {sr:false, priority:'assertive'}) and then
    srAlert(shortError) unconditionally - the srAnnounce fallback inside
    speak() was correctly suppressed by sr:false, but the very next line
    announced the identical text via the assertive live region anyway. In
    Screen Reader Safe mode that was harmless (speak() produced nothing, so
    srAlert() was the only announcement), but in CodeUp Voice mode speak()
    spoke the error audibly AND srAlert() announced it via ARIA at the same
    time - live-verified in a real browser as two overlapping announcements
    of "Error: Line 1: ZeroDivisionError: division by zero". srAlert() must
    now be conditional on not already being covered by audible speech."""
    error_branch = re.search(
        r"speak\(`Error\$\{lineHint\}: \$\{lastLine\}`, \{ sr: false, priority: 'assertive' \}\);"
        r"[\s\S]{0,400}?srAlert\(`Error\$\{lineHint\}: \$\{lastLine\}`\);",
        STATIC_APP,
    )
    assert error_branch, "error-speak/srAlert pairing not found in the expected shape"
    assert "if (_speechMode !== 'codeup-voice') srAlert(" in error_branch.group(0)


def test_classroom_announce_helper_does_not_duplicate_into_codeup_voice_mode():
    """Regression: classroom.js's shared announce(text, opts) helper - used
    by all ~40 classroom narration call sites (assignment status, help
    requests, guided lessons, submissions, quizzes, challenges) - called
    speak(text, {sr:false}) and then srAnnounce(text, ...) unconditionally.
    In Screen Reader Safe mode this was fine (speak() produced nothing), but
    in CodeUp Voice mode every one of those 40+ events would speak audibly
    AND announce via ARIA at the same time. announce() must skip the
    srAnnounce() call once speak() has already covered it audibly."""
    classroom_js = Path("static/classroom.js").read_text(encoding="utf-8")
    announce_match = re.search(r"function announce\(text, opts\) \{[\s\S]*?\n  \}", classroom_js)
    assert announce_match, "announce() not found in classroom.js"
    body = announce_match.group(0)
    assert "_speechMode" in body and "codeup-voice" in body
    assert re.search(r"if \(!isCodeupVoice[\s\S]*?srAnnounce\(", body), (
        "srAnnounce() in announce() is no longer gated behind the CodeUp Voice mode check"
    )


def test_classroom_speech_mode_check_uses_shared_global_scope():
    """classroom.js and app.js are both loaded as plain <script> tags (not
    modules), so a bare `_speechMode` reference in classroom.js resolves to
    app.js's `let _speechMode` binding via the shared classic-script global
    lexical scope - confirmed live in a real browser tab. This just guards
    against someone "fixing" the reference to `window._speechMode` (which
    would silently always read undefined, since top-level `let` never
    becomes a window property) and reintroducing the duplicate-speech bug
    without any test noticing."""
    classroom_js = Path("static/classroom.js").read_text(encoding="utf-8")
    assert "window._speechMode" not in classroom_js
    index_html = Path("templates/index.html").read_text(encoding="utf-8")
    app_pos = index_html.index('src="/static/app.js"')
    classroom_pos = index_html.index('src="/static/classroom.js"')
    assert app_pos < classroom_pos, "app.js must load before classroom.js so _speechMode exists first"
    assert 'type="module"' not in re.search(r'<script[^>]*classroom\.js[^>]*>', index_html).group(0)


def test_applying_a_mode_sets_both_derived_flags_atomically():
    apply_match = re.search(r"function applySpeechMode\(mode, opts = \{\}\) \{[\s\S]*?\n\}", STATIC_APP)
    assert apply_match
    body = apply_match.group(0)
    assert "_screenReaderModeEnabled = mode !== 'codeup-voice';" in body
    assert "_browserSpeechEnabled = mode === 'codeup-voice';" in body
    # Both are written before either is persisted or read back out, so a
    # concurrent read (e.g. from speak()) can never observe a half-applied
    # mode switch.
    assert body.index("_screenReaderModeEnabled = mode") < body.index("localStorage.setItem(SPEECH_MODE_KEY")
    assert body.index("_browserSpeechEnabled = mode") < body.index("localStorage.setItem(SPEECH_MODE_KEY")
