"""Instructor UX in a real browser: cohort dashboard state, help queue,
assistance-policy presets, and live-sync focus safety.

Real Flask server + Chromium. Learner state is produced through the real
learner endpoints (join-api, help-requests, submit), not written to the DB.

Regressions locked here (Vision-Aid acceptance continuation):
  * instructor-sync reconcileTable walked tbody.firstChild/nextSibling, which
    in server-rendered markup are whitespace text nodes - so the first
    live-sync reconcile re-inserted every learner row and knocked keyboard
    focus off the row link to <body>;
  * submission times were shown as raw ISO timestamps (read aloud digit by
    digit) instead of the "Just now / N min ago" wording used elsewhere.

Integration module (needs Playwright Chromium); see tests/conftest.py.
"""

from __future__ import annotations

import re
import socket
import threading

import pytest

import app as app_module

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

from werkzeug.serving import make_server


def _chromium_available() -> bool:
    if sync_playwright is None:
        return False
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium not available")


@pytest.fixture
def live_server(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
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


@pytest.fixture
def classroom(live_server):
    base = live_server
    headers = {"Origin": base, "Referer": base + "/ide"}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ictx = browser.new_context()
        api = ictx.request
        api.post(f"{base}/classroom/instructor/register",
                 form={"username": "ux_instr", "password": "ux-test-pass-1", "display_name": "Ms Rao"})
        html = api.post(f"{base}/classroom/cohorts", form={"name": "Vision Aid Batch"}).text()
        join_code = re.search(r'cu-join-code">([A-Z0-9]+)<', html).group(1)
        cohort_id = re.search(r'cohorts/(\d+)"', html).group(1)
        r = api.post(f"{base}/classroom/cohorts/{cohort_id}/assignments",
                     form={"title": "Marks average", "instructions": "Average three marks.",
                           "ai_policy": "GUIDED_PRACTICE"})
        aid = re.search(r"assignments/(\d+)", r.url).group(1)
        api.post(f"{base}/classroom/assignments/{aid}/publish")

        def join(name):
            ctx = browser.new_context()
            ctx.request.post(f"{base}/classroom/join-api", data={"join_code": join_code, "display_name": name},
                             headers=headers)
            ctx.request.get(f"{base}/classroom/assignments/{aid}/open")
            return ctx.request

        asha, ravi = join("Asha"), join("Ravi")
        asha.post(f"{base}/classroom/help-requests",
                  data={"message": "My loop prints the wrong total", "assignment_id": int(aid)}, headers=headers)
        ravi.post(f"{base}/classroom/assignments/{aid}/submit", data={"code": "print((80 + 90) / 2)"},
                  headers=headers)
        yield {"base": base, "page": ictx.new_page(), "cohort_id": cohort_id, "aid": aid, "join": join}
        browser.close()


def _rows(page, table_index):
    return page.evaluate(
        "(i) => [...[...document.querySelectorAll('table')][i].tBodies[0].rows]"
        ".map(r => [...r.cells].map(c => c.innerText.trim()))", table_index)


def test_dashboard_shows_learner_state_help_and_human_readable_policy(classroom):
    page = classroom["page"]
    page.goto(f"{classroom['base']}/classroom/cohorts/{classroom['cohort_id']}")
    help_rows = _rows(page, 0)
    assert help_rows[0][0] == "Asha" and help_rows[0][2] == "Marks average"
    assert "wrong total" in help_rows[0][3] and help_rows[0][4] == "Waiting"
    learners = {row[0]: row for row in _rows(page, 1)}
    assert learners["Asha"][1] == "requested help" and learners["Asha"][2] == "Waiting for help"
    assert learners["Asha"][3] == "Marks average"
    assert learners["Ravi"][1] == "submitted" and learners["Ravi"][6] == "1 / 1"
    assignments = _rows(page, 2)
    assert assignments[0][0] == "Marks average" and assignments[0][3] == "Guided practice"
    body = page.inner_text("body")
    assert not re.search(r"\b(GUIDED_PRACTICE|FULL|ASSESSMENT|HINTS_ONLY)\b", body)
    # Table semantics and labelled regions.
    assert page.evaluate("() => [...document.querySelectorAll('table')].every(t => t.caption"
                         " && [...t.tHead.querySelectorAll('th')].every(th => th.scope === 'col'))")
    assert page.evaluate("() => [...document.querySelectorAll('input:not([type=hidden]),select,textarea')]"
                         ".every(el => (el.labels && el.labels.length) || el.getAttribute('aria-label'))")


def test_live_sync_keeps_keyboard_focus_and_announces_new_help(classroom):
    page = classroom["page"]
    page.goto(f"{classroom['base']}/classroom/cohorts/{classroom['cohort_id']}")
    page.wait_for_load_state("networkidle")
    page.evaluate("() => [...document.querySelectorAll('table')][1].querySelector('a').focus()")
    aaron = classroom["join"]("Aaron")  # sorts above the focused Asha row
    aaron.post(f"{classroom['base']}/classroom/help-requests", data={"message": "stuck"},
               headers={"Origin": classroom["base"], "Referer": classroom["base"] + "/ide"})
    page.evaluate("() => window.dispatchEvent(new Event('focus'))")  # the script's own immediate-sync trigger
    page.wait_for_function("() => [...document.querySelectorAll('table')][1].tBodies[0].rows.length === 3",
                           timeout=15000)
    assert [r[0] for r in _rows(page, 1)] == ["Aaron", "Asha", "Ravi"]
    assert page.evaluate("() => document.activeElement.tagName + ' ' + document.activeElement.textContent") \
        == "A Asha"
    page.wait_for_function("() => /Aaron requested instructor help/.test("
                           "document.getElementById('srAnnouncer').textContent)", timeout=5000)


def test_assignment_detail_presets_individual_controls_and_readable_submission_time(classroom):
    page = classroom["page"]
    page.goto(f"{classroom['base']}/classroom/assignments/{classroom['aid']}")
    labels = page.eval_on_selector_all("select[name=ai_policy] option", "os => os.map(o => o.textContent.trim())")
    assert labels[:3] == ["Learning", "Guided practice", "Assessment"]
    caps = "() => Object.fromEntries([...document.querySelectorAll('input[type=checkbox][name^=cap_]')]" \
           ".map(c => [c.labels[0].textContent.trim(), c.checked]))"
    state = page.evaluate(caps)
    assert state["Code generation"] is False and state["Automatic fixes"] is False and state["Hints"] is True
    page.select_option("select[name=ai_policy]", "ASSESSMENT")
    page.click("text=Apply preset")
    page.wait_for_load_state("networkidle")
    assert not any(page.evaluate(caps).values())
    page.check("input[name=cap_hint]")
    page.click("text=Save permissions")
    page.wait_for_load_state("networkidle")
    state = page.evaluate(caps)
    assert state["Hints"] is True and state["Code generation"] is False
    submissions = {row[0]: row for row in _rows(page, 0)}
    assert submissions["Ravi"][1] == "submitted" and submissions["Ravi"][4] == "Just now"
    assert not re.search(r"\d{4}-\d\d-\d\dT", page.inner_text("main"))


def test_help_queue_mark_helping_and_resolve(classroom):
    page = classroom["page"]
    page.goto(f"{classroom['base']}/classroom/cohorts/{classroom['cohort_id']}/help-requests")
    page.click("button[aria-label='Mark as helping Asha']")
    page.wait_for_load_state("networkidle")
    assert "Currently helping (1)" in page.inner_text("main")
    page.click("button[aria-label=\"Mark Asha's request resolved\"]")
    page.wait_for_load_state("networkidle")
    text = page.inner_text("main")
    assert "Open requests (0)" in text and "Currently helping (0)" in text
