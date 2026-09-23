"""XRCVC V1 Finding 4 / V2 Finding 4B (Monaco Tab keyboard trap) full closure,
plus the CodeUp How-To Guide contract (docs/guide/quick-how-to-guide.html:
Tab and Shift+Tab leave the editor by default; Ctrl+] / Ctrl+[ indent).

Current contract: new users get the XRCVC-safe behavior, so Tab/Shift+Tab move
out of the editor in the expected direction. Learners can explicitly turn off
"Tab Leaves Editor" to restore Monaco's Tab/Shift+Tab indent/outdent behavior;
Ctrl+]/Ctrl+[ remain available in both modes.

Root cause: accessibilitySupport:'on' only affects ARIA/textarea presentation.
Whether Tab actually exits Monaco is governed by a *separate* global option,
`tabFocusMode`, normally flipped by Monaco's own built-in Ctrl+M action - a
binding this app replaces with leaveEditor(), so tabFocusMode could never be
turned on through any path a user had access to. Confirmed live on production:
editor.getOption(monaco.editor.EditorOption.tabFocusMode) read false, and a
*trusted* Tab keypress inserted an indent instead of leaving the editor.

The underlying Monaco TabFocus service is not part of the public standalone
`monaco` API surface in the bundled version (editor.getAction('editor.action.
toggleTabFocusMode') returns null; editor.trigger() for that id is a silent
no-op) - confirmed by exhausting those paths before falling back to handling
Tab/Shift+Tab directly, the same way Escape and Ctrl+M already are.

This test uses only *trusted* keyboard input (page.keyboard.press), never
dispatchEvent(new KeyboardEvent(...)), because that is exactly the
distinction that mattered here: earlier sessions' synthetic-event tests
missed this defect entirely.

Marked as an integration module (see tests/conftest.py INTEGRATION_MODULES):
needs a real browser and a live server, so it does not run in the default
`pytest -q` quick suite.
"""

from __future__ import annotations

import socket
import threading

import pytest

import app as app_module

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - environment without the dev deps installed
    sync_playwright = None

try:
    from werkzeug.serving import make_server
except ImportError:  # pragma: no cover - werkzeug always ships with Flask
    make_server = None


def _playwright_chromium_available() -> bool:
    if sync_playwright is None:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


_SKIP_REASON = None
if sync_playwright is None:
    _SKIP_REASON = "playwright not installed (see requirements-dev.txt)"
elif not _playwright_chromium_available():
    _SKIP_REASON = "Playwright's Chromium browser is not installed - run `playwright install chromium`"

pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


@pytest.fixture(scope="module")
def live_server():
    """Real HTTP server for the actual Flask app - a Flask test-client
    response is just an HTML string and cannot be scripted by a real
    browser with real keyboard events."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    app_module.app.config.update(TESTING=False)
    server = make_server("127.0.0.1", port, app_module.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _active(page):
    return page.evaluate(
        "() => ({tag: document.activeElement.tagName, id: document.activeElement.id, "
        "cls: (document.activeElement.className||'').toString()})"
    )


def _open_ide(page, live_server, tab_moves_focus=None):
    if tab_moves_focus is not None:
        page.add_init_script(
            f"localStorage.setItem('codeupTabMovesFocus', '{str(tab_moves_focus).lower()}')"
        )
    page.goto(f"{live_server}/ide")
    page.wait_for_selector("#editor")
    page.wait_for_function("typeof editor !== 'undefined' && !!editor")
    page.wait_for_function("window._editorShortcutsRegistered === true")


def _focus_editor(page, code, position=None, selection=None):
    page.evaluate("(c) => editor.setValue(c)", code)
    if position:
        page.evaluate("(p) => editor.setPosition(p)", position)
    if selection:
        page.evaluate(
            "(s) => editor.setSelection(new monaco.Range(s[0], s[1], s[2], s[3]))", selection
        )
    page.evaluate("() => editor.focus()")
    page.wait_for_timeout(150)


def test_jump_to_editor_enters_editing_with_one_activation_and_works_repeatedly(live_server, page):
    _open_ide(page, live_server)
    page.evaluate("() => editor.setValue('')")
    page.locator('.cu-skip-links a[href="#editor"]').focus()
    page.keyboard.press("Enter")
    page.wait_for_function("editor.hasTextFocus()")
    page.keyboard.type("print('first')")
    assert page.evaluate("() => editor.getValue()") == "print('first')"

    page.keyboard.press("Escape")
    assert _active(page)["id"] == "runBtn"
    page.locator('.cu-skip-links a[href="#editor"]').focus()
    page.keyboard.press("Enter")
    page.wait_for_function("editor.hasTextFocus()")
    page.keyboard.type("\nprint('second')")
    assert "print('second')" in page.evaluate("() => editor.getValue()")


def test_jump_to_command_input_focuses_the_real_entry_control_repeatedly(live_server, page):
    _open_ide(page, live_server)
    link = page.locator('.cu-skip-links a[href="#voiceText"]')
    for expected in ("first command", "second command"):
        link.focus()
        page.keyboard.press("Enter")
        assert _active(page)["id"] == "voiceText"
        page.keyboard.type(expected)
        assert page.locator("#voiceText").input_value().endswith(expected)
        page.locator("#voiceText").fill("")


def test_accessibility_shortcut_opens_settings_from_editor(live_server, page):
    _open_ide(page, live_server)
    _focus_editor(page, "print(1)")
    page.keyboard.press("Alt+Shift+o")
    assert page.locator(".cu-header-settings").get_attribute("open") is not None
    assert _active(page)["id"] == "speechModeSelect"


def test_voice_input_shortcut_still_fires_after_tutorial_starts(live_server, page):
    _open_ide(page, live_server)
    page.evaluate("() => window.TutorialController.open()")
    page.wait_for_function("window.TutorialController.active === true")
    page.wait_for_function("!document.getElementById('tutorialOverlay').hidden")
    before = page.locator("#output").inner_text()
    page.locator("#voiceText").focus()
    page.keyboard.press("Control+Shift+m")
    page.wait_for_function(
        "(before) => document.getElementById('output').textContent !== before || "
        "document.getElementById('voiceButton').getAttribute('aria-pressed') !== 'false'",
        arg=before,
    )
    output = page.locator("#output").inner_text()
    pressed = page.locator("#voiceButton").get_attribute("aria-pressed")
    assert (
        pressed in {"true", "mixed"}
        or "Speech recognition is not supported" in output
        or "Microphone access blocked" in output
    )


def test_screen_reader_safe_run_announces_the_output_itself(live_server, page):
    # XRCVC 4c and finding 5: the screen reader must read the output aloud,
    # including list punctuation, not only a pointer to the output panel.
    # The prime program mirrors the report's "stopped at 29" reproduction. Its
    # nested loop also triggers the "High iteration count" semantic note in
    # the same tick, which used to replace the pending output announcement.
    _open_ide(page, live_server)
    code = (
        "primes = []\nfor n in range(2, 100):\n    is_prime = True\n    for d in range(2, n):\n"
        "        if n % d == 0:\n            is_prime = False\n            break\n"
        "    if is_prime:\n        primes.append(n)\n        print(n)\nprint(primes[:3])"
    )
    page.evaluate("(c) => editor.setValue(c)", code)
    page.locator("#runBtn").click()
    page.wait_for_function("document.getElementById('srAnnouncer').textContent.includes('Program output:')")
    announcement = page.locator("#srAnnouncer").inner_text()
    assert "Program output: 2, 3, 5, 7" in announcement
    assert "29, 31" in announcement and "89, 97" in announcement
    assert "open bracket 2 comma 3 comma 5 close bracket" in announcement
    assert announcement.index("97") < announcement.index("High iteration count")


def test_long_program_output_stays_complete_but_live_announcement_is_bounded(live_server, page):
    _open_ide(page, live_server)
    page.evaluate("() => editor.setValue('for i in range(1200):\\n    print(i)')")
    page.locator("#runBtn").click()
    page.wait_for_function("document.getElementById('output').textContent.includes('1199')")
    page.wait_for_function("document.getElementById('srAnnouncer').textContent.includes('Read output again')")
    assert page.locator("#output").inner_text().splitlines()[-1] == "1199"
    announcement = page.locator("#srAnnouncer").inner_text()
    assert announcement.startswith("Program output shortened for speech after 4000 characters: 0, 1, 2")
    output_part = announcement[:announcement.index("Read output again")]
    assert len(output_part) <= 4200
    assert len(announcement) <= 8400


def test_accessibility_support_is_on(live_server, page):
    page.goto(f"{live_server}/ide")
    page.wait_for_selector("#editor")
    page.wait_for_function("typeof editor !== 'undefined' && !!editor")
    support = page.evaluate(
        "() => editor.getOption(monaco.editor.EditorOption.accessibilitySupport)"
    )
    assert support == 2, "accessibilitySupport must remain 'on' (enum value 2)"


@pytest.mark.parametrize(
    "label,code,position,selection",
    [
        ("empty editor", "", None, None),
        ("code present", "print('hello')", None, None),
        ("cursor mid-line", "print('hello world')", {"lineNumber": 1, "column": 8}, None),
        ("selected text", 'print("hello world")', None, [1, 1, 1, 6]),
    ],
)
def test_trusted_tab_exits_editor_without_modifying_code(live_server, page, label, code, position, selection):
    _open_ide(page, live_server, tab_moves_focus=True)
    _focus_editor(page, code, position, selection)
    before = page.evaluate("() => editor.getValue()")

    page.keyboard.press("Tab")
    page.wait_for_timeout(150)

    after = page.evaluate("() => editor.getValue()")
    dest = _active(page)
    assert after == before, f"Tab must not modify code ({label}): {before!r} -> {after!r}"
    assert dest["tag"] != "TEXTAREA", f"Tab must move focus out of Monaco ({label}), stayed on {dest}"
    assert dest["id"] == "runBtn", f"Tab's forward destination should be the real next control ({label}), got {dest}"


@pytest.mark.parametrize(
    "label,code,position,selection",
    [
        ("empty editor", "", None, None),
        ("code present", "print('hello')", None, None),
        ("cursor mid-line", "print('hello world')", {"lineNumber": 1, "column": 8}, None),
        ("selected text", 'print("hello world")', None, [1, 1, 1, 6]),
    ],
)
def test_trusted_shift_tab_exits_editor_backward_without_modifying_code(live_server, page, label, code, position, selection):
    _open_ide(page, live_server, tab_moves_focus=True)
    _focus_editor(page, code, position, selection)
    before = page.evaluate("() => editor.getValue()")

    page.keyboard.press("Shift+Tab")
    page.wait_for_timeout(150)

    after = page.evaluate("() => editor.getValue()")
    dest = _active(page)
    assert after == before, f"Shift+Tab must not modify code ({label}): {before!r} -> {after!r}"
    assert dest["tag"] != "TEXTAREA", f"Shift+Tab must move focus out of Monaco ({label}), stayed on {dest}"
    # The real previous focusable element in DOM order (dynamically
    # determined, not hardcoded) - currently the mode-switch button that
    # sits immediately before the editor region in the template.
    assert dest["id"] == "audioBlocksModeBtn", f"Shift+Tab's backward destination should be the real previous control ({label}), got {dest}"


@pytest.mark.parametrize("tab_moves_focus", [False, True])
def test_ctrl_bracket_indent_and_outdent_still_work(live_server, page, tab_moves_focus):
    _open_ide(page, live_server, tab_moves_focus=tab_moves_focus)

    _focus_editor(page, "print(1)", position={"lineNumber": 1, "column": 1})
    page.keyboard.press("Control+]")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "    print(1)"
    page.keyboard.press("Control+[")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "print(1)"

    _focus_editor(page, "a=1\nb=2\nc=3", selection=[1, 1, 3, 4])
    page.keyboard.press("Control+]")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "    a=1\n    b=2\n    c=3"
    page.keyboard.press("Control+[")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "a=1\nb=2\nc=3"


@pytest.mark.parametrize("tab_moves_focus", [False, True])
def test_escape_while_quiet_leaves_editor(live_server, page, tab_moves_focus):
    _open_ide(page, live_server, tab_moves_focus=tab_moves_focus)
    _focus_editor(page, "print(1)")
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    assert _active(page)["id"] == "runBtn"


_HELD_SPEECH = """
(() => {
  let current = null;
  const fake = {
    speaking: false, pending: false, paused: false,
    getVoices() { return []; }, addEventListener() {}, removeEventListener() {}, pause() {}, resume() {},
    cancel() { window.__cancels = (window.__cancels || 0) + 1; const u = current; current = null; fake.speaking = false;
               if (u && u.onend) setTimeout(() => u.onend({}), 0); },
    speak(u) { current = u; fake.speaking = true; if (u.onstart) setTimeout(() => u.onstart({}), 0);
               // The silent first-gesture primer ends at once, as in Chrome; real speech is held.
               if (!u.text.trim()) setTimeout(() => { if (current === u) { current = null; fake.speaking = false; } if (u.onend) u.onend({}); }, 5); },
  };
  Object.defineProperty(window, 'speechSynthesis', { value: fake, configurable: true });
})();
"""


def test_escape_while_speaking_stops_speech_and_stays_in_editor(live_server, page):
    # Documented contract (editor help, shortcut dialog, report 4a): Escape
    # while CodeUp is speaking only stops speech; a further Escape leaves.
    # Two handlers used to run on one press: the capture listener stopped
    # speech, then Monaco's Escape command saw silence and left the editor.
    page.add_init_script(_HELD_SPEECH)
    _open_ide(page, live_server)
    page.evaluate("() => applySpeechMode('codeup-voice', {silent: true})")
    _focus_editor(page, "abc")
    # Browsers only allow speech after a user gesture, and that first gesture
    # also fires the silent audio primer; do it before CodeUp starts speaking.
    page.keyboard.press("Shift")
    page.wait_for_timeout(100)
    page.evaluate("() => speak('A long sentence that is still being spoken.', {forceFull: true})")
    page.wait_for_function("() => window.speechSynthesis.speaking === true")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert page.evaluate("() => window.__cancels || 0") >= 1
    assert page.evaluate("() => editor.hasTextFocus()"), "the speech-stopping Escape must not also leave the editor"
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert _active(page)["id"] == "runBtn"


@pytest.mark.parametrize("combo", ["Control+Shift+P", "Control+Shift+p"])
def test_command_palette_shortcut_is_case_insensitive(live_server, page, combo):
    # Caps Lock (or a lowercase key from assistive input) delivers "p", which
    # the handler used to ignore even though the shortcut list documents it.
    _open_ide(page, live_server)
    page.locator("#voiceText").focus()
    page.keyboard.press(combo)
    page.wait_for_function(
        "() => { const o = document.getElementById('commandPaletteOverlay'); return !!o && !o.hasAttribute('hidden'); }"
    )


@pytest.mark.parametrize("tab_moves_focus", [False, True])
def test_ctrl_m_leaves_editor(live_server, page, tab_moves_focus):
    _open_ide(page, live_server, tab_moves_focus=tab_moves_focus)
    _focus_editor(page, "print(1)")
    page.keyboard.press("Control+m")
    page.wait_for_timeout(150)
    assert _active(page)["id"] == "runBtn"


# ---- default is trap-free; indentation remains an explicit preference ------

def test_default_trusted_tab_and_shift_tab_leave_editor(live_server, page):
    _open_ide(page, live_server)
    _focus_editor(page, "print(1)")
    page.keyboard.press("Tab")
    page.wait_for_timeout(150)
    assert _active(page)["id"] == "runBtn"

    _focus_editor(page, "print(1)")
    page.keyboard.press("Shift+Tab")
    page.wait_for_timeout(150)
    assert _active(page)["id"] == "audioBlocksModeBtn"

def test_opt_out_mode_keeps_trusted_tab_indent_and_shift_tab_outdent(live_server, page):
    _open_ide(page, live_server, tab_moves_focus=False)
    _focus_editor(page, "for i in range(3):\nprint(i)", position={"lineNumber": 2, "column": 1})
    page.keyboard.press("Tab")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "for i in range(3):\n    print(i)"
    assert page.evaluate("() => editor.hasTextFocus()") is True
    page.keyboard.press("Shift+Tab")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "for i in range(3):\nprint(i)"
    assert page.evaluate("() => editor.hasTextFocus()") is True

    _focus_editor(page, "a=1\nb=2", selection=[1, 1, 2, 4])
    page.keyboard.press("Tab")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "    a=1\n    b=2"


def test_default_mode_describes_tab_navigation_and_keyboard_indentation(live_server, page):
    _open_ide(page, live_server)
    help_text = page.inner_text("#editorHelp")
    assert "Tab moves focus out of the editor" in help_text
    assert "Control right bracket" in help_text
    assert "Control+M" in help_text and "Escape" in help_text
    label = page.evaluate("() => editor.getOption(monaco.editor.EditorOption.ariaLabel)")
    assert "Tab moves focus out of the editor" in label and "Control M" in label
    assert page.get_attribute("#tabFocusToggle", "aria-pressed") == "true"


def test_tab_leaves_editor_setting_toggles_and_persists(live_server, page):
    _open_ide(page, live_server)
    page.evaluate("() => document.querySelector('.cu-header-settings').open = true")
    page.click("#tabFocusToggle")
    page.wait_for_timeout(100)
    assert page.get_attribute("#tabFocusToggle", "aria-pressed") == "false"
    assert "Tab indents code" in page.inner_text("#editorHelp")

    page.reload()
    page.wait_for_function("window._editorShortcutsRegistered === true")
    assert page.get_attribute("#tabFocusToggle", "aria-pressed") == "false"
    _focus_editor(page, "print(1)")
    page.keyboard.press("Tab")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "    print(1)"
    assert page.evaluate("() => editor.hasTextFocus()") is True
