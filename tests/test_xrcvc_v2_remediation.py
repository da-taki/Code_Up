"""Regression tests for the XRCVC Version 2 accessibility remediation pass.

Each test is anchored to one numbered XRCVC finding (see the remediation
report) so a future change that reintroduces a fixed bug fails loudly here
instead of silently regressing. Follows the codebase's existing convention
(test_nvda_review_fixes.py, test_run_output_speech.py, ...) of asserting on
the actual served HTML and the literal static/app.js source rather than
executing JS, since there is no JS DOM test runner wired into pytest.
"""
import re
from pathlib import Path

import pytest

import app as app_module

STATIC_APP = Path("static/app.js").read_text(encoding="utf-8")
CORE_CSS = Path("static/style/core.css").read_text(encoding="utf-8")
UI_CSS = Path("static/style/ui-improvements.css").read_text(encoding="utf-8")
ACCESSIBILITY_HTML = Path("templates/accessibility.html").read_text(encoding="utf-8")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def ide_html(client):
    return client.get("/ide").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Issue 2: "Jump to command input" skip link
# ---------------------------------------------------------------------------

def test_jump_to_command_input_skip_link_exists(client):
    html = ide_html(client)
    assert '<a class="skip-link" href="#voiceText">Jump to command input</a>' in html
    # It must sit between the editor and output skip links, matching the
    # left-to-right layout of the page (editor -> commands -> output).
    editor_pos = html.index('href="#editor"')
    command_pos = html.index('href="#voiceText"')
    output_pos = html.index('href="#output"')
    assert editor_pos < command_pos < output_pos
    # The target must be a real, natively focusable element.
    assert 'id="voiceText"' in html


# ---------------------------------------------------------------------------
# Issue 3 / 13 / 14 / 17: disclosure ("Snippets" etc.) duplicate/odd semantics
# ---------------------------------------------------------------------------

def test_disclosure_triangle_uses_no_content_glyph():
    # A `content: "..."` value on a ::before/::after pseudo-element is folded
    # into the accessible name of an element whose name is computed from
    # content (per the ARIA accname spec) - the old triangle glyph
    # (content: "\25B8") was read aloud by NVDA as "right-pointing small
    # triangle" on every <details>/<summary> disclosure (Snippets, Project
    # files, Block actions, Accessibility settings, Show commands & help,
    # ...), on top of the native "collapsed/expanded" announcement.
    assert '.cu-disclosure-summary::before {' in CORE_CSS
    before_block = CORE_CSS[CORE_CSS.index('.cu-disclosure-summary::before {'):]
    before_block = before_block[:before_block.index('}')]
    assert 'content: "";' in before_block
    assert '25B8' not in before_block
    # Drawn with borders instead, which carry no accessible text.
    assert 'border-left: 5px solid' in before_block


def test_settings_heading_is_connected_not_orphaned(client):
    html = ide_html(client)
    assert '<h2 id="accessibilitySettingsHeading" class="sr-only">Accessibility and speech settings</h2>' in html
    assert 'aria-labelledby="accessibilitySettingsHeading"' in html
    # The old duplicate aria-label that repeated the summary's own visible
    # text (and disagreed with the orphaned H2's wording) must be gone.
    assert html.count('aria-label="CodeUp display and accessibility settings"') == 0


def test_show_commands_help_has_a_heading_but_no_extra_landmark(client):
    html = ide_html(client)
    # A real heading makes the disclosure discoverable by heading-list
    # navigation (NVDA/JAWS "H" key) once expanded...
    assert '<h3 id="cuHelpPanelHeading" class="sr-only">Commands and help</h3>' in html
    # ...but does NOT add a 4th `role="region"` landmark - the IDE has a
    # deliberately bounded set of exactly three (editor/output/commands),
    # enforced by test_nvda_review_fixes.py::test_landmark_labels_are_unique_and_regions_are_limited
    # and test_ide_accessibility_semantics.py::test_no_explicit_role_region_anywhere.
    help_content_start = html.index('<div class="cu-help-content">')
    assert 'role="region"' not in html[help_content_start:help_content_start + 60]


# ---------------------------------------------------------------------------
# Issue 6: structured output punctuation narration
# ---------------------------------------------------------------------------

def test_narration_formatter_helper_exists_and_is_wired_into_run_speech():
    assert "function narrateStructuredOutputLine(" in STATIC_APP
    fmt_start = STATIC_APP.index("function formatRunOutputSpeech(")
    fmt_block = STATIC_APP[fmt_start:fmt_start + 500]
    assert "narrateStructuredOutputLine" in fmt_block
    full_start = STATIC_APP.index("function formatFullOutputSpeech(")
    full_block = STATIC_APP[full_start:full_start + 400]
    assert "narrateStructuredOutputLine" in full_block


# Behavioral coverage for the formatter itself (list/tuple/dict/nested/etc.)
# lives in tests/spoken_code.test.js, run via node - this just guards wiring.


# ---------------------------------------------------------------------------
# Issue 7: keyboard focus styling for the command palette
# ---------------------------------------------------------------------------

def test_command_palette_selected_row_has_a_non_color_indicator():
    assert ".command-palette-results > *.selected," in UI_CSS
    idx = UI_CSS.index(".command-palette-results > *.selected,\n.command-palette-results > *[aria-selected=\"true\"] {\n  box-shadow:")
    assert idx != -1


# ---------------------------------------------------------------------------
# Issue 8 / 10: voice control shortcut discoverability
# ---------------------------------------------------------------------------

def test_voice_button_advertises_its_real_shortcut(client):
    html = ide_html(client)
    voice_btn_start = html.index('id="voiceButton"')
    voice_btn = html[max(0, voice_btn_start - 200):voice_btn_start + 200]
    assert "Ctrl+Shift+M" in voice_btn
    # The actual registered handler really is Ctrl+Shift+M, not Alt+Shift+M
    # (which is a different chord bound to "code map") - the label must not
    # be a documentation mismatch.
    assert "e.ctrlKey && e.shiftKey && e.key === 'M'" in STATIC_APP


# ---------------------------------------------------------------------------
# Issue 18: "Accessibility Options" shortcut
# ---------------------------------------------------------------------------

def test_accessibility_options_shortcut_has_a_real_working_target():
    assert "function openAccessibilityOptionsPanel(" in STATIC_APP
    assert "O: 'open accessibility options'" in STATIC_APP
    assert "openAccessibilityOptionsPanel();" in STATIC_APP
    fn_start = STATIC_APP.index("function openAccessibilityOptionsPanel(")
    fn_block = STATIC_APP[fn_start:fn_start + 500]
    assert "details.open = true;" in fn_block


# ---------------------------------------------------------------------------
# Issue 19: keyboard shortcut reference reachable without knowing a shortcut
# ---------------------------------------------------------------------------

def test_shortcut_help_is_reachable_from_a_visible_button(client):
    html = ide_html(client)
    assert '<button type="button" id="shortcutHelpBtn"' in html
    assert 'id="shortcutHelpModal"' in html
    assert 'role="dialog"' in html[html.index('id="shortcutHelpModal"'):html.index('id="shortcutHelpModal"') + 120]
    assert "function openShortcutHelp()" in STATIC_APP
    assert "shortcutHelpBtn.addEventListener('click', () => openShortcutHelp());" in STATIC_APP
    # Alt+Shift+K opens the same concise modal, not just the giant
    # "what can I do here" command wall.
    assert "} else if (key === 'K') {\n          openShortcutHelp();" in STATIC_APP


def test_shortcut_help_covers_the_core_shortcuts_from_the_ticket(client):
    html = ide_html(client)
    modal_start = html.index('id="shortcutHelpModal"')
    modal_end = html.index('</div>\n\n  <div id="guideModal"')
    modal = html[modal_start:modal_end]
    for expected in ("Run your code", "Focus the editor", "Leave the editor",
                      "Focus the command box", "Focus program output",
                      "accessibility and speech settings", "CodeUp Voice control",
                      "Audio Blocks"):
        assert expected in modal, expected


# ---------------------------------------------------------------------------
# Issue 20: Getting Started guide
# ---------------------------------------------------------------------------

def test_getting_started_guide_is_reachable_from_a_visible_button(client):
    html = ide_html(client)
    assert '<button type="button" id="guideBtn"' in html
    assert 'id="guideModal"' in html
    assert "function openGettingStartedGuide()" in STATIC_APP
    assert "guideBtn.addEventListener('click', () => openGettingStartedGuide());" in STATIC_APP


# ---------------------------------------------------------------------------
# Issue 9: duplicate speech
# ---------------------------------------------------------------------------

def test_run_success_output_is_not_announced_twice():
    # See test_assistive_technology_integration.py::test_frontend_routes_visual_output_to_live_regions
    # for the ordering assertion; this one guards the sr:false itself so a
    # future edit can't silently drop it and reintroduce the duplicate.
    assert "out(data.output, { sr: false });" in STATIC_APP


def test_sonify_start_message_is_not_announced_twice():
    start = STATIC_APP.index("const startMsg = `Sonifying block from line")
    block = STATIC_APP[start:start + 300]
    assert "out(startMsg);" in block
    assert "srAnnounce(startMsg)" not in block


def test_step_narration_stop_message_is_not_announced_twice():
    start = STATIC_APP.index("stopWords.some(w => t === w)")
    block = STATIC_APP[start:start + 700]
    assert "out('Stopped.');" in block
    assert "srAnnounce('Stopped');" not in block


def test_output_region_live_announcement_toggles_with_speech_mode():
    # The single biggest source of "CodeUp Voice and NVDA speaking
    # simultaneously": #output's aria-live="polite" is static HTML, so
    # {sr:false} on out() (which only skips CodeUp's own srAnnounce push)
    # could never stop a concurrently running screen reader from
    # independently announcing #output's own text the instant it changed -
    # duplicating CodeUp Voice's own spoken narration of the same content.
    fn_start = STATIC_APP.index("function updateSpeechModeUI(")
    fn_block = STATIC_APP[fn_start:fn_start + 2400]
    assert "outputRegion.setAttribute('aria-live', _browserSpeechEnabled ? 'off' : 'polite');" in fn_block
    assert "mentorRegion.setAttribute('aria-live', _browserSpeechEnabled ? 'off' : 'polite');" in fn_block


def test_switching_to_screen_reader_safe_cancels_in_flight_speech():
    fn_start = STATIC_APP.index("function applySpeechMode(")
    fn_block = STATIC_APP[fn_start:fn_start + 900]
    assert "wasBrowserSpeechEnabled && !_browserSpeechEnabled" in fn_block
    assert "SpeechManager.cancelAll();" in fn_block


# ---------------------------------------------------------------------------
# Issue 12: Audio Blocks Mode - focus follows block navigation
# ---------------------------------------------------------------------------

def test_audio_blocks_navigation_moves_real_focus_onto_the_current_block():
    fn_start = STATIC_APP.index("function renderAudioBlocks(")
    fn_block = STATIC_APP[fn_start:fn_start + 6800]
    assert "focusWasInBlocksUI" in fn_block
    assert "currentItem.focus();" in fn_block


# ---------------------------------------------------------------------------
# Issue 16: tutorial focus vs. voice shortcut
# ---------------------------------------------------------------------------

def test_voice_toggle_shortcut_has_no_editable_target_guard():
    # If this chord ever grows an `editableTarget` guard (like the bare "2"
    # stop-listening shortcut has), Ctrl+Shift+M would stop working while
    # the tutorial has focused #voiceText - reproducing XRCVC's report.
    idx = STATIC_APP.index("if (e.ctrlKey && e.shiftKey && e.key === 'M')")
    line = STATIC_APP[idx:STATIC_APP.index("\n", idx)]
    assert "editableTarget" not in line


def test_tutorial_has_no_stoppropagation_that_could_swallow_the_shortcut():
    tutorial_js = Path("static/tutorial.js").read_text(encoding="utf-8")
    assert "stopPropagation" not in tutorial_js


# ---------------------------------------------------------------------------
# Issue 15 / 21: contrast and text size
# ---------------------------------------------------------------------------

def test_accessibility_help_page_respects_dark_mode_preference():
    assert "@media (prefers-color-scheme: dark)" in ACCESSIBILITY_HTML
    dark_block = ACCESSIBILITY_HTML[ACCESSIBILITY_HTML.index("@media (prefers-color-scheme: dark)"):]
    assert "background: #0a0703" in dark_block


def _rule_font_size_rem(css_text, selector):
    """Find `selector { ... font-size: N rem ... }` and return N as a float.

    Numeric extraction instead of a hardcoded substring check: the XRCVC
    full re-audit (Sept 2026) raised these floors again (0.86rem->0.9rem,
    0.85rem->0.9rem/0.92rem), and an exact-string assertion on the old
    values would fail on every future genuine improvement, not just on a
    regression - so this asserts a numeric floor instead of a fixed number.
    """
    pattern = re.compile(re.escape(selector) + r"\s*\{[^}]*font-size:\s*([\d.]+)rem", re.DOTALL)
    match = pattern.search(css_text)
    assert match, f"could not find a font-size rule for {selector!r}"
    return float(match.group(1))


def test_secondary_help_text_is_not_tiny():
    # Command tips / settings descriptions / tutorial status+help text are
    # real reading content (not decorative badges) and must clear a 13.6px+
    # floor (0.85rem+ at a 16px root) rather than the old ~11-13px sizes.
    assert _rule_font_size_rem(CORE_CSS, ".cu-settings-description") >= 0.85
    assert _rule_font_size_rem(CORE_CSS, ".cu-command-tip") >= 0.85
    assert _rule_font_size_rem(UI_CSS, ".cu-tutorial-status") >= 0.85
    assert _rule_font_size_rem(UI_CSS, ".cu-tutorial-help") >= 0.85


# ---------------------------------------------------------------------------
# CodeUp User Guide + WCAG audit finding D1: Ctrl+Shift+P was documented in
# three different places (the accessibility help page, the User Guide, and
# the app's own in-product shortcut dialog) with two different, conflicting
# names for the same shortcut ("command help" vs. "Command palette").
# Application behavior (Ctrl+Shift+P opens a fuzzy command-search overlay,
# Alt+Shift+K opens the actual keyboard shortcut reference) is intentional
# and correctly implemented, so the fix corrects the one outlier - the
# /accessibility page - to match the app's own consistent labeling instead
# of touching any keybinding.
# ---------------------------------------------------------------------------

def test_accessibility_help_page_agrees_with_in_app_shortcut_labels():
    # The in-app "Keyboard shortcuts" dialog and the help-command text in
    # app.js are the source of truth for what each shortcut is actually
    # called; both already call Ctrl+Shift+P "Command palette" and
    # Alt+Shift+K the shortcut-list opener.
    index_html = Path("templates/index.html").read_text(encoding="utf-8")
    assert "Ctrl+Shift+P</span> — Command palette" in index_html
    assert "Alt+Shift+K</span> — Open this shortcut list" in index_html
    assert "- Ctrl+Shift+P: Command palette" in STATIC_APP
    assert "- Alt+Shift+K: Show this full shortcut list" in STATIC_APP

    # The standalone /accessibility page used to call the same Ctrl+Shift+P
    # chord "command help" - a different feature - and never mentioned
    # Alt+Shift+K at all. It must now describe both shortcuts the same way
    # the rest of the product does.
    assert "Press Control+Shift+P to open command help." not in ACCESSIBILITY_HTML
    assert "Press Control+Shift+P to open the command palette." in ACCESSIBILITY_HTML
    assert "Alt+Shift+K" in ACCESSIBILITY_HTML


# ---------------------------------------------------------------------------
# CodeUp User Guide + WCAG audit finding D4: dismissing the getting-started
# banner removed the focused Dismiss button from layout without first
# moving focus anywhere, so focus silently fell back to <body> with no
# visible indicator on screen.
# ---------------------------------------------------------------------------

def test_dismissing_start_banner_moves_focus_to_next_focusable_control(client):
    html = ide_html(client)
    # The fix must look up the real next-in-DOM-order focusable element via
    # the same helper leaveEditorBackward() already uses for the analogous
    # backward case, not a hardcoded destination.
    assert "_getFocusableElements()" in html or "_getFocusableElements" in STATIC_APP
    assert 'id="cuStartBannerDismiss"' in html
    script_start = html.index("dismiss.addEventListener('click'")
    handler = html[script_start:script_start + 1200]
    assert "_getFocusableElements" in handler
    assert "next.focus()" in handler
    # The lookup must happen (or be captured) before the banner is hidden,
    # not after - otherwise the Dismiss button is already out of layout
    # and _getFocusableElements() would never find it.
    idx_lookup = handler.index("focusable.indexOf(dismiss)")
    idx_hide = handler.index("banner.style.display = 'none'")
    assert idx_lookup < idx_hide
