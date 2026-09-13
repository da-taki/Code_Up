"""XRCVC V1 Finding 4 / V2 Finding 4B (Monaco Tab keyboard trap) full closure,
plus the CodeUp How-To Guide contract ("Tab inside the editor indents code").

Current contract: by default Tab/Shift+Tab indent/outdent inside the editor, as
the guide promises, while Escape (when quiet) and Ctrl+M always leave it - so
the editor is never a keyboard trap. The XRCVC Tab-exits-editor behavior below
is kept as an opt-in "Tab Leaves Editor" setting (localStorage
codeupTabMovesFocus = 'true'), and its trusted-keypress tests run with it on.

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


def _open_ide(page, live_server, tab_moves_focus=False):
    if tab_moves_focus:
        page.add_init_script("localStorage.setItem('codeupTabMovesFocus', 'true')")
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


@pytest.mark.parametrize("tab_moves_focus", [False, True])
def test_ctrl_m_leaves_editor(live_server, page, tab_moves_focus):
    _open_ide(page, live_server, tab_moves_focus=tab_moves_focus)
    _focus_editor(page, "print(1)")
    page.keyboard.press("Control+m")
    page.wait_for_timeout(150)
    assert _active(page)["id"] == "runBtn"


# ---- guide contract: default Tab indents ------------------------------------

def test_default_trusted_tab_indents_and_shift_tab_outdents_inside_editor(live_server, page):
    _open_ide(page, live_server)
    _focus_editor(page, "for i in range(3):\nprint(i)", position={"lineNumber": 2, "column": 1})

    page.keyboard.press("Tab")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "for i in range(3):\n    print(i)"
    assert page.evaluate("() => editor.hasTextFocus()") is True, "Tab must keep focus in the editor by default"

    page.keyboard.press("Shift+Tab")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "for i in range(3):\nprint(i)"
    assert page.evaluate("() => editor.hasTextFocus()") is True

    # Multi-line selection indents every selected line.
    _focus_editor(page, "a=1\nb=2", selection=[1, 1, 2, 4])
    page.keyboard.press("Tab")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "    a=1\n    b=2"


def test_default_mode_describes_tab_indent_and_the_way_out(live_server, page):
    _open_ide(page, live_server)
    help_text = page.inner_text("#editorHelp")
    assert "Tab indents code" in help_text
    assert "Control+M" in help_text and "Escape" in help_text
    label = page.evaluate("() => editor.getOption(monaco.editor.EditorOption.ariaLabel)")
    assert "Tab indents code" in label and "Control M" in label
    assert page.get_attribute("#tabFocusToggle", "aria-pressed") == "false"


def test_tab_leaves_editor_setting_toggles_and_persists(live_server, page):
    _open_ide(page, live_server)
    page.evaluate("() => document.querySelector('.cu-header-settings').open = true")
    page.click("#tabFocusToggle")
    page.wait_for_timeout(100)
    assert page.get_attribute("#tabFocusToggle", "aria-pressed") == "true"
    assert "Tab moves focus out of the editor" in page.inner_text("#editorHelp")

    page.reload()
    page.wait_for_function("window._editorShortcutsRegistered === true")
    assert page.get_attribute("#tabFocusToggle", "aria-pressed") == "true"
    _focus_editor(page, "print(1)")
    page.keyboard.press("Tab")
    page.wait_for_timeout(150)
    assert page.evaluate("() => editor.getValue()") == "print(1)"
    assert _active(page)["id"] == "runBtn"
