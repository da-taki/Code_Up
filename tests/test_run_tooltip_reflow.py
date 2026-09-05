"""XRCVC Finding 9 (Run tooltip) full closure: the Run button's tooltip
(static/style/ui-improvements.css `[data-tooltip]::after`, `attr(data-tooltip)`)
used to be centered under the button via `left:50%; transform:translateX(-50%)`.
#runBtn is always the first control in .cu-command-bar, so at any narrow or
zoomed layout the tooltip's computed left offset goes negative and <body>'s
own `overflow-x:hidden` silently clips the off-screen portion - reproduced
live at a 640px viewport (button left ~36px, tooltip would start ~-45px).

This is a real headless-Chromium/Playwright measurement (matching this
repo's existing convention in test_accessibility_axe.py) rather than a
class-name-presence check, because the bug is specifically about *rendered
pixel position* relative to the *actual viewport width* at several sizes -
something no jsdom/string-based test can observe.

Marked as an integration module (see tests/conftest.py INTEGRATION_MODULES)
for the same reason test_accessibility_axe.py is: needs a real browser and
a live server, so it does not run in the default `pytest -q` quick suite.
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
    """Real HTTP server for the actual Flask app - see
    test_accessibility_axe.py::live_server for why a real server (not the
    Flask test client) is required for a real browser to load /ide."""
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


def _measure_run_tooltip(page, trigger):
    """trigger: 'hover' or 'focus'. Returns pixel measurements of #runBtn
    and its ::after tooltip pseudo-element, plus document scroll metrics."""
    if trigger == "hover":
        page.hover("#runBtn")
    elif trigger == "focus":
        page.focus("#runBtn")
    else:
        raise ValueError(trigger)

    return page.evaluate(
        """
        () => {
          const btn = document.getElementById('runBtn');
          const btnRect = btn.getBoundingClientRect();
          const after = getComputedStyle(btn, '::after');
          const tooltipLeft = btnRect.left + (parseFloat(after.left) || 0);
          const tooltipWidth = parseFloat(after.width) || 0;
          return {
            content: after.content,
            color: after.color,
            backgroundColor: after.backgroundColor,
            fontSize: after.fontSize,
            btnLeft: btnRect.left,
            btnRight: btnRect.right,
            btnTop: btnRect.top,
            tooltipLeft: tooltipLeft,
            tooltipWidth: tooltipWidth,
            tooltipRight: tooltipLeft + tooltipWidth,
            viewportWidth: window.innerWidth,
            docScrollWidth: document.documentElement.scrollWidth,
            docClientWidth: document.documentElement.clientWidth,
          };
        }
        """
    )


WIDTHS = [(320, 640), (640, 800), (1280, 800)]


@pytest.mark.parametrize("width,height", WIDTHS)
@pytest.mark.parametrize("trigger", ["hover", "focus"])
def test_run_tooltip_fully_visible_and_no_page_overflow(live_server, browser, width, height, trigger):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    try:
        page.goto(f"{live_server}/ide")
        page.wait_for_selector("#runBtn")

        baseline_scroll_width = page.evaluate("document.documentElement.scrollWidth")
        m = _measure_run_tooltip(page, trigger)

        assert m["content"] not in (None, "none", '""'), (
            f"tooltip did not render on {trigger} at {width}px - Finding 9 requires it on both "
            "hover and keyboard focus"
        )
        assert m["tooltipLeft"] >= 0, (
            f"tooltip left edge is at {m['tooltipLeft']}px (negative) on {trigger} at {width}px viewport - "
            f"it is being clipped by <body>'s overflow-x:hidden (btnLeft={m['btnLeft']})"
        )
        assert m["tooltipRight"] <= m["viewportWidth"], (
            f"tooltip right edge is at {m['tooltipRight']}px but the viewport is only "
            f"{m['viewportWidth']}px wide on {trigger} at {width}px viewport"
        )
        assert m["docScrollWidth"] <= m["viewportWidth"] + 1, (
            f"the tooltip caused the whole page to gain horizontal scroll: "
            f"scrollWidth={m['docScrollWidth']} at a {width}px viewport (baseline was {baseline_scroll_width})"
        )
        # Sanity: the tooltip must not have silently grown the page's own
        # scrollWidth relative to before it appeared (a regression that a
        # `>= 0` / `<= viewport` check alone wouldn't catch if the viewport
        # itself were being reported post-overflow).
        assert m["docScrollWidth"] <= baseline_scroll_width + 1
    finally:
        ctx.close()


def test_run_tooltip_keeps_accessible_name_and_shortcut_text(live_server, browser):
    """Finding 9 in full: a visible "Run" label, a correct accessible name,
    and shortcut metadata documented in *both* the accessible name (for a
    screen reader, which never sees the hover/focus-only ::after tooltip)
    and the visible tooltip text (for a sighted keyboard user who is not
    running a screen reader)."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    try:
        page.goto(f"{live_server}/ide")
        page.wait_for_selector("#runBtn")
        btn = page.locator("#runBtn")
        assert btn.inner_text().strip() == "Run"
        assert btn.get_attribute("aria-label") == "Run Python code (Ctrl+Enter)"
        assert "Ctrl+Enter" in (btn.get_attribute("data-tooltip") or "")
        m = _measure_run_tooltip(page, "focus")
        assert "Ctrl+Enter" in m["content"], "the visible tooltip must also carry the shortcut, not just aria-label"
    finally:
        ctx.close()


@pytest.mark.parametrize("theme_setup,label", [
    (None, "default theme"),
    ("document.getElementById('nightToggle').click();", "Night Mode"),
    (
        "document.getElementById('colorVisionMode').value = 'high-contrast';"
        "document.getElementById('colorVisionMode').dispatchEvent(new Event('change'));",
        "High Contrast",
    ),
])
def test_run_tooltip_readable_in_each_theme(live_server, browser, theme_setup, label):
    """Finding 9 requires the tooltip to remain readable (not just present)
    in Night Mode and High Contrast, not only the default theme."""
    ctx = browser.new_context(viewport={"width": 640, "height": 800})
    page = ctx.new_page()
    try:
        page.goto(f"{live_server}/ide")
        page.wait_for_selector("#runBtn")
        if theme_setup:
            page.evaluate(theme_setup)
            page.wait_for_timeout(150)
        m = _measure_run_tooltip(page, "focus")
        assert m["content"] not in (None, "none", '""'), f"tooltip missing in {label}"

        def to_rgb(css_color):
            nums = [float(x) for x in css_color[css_color.find("(") + 1:css_color.find(")")].split(",")[:3]]
            return nums

        def luminance(rgb):
            def lin(c):
                c = c / 255
                return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            r, g, b = rgb
            return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

        fg = to_rgb(m["color"])
        bg = to_rgb(m["backgroundColor"])
        l1, l2 = luminance(fg), luminance(bg)
        lighter, darker = max(l1, l2), min(l1, l2)
        ratio = (lighter + 0.05) / (darker + 0.05)
        assert ratio >= 4.5, f"tooltip contrast in {label} is only {ratio:.2f}:1 (needs >= 4.5:1)"
        assert m["tooltipLeft"] >= 0 and m["tooltipRight"] <= m["viewportWidth"], (
            f"tooltip clipped in {label} at 640px: left={m['tooltipLeft']} right={m['tooltipRight']} "
            f"viewport={m['viewportWidth']}"
        )
    finally:
        ctx.close()
