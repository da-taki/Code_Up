"""Persistent automated accessibility regression suite: renders the real
student IDE and classroom pages in a real headless Chromium (via Playwright)
against a real running instance of this Flask app, and runs the actual
axe-core engine (via axe-playwright-python, which bundles axe-core locally -
no network access needed) against each one.

This is deliberately NOT a jsdom-based check: axe-core's color-contrast rule
needs real CSS layout/paint to compute foreground/background colors, which
jsdom does not implement. A real browser is the only way to catch the class
of bug this suite exists for (the night-theme button contrast failure and
the unstyled-link contrast failures found during the accessibility audit).

Marked as an integration module (see tests/conftest.py INTEGRATION_MODULES)
because it needs a real browser and a live server - it does not run in the
default `pytest -q` quick suite, only with `--run-full` or when explicitly
selected (see the dedicated "accessibility" CI job in
.github/workflows/test.yml).

Gate: a test fails only on a "serious" or "critical" impact violation (axe's
own severity scale). "moderate"/"minor" findings are still visible in the
assertion message if a serious/critical one trips, for context, but do not
fail the build on their own - matching the instruction to gate on serious
violations, not to chase every subjective/minor axe suggestion.

No axe rules are disabled, globally or otherwise. If a third-party
component (Monaco) ever needs a narrow, documented exception, it belongs
right next to the assertion that needs it - see SERIOUS_IMPACTS below for
the one and only severity filter this file applies.
"""

from __future__ import annotations

import socket
import threading

import pytest

import app as app_module

try:
    from playwright.sync_api import sync_playwright
    from axe_playwright_python.sync_playwright import Axe
except ImportError:  # pragma: no cover - environment without the dev deps installed
    sync_playwright = None
    Axe = None

try:
    from werkzeug.serving import make_server
except ImportError:  # pragma: no cover - werkzeug always ships with Flask
    make_server = None

SERIOUS_IMPACTS = {"serious", "critical"}


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
    _SKIP_REASON = "playwright / axe-playwright-python not installed (see requirements-dev.txt)"
elif not _playwright_chromium_available():
    _SKIP_REASON = "Playwright's Chromium browser is not installed - run `playwright install chromium`"

pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


@pytest.fixture(scope="module")
def live_server():
    """A real HTTP server for the actual Flask app, on an ephemeral port, so
    a real browser can load /ide and /static/* exactly as a user's browser
    would - a Flask test-client response is just an HTML string and cannot
    be rendered, styled, or scripted by a real browser."""
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


def _axe_violations(page, context=None):
    axe = Axe()
    results = axe.run(page, context=context, options={"resultTypes": ["violations"]})
    return results.response["violations"]


def assert_no_serious_violations(page, label, context=None):
    violations = _axe_violations(page, context=context)
    serious = [v for v in violations if v.get("impact") in SERIOUS_IMPACTS]
    if serious:
        details = []
        for v in serious:
            targets = [n["target"] for n in v["nodes"]]
            details.append(f"  [{v['impact']}] {v['id']}: {v['help']} -> {targets}")
        all_ids = [f"{v['id']}({v.get('impact')})" for v in violations]
        raise AssertionError(
            f"{label}: {len(serious)} serious/critical axe violation(s):\n"
            + "\n".join(details)
            + f"\n\nAll violations found: {all_ids}"
        )
    return violations


def _unique(prefix):
    import time

    return f"{prefix}{int(time.time() * 1000) % 1000000}"


def _make_cohort(live_server, page, username):
    """Mirrors tests/test_classroom_accessibility.py::_make_cohort, but via
    real HTTP requests against the live server instead of the Flask test
    client, so the resulting session cookies are the browser's own."""
    api = page.request
    api.post(f"{live_server}/classroom/instructor/register", form={
        "username": username, "password": "correct-horse-1", "display_name": "Teacher",
    })
    resp = api.post(f"{live_server}/classroom/cohorts", form={"name": "Cohort"})
    html = resp.text()
    import re
    join_code = re.search(r'cu-join-code">([A-Z0-9]+)<', html).group(1)
    cohort_id = re.search(r'cohorts/(\d+)"', html).group(1)
    return join_code, cohort_id


# ---- the student IDE ---------------------------------------------------------

def test_ide_default_load(live_server, page):
    page.goto(f"{live_server}/ide")
    page.wait_for_selector("#editor")
    assert_no_serious_violations(page, "/ide default load")


def test_ide_with_settings_disclosure_open(live_server, page):
    page.goto(f"{live_server}/ide")
    page.wait_for_selector("#editor")
    page.click("summary.cu-disclosure-summary")
    assert_no_serious_violations(page, "/ide with accessibility settings open")


def test_ide_with_output_rendered(live_server, page):
    page.goto(f"{live_server}/ide")
    page.wait_for_selector("#editor")
    page.wait_for_function("typeof editor !== 'undefined' && !!editor")
    page.evaluate("setCode('print(1)', {preserveSpeech:false})")
    page.click("#runBtn")
    page.wait_for_function("document.getElementById('output').textContent.trim().length > 0", timeout=10000)
    assert_no_serious_violations(page, "/ide with program output rendered")


def test_ide_with_error_rendered(live_server, page):
    page.goto(f"{live_server}/ide")
    page.wait_for_selector("#editor")
    page.wait_for_function("typeof editor !== 'undefined' && !!editor")
    page.evaluate("setCode('print(1/0)', {preserveSpeech:false})")
    page.click("#runBtn")
    page.wait_for_function(
        "document.getElementById('output').textContent.includes('ERROR')", timeout=10000
    )
    assert_no_serious_violations(page, "/ide with a runtime error rendered")


def test_ide_narrow_viewport(live_server, browser):
    ctx = browser.new_context(viewport={"width": 375, "height": 812})
    page = ctx.new_page()
    try:
        page.goto(f"{live_server}/ide")
        page.wait_for_selector("#editor")
        assert_no_serious_violations(page, "/ide at 375px mobile viewport")
    finally:
        ctx.close()


# ---- classroom ---------------------------------------------------------------

def test_classroom_student_join_page(live_server, page):
    page.goto(f"{live_server}/classroom/join")
    assert_no_serious_violations(page, "/classroom/join (student join)")


def test_classroom_instructor_login_page(live_server, page):
    page.goto(f"{live_server}/classroom/instructor/login")
    assert_no_serious_violations(page, "/classroom/instructor/login")


def test_classroom_instructor_dashboard(live_server, page):
    username = _unique("axe_instr")
    _make_cohort(live_server, page, username)
    page.goto(f"{live_server}/classroom/instructor")
    assert_no_serious_violations(page, "/classroom/instructor (instructor dashboard)")


def test_classroom_cohort_dashboard(live_server, page):
    username = _unique("axe_cohort")
    _, cohort_id = _make_cohort(live_server, page, username)
    page.goto(f"{live_server}/classroom/cohorts/{cohort_id}")
    assert_no_serious_violations(page, "/classroom/cohorts/<id> (cohort dashboard content page)")


def test_classroom_student_dashboard(live_server, page):
    username = _unique("axe_learner")
    join_code, _ = _make_cohort(live_server, page, username)
    resp = page.request.post(
        f"{live_server}/classroom/join", form={"join_code": join_code, "display_name": "Amir"}
    )
    assert resp.ok
    page.goto(f"{live_server}/classroom")
    assert_no_serious_violations(page, "/classroom (student dashboard)")


def test_classroom_assignment_detail_page(live_server, page):
    import re

    username = _unique("axe_assign")
    _, cohort_id = _make_cohort(live_server, page, username)
    resp = page.request.post(
        f"{live_server}/classroom/cohorts/{cohort_id}/assignments",
        form={"title": "A", "instructions": "Do the thing", "starter_code": "", "ai_policy": "OFF"},
    )
    assignment_id = re.search(rb"assignments/(\d+)/publish", resp.body()).group(1).decode()
    page.goto(f"{live_server}/classroom/assignments/{assignment_id}")
    assert_no_serious_violations(page, "/classroom/assignments/<id> (assignment/task page)")


def test_classroom_quiz_page_if_a_builtin_module_has_one(live_server, page):
    """Best-effort: the quiz route redirects to curriculum_home when the
    requested module has no quiz_question configured. Only the first
    built-in module (see codeup/learning/tutorial_engine.MODULE_ORDER) is
    tried; if none of them ship a quiz, this documents that rather than
    forcing content to exist just to exercise the page."""
    username = _unique("axe_quiz")
    join_code, _ = _make_cohort(live_server, page, username)
    join_resp = page.request.post(
        f"{live_server}/classroom/join", form={"join_code": join_code, "display_name": "Amir"}
    )
    assert join_resp.ok
    resp = page.goto(f"{live_server}/classroom/curriculum/print/quiz")
    if "/curriculum" in resp.url and "quiz" not in resp.url:
        pytest.skip("built-in 'print' module has no quiz_question configured - route redirected")
    assert_no_serious_violations(page, "/classroom/curriculum/<module>/quiz")


def test_classroom_narrow_viewport(live_server, browser):
    ctx = browser.new_context(viewport={"width": 375, "height": 812})
    page = ctx.new_page()
    try:
        page.goto(f"{live_server}/classroom/join")
        assert_no_serious_violations(page, "/classroom/join at 375px mobile viewport")
    finally:
        ctx.close()
