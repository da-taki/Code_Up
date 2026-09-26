"""Expanded curriculum reachability in a real browser.

Real Flask server, real /ide page, real tutorial.js controller, real /run and
/tutorial/validate. The tutorial is opened the way a learner opens it - a
typed/voice command through handleCommandText -> POST /voice-command ->
start_tutorial - and never by un-hiding production UI.

Regression for the Vision-Aid acceptance audit: expanded topics (Dictionaries,
Lists, ...) rendered in the topic list but practice() only accepted the legacy
five-module order, so they could never become the active lesson. Also locks
that finishing a standalone practice topic does not announce "final topic /
all topics complete".

Integration module (needs Playwright Chromium); see tests/conftest.py.
"""

from __future__ import annotations

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


@pytest.fixture(scope="module")
def live_server():
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
def page(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        pg = ctx.new_page()
        pg.goto(f"{live_server}/ide")
        pg.wait_for_function("typeof editor !== 'undefined' && !!editor && !!window.TutorialController")
        pg.evaluate("""() => {
          window.__spoken = [];
          const orig = window.speak;
          window.speak = function (t) { window.__spoken.push(String(t)); return orig.apply(this, arguments); };
        }""")
        yield pg
        browser.close()


def _state(page):
    return page.evaluate("""() => ({
      overlayHidden: document.getElementById('tutorialOverlay').hidden,
      module: TutorialController.model.moduleId,
      stage: TutorialController.model.stage,
      completed: TutorialController.model.completed.slice(),
      topic: document.getElementById('tutorialTopic').textContent,
      progress: document.getElementById('tutorialProgress').textContent,
      status: document.getElementById('tutorialStatus').textContent,
      saved: localStorage.getItem('codeup_tutorial_progress'),
    })""")


def _run(page, code):
    page.evaluate("(c) => setCode(c, {preserveSpeech: true})", code)
    page.evaluate("() => runCode()")


def test_expanded_topic_is_selectable_validates_persists_and_navigates(page):
    page.evaluate("() => handleCommandText('start tutorial')")
    page.wait_for_function("!document.getElementById('tutorialOverlay').hidden", timeout=10000)

    # Expanded topics render inside the tutorial panel.
    page.click("#tutorialTopicsDetails > summary")
    page.wait_for_function("document.getElementById('tutorialTopicsList').childElementCount > 5", timeout=10000)
    labels = page.eval_on_selector_all("#tutorialTopicsList button", "els => els.map(e => e.textContent)")
    assert labels[:5] == ["Print statements", "Variables", "If statements", "For loops", "While loops"]
    assert {"Dictionaries", "Lists", "Functions", "Debugging strategies"} <= set(labels)

    # A topic outside the legacy five becomes the active lesson.
    page.click("#tutorialTopicsList button[aria-label='Practise Dictionaries']")
    page.wait_for_function("TutorialController.model.moduleId === 'dictionaries' "
                           "&& TutorialController.model.stage === 'activity'", timeout=10000)
    s = _state(page)
    assert s["topic"] == "Dictionaries" and s["progress"] == "Practice topic"

    # Wrong attempt: real /run + /tutorial/validate keep the lesson open.
    _run(page, "x = 5\nprint(x)")
    page.wait_for_function("document.getElementById('tutorialStatus').textContent.indexOf('Not yet') === 0",
                           timeout=15000)
    assert _state(page)["completed"] == []

    # Correct attempt passes and is saved.
    _run(page, 'student = {"name": "Asha", "marks": 90}\nprint(student["name"])')
    page.wait_for_function("TutorialController.model.stage === 'decision'", timeout=15000)
    s = _state(page)
    assert s["completed"] == ["dictionaries"] and s["saved"] == '["dictionaries"]'
    assert s["status"].startswith("Practice topic complete")
    spoken = " ".join(page.evaluate("() => window.__spoken"))
    assert "That was the final topic" not in spoken
    assert "Practice topic complete" in spoken

    # Progress survives a reload.
    page.reload()
    page.wait_for_function("!!window.TutorialController && typeof editor !== 'undefined' && !!editor")
    page.evaluate("() => TutorialController._loadProgress()")
    assert _state(page)["completed"] == ["dictionaries"]

    # Another expanded topic by typed/voice command.
    page.evaluate("() => handleCommandText('practise lists')")
    page.wait_for_function("TutorialController.model.moduleId === 'lists'", timeout=10000)
    _run(page, "marks = [80, 90, 75]\nprint(marks[0])")
    page.wait_for_function("TutorialController.model.stage === 'decision'", timeout=15000)
    s = _state(page)
    assert s["completed"] == ["dictionaries", "lists"]
    assert s["saved"] == '["dictionaries","lists"]'


def test_core_sequence_still_announces_topic_numbers(page):
    page.evaluate("() => handleCommandText('start tutorial')")
    page.wait_for_function("TutorialController.model.moduleId === 'print'", timeout=10000)
    assert _state(page)["progress"] == "Topic 1 of 5"
