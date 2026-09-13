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


def test_classroom_bundle_renders_join_disclosure_without_runtime_errors(browser, live_server):
    context = browser.new_context()
    current = context.new_page()
    errors = []
    current.on("pageerror", lambda error: errors.append(str(error)))
    current.on(
        "console",
        lambda message: errors.append(message.text) if message.type == "error" else None,
    )
    current.goto(f"{live_server}/ide")
    current.wait_for_function("typeof editor !== 'undefined' && !!editor")
    current.wait_for_selector("#classroomDetails", state="attached")
    assert current.locator("#classroomPanelHeading").inner_text() == "Join a class"
    assert current.locator("#classroomDetails").evaluate("element => element.open") is False

    current.evaluate("editor.setValue('print(1)'); editor.focus()")
    current.keyboard.press("Control+Enter")
    current.wait_for_function("document.querySelector('#output').innerText.includes('1')")
    current.wait_for_timeout(2200)
    current.evaluate(
        "() => { if (typeof window._classroomOnHeartbeat === 'function') "
        "window._classroomOnHeartbeat(); }"
    )
    current.wait_for_timeout(200)
    assert errors == []
    context.close()


def test_typed_analyze_deeper_uses_deep_endpoint_and_semantics(page):
    analysis_requests = []
    page.on(
        "request",
        lambda request: analysis_requests.append(request.url)
        if "/analyze" in request.url
        else None,
    )
    page.evaluate(
        "code => { editor.setValue(code); editor.focus(); }",
        "for item in range(2):\n    print(item)\n",
    )
    page.locator("#voiceText").fill("analyze deeper")
    page.locator("#sendCommandBtn").click()
    page.wait_for_function(
        "document.querySelector('#output').innerText.includes('colon says that an indented block follows')"
    )
    output = page.locator("#output").inner_text()
    assert "Line 1:" in output and "Line 2:" in output
    assert "parentheses contain the arguments passed to a function" in output
    assert any(url.endswith("/analyze-deep") for url in analysis_requests)
    assert not any(url.endswith("/analyze") for url in analysis_requests)


def _run_until_prompt(page, code):
    page.evaluate("code => { editor.setValue(code); editor.focus(); }", code)
    page.keyboard.press("Control+Enter")
    page.wait_for_function("!document.querySelector('#programInputValue').disabled")
    assert page.evaluate("document.activeElement.id") == "programInputValue"
    page.wait_for_timeout(120)  # let the input panel's final focus retry settle


def _wait_for_output(page, expected):
    page.wait_for_function("expected => document.querySelector('#output').textContent.includes(expected)", arg=expected)


@pytest.mark.parametrize("answer", ["run", "stop", "yes", "no", "123", "hello world"])
def test_field_enter_accepts_common_values_once(page, answer):
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
        'first = input("First? ")\nsecond = input("Second? ")\nprint("PAIR:" + first + ":" + second)',
    )
    page.keyboard.type("alpha")
    page.keyboard.press("Enter")
    page.wait_for_function(
        "document.querySelector('#programInputStatus').textContent.includes('Second?')"
    )
    assert page.evaluate("document.activeElement.id") == "programInputValue"
    page.keyboard.type("beta")
    page.keyboard.press("Tab")
    page.keyboard.press("Space")
    _wait_for_output(page, "PAIR:alpha:beta")
