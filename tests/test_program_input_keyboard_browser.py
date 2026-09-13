"""Trusted-browser keyboard coverage for the contextual program-input flow.

Synthetic KeyboardEvents do not exercise native focus and button activation.
These tests use Playwright's real keyboard path against the running Flask app.
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


def _browser_available():
    if sync_playwright is None:
        return False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _browser_available(), reason="Playwright Chromium is unavailable")


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


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser, live_server):
    context = browser.new_context()
    current = context.new_page()
    current.goto(f"{live_server}/ide")
    current.wait_for_function("typeof editor !== 'undefined' && !!editor")
    yield current
    context.close()


def _run_until_prompt(page, code):
    page.evaluate("code => { editor.setValue(code); editor.focus(); }", code)
    page.keyboard.press("Control+Enter")
    page.wait_for_function("!document.querySelector('#programInputValue').disabled")
    assert page.evaluate("document.activeElement.id") == "programInputValue"
    page.wait_for_timeout(120)


def _wait_for_output(page, expected):
    page.wait_for_function(
        "expected => document.querySelector('#output').textContent.includes(expected)",
        arg=expected,
    )


@pytest.mark.parametrize("answer", ["run", "stop", "yes", "no", "hello", "16", "Alex", "one"])
def test_field_enter_accepts_program_values_once(page, answer):
    _run_until_prompt(page, 'answer = input("Answer? ")\nprint("GOT:" + answer)')
    page.keyboard.type(answer)
    page.keyboard.press("Enter")
    _wait_for_output(page, "GOT:" + answer)
    assert page.locator("#output").inner_text().count("GOT:" + answer) == 1


@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_focused_submit_button_activates_with_keyboard(page, key):
    _run_until_prompt(page, 'answer = input("Answer? ")\nprint("SUBMIT:" + answer)')
    page.keyboard.type("button value")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "programInputSubmitBtn"
    page.keyboard.press(key)
    _wait_for_output(page, "SUBMIT:button value")


@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_focused_cancel_button_activates_with_keyboard(page, key):
    _run_until_prompt(page, 'answer = input("Answer? ")\nprint(answer)')
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "programInputCancelBtn"
    page.keyboard.press(key)
    page.wait_for_function("document.querySelector('#programInputValue').disabled")
    assert "cu-program-input--active" not in page.locator("#programInputSection").get_attribute("class")


def test_two_sequential_inputs_complete_entirely_by_keyboard(page):
    _run_until_prompt(
        page,
        'name = input("Name: ")\nage = input("Age: ")\nprint(name, age)',
    )
    page.keyboard.type("Alex")
    page.keyboard.press("Enter")
    page.wait_for_function(
        "document.querySelector('#programInputStatus').textContent.includes('Age')"
    )
    assert page.evaluate("document.activeElement.id") == "programInputValue"
    page.keyboard.type("16")
    page.keyboard.press("Tab")
    page.keyboard.press("Space")
    _wait_for_output(page, "Alex 16")


def test_rapid_keyboard_activation_submits_only_once(page):
    run_requests = []
    page.on(
        "request",
        lambda request: run_requests.append(request.url)
        if request.url.endswith("/run")
        else None,
    )
    _run_until_prompt(page, 'answer = input("Answer? ")\nprint("ONCE:" + answer)')
    page.keyboard.type("value")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    _wait_for_output(page, "ONCE:value")
    assert len(run_requests) == 2  # initial prompt request plus one submission
