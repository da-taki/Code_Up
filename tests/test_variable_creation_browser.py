"""Variable creation end to end in a real browser.

Real /ide page and app.js dispatch: handleCommandText -> POST /voice-command
-> variable_creation -> the editor. Covers the one-shot form, the two-turn
clarification (the reply completes the pending request) and a dictionary.
The model is scripted and never needed.

Integration module (needs Playwright Chromium); see tests/conftest.py.
"""

from __future__ import annotations

import socket
import threading

import pytest

import app as app_module
from test_semantic_intent_and_clarification import FakeModel

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
def page(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fake)
    monkeypatch.setattr(app_module, "_structured_ai_available", lambda: True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    app_module.app.config.update(TESTING=False)
    server = make_server("127.0.0.1", port, app_module.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.goto(f"http://127.0.0.1:{port}/ide")
        pg.wait_for_function("typeof editor !== 'undefined' && !!editor && typeof handleCommandText === 'function'")
        yield pg
        browser.close()
    server.shutdown()
    thread.join(timeout=5)


def _say(page, text):
    page.evaluate("(t) => handleCommandText(t)", text)


def test_variable_is_inserted_and_existing_code_kept(page):
    page.evaluate("() => setCode('print(\"Hello\")', {preserveSpeech: true})")
    _say(page, "Let's make a variable name with value taki")
    page.wait_for_function("() => getCode().includes('name = \"taki\"')", timeout=15000)
    code = page.evaluate("() => getCode()")
    assert 'print("Hello")' in code
    assert code.index('name = "taki"') < code.index('print("Hello")')


def test_missing_name_reply_completes_the_variable(page):
    page.evaluate("() => setCode('', {preserveSpeech: true})")
    _say(page, "Variable insert karo with value taki")
    page.wait_for_timeout(1500)
    assert "taki" not in page.evaluate("() => getCode()")
    _say(page, "username")
    page.wait_for_function("() => getCode().includes('username = \"taki\"')", timeout=15000)
    assert "Variable insert" not in page.evaluate("() => getCode()")


def test_dictionary_from_key_value_pairs(page):
    page.evaluate("() => setCode('', {preserveSpeech: true})")
    _say(page, "make a library with key value pairs, Paris expensive, Amsterdam cheap")
    page.wait_for_function("() => getCode().includes('\"Amsterdam\": \"cheap\"')", timeout=15000)
    assert page.evaluate("() => getCode()").startswith("library = {")
