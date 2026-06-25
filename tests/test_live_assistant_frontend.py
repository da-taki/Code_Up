"""Live Assistant Mode: a controlled, interruptible non-visual assistant layer.

The state machine lives in static/live-assistant.js and is unit-tested in Node
(mocked speech/recognition, no DOM, no real microphone). These tests run that JS
suite and assert the frontend wiring + accessible panel are present.
"""

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_live_assistant_state_machine_in_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available in this environment")
    script = os.path.join(ROOT, "tests", "live_assistant.test.js")
    result = subprocess.run([node, script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (
        "node live assistant test failed:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "live assistant tests passed" in result.stdout


def test_live_assistant_module_is_self_contained():
    src = _read("static/live-assistant.js")
    assert "function createLiveAssistant(deps)" in src
    # Exported for Node tests and exposed as a browser global, but no DOM access.
    assert "module.exports" in src
    assert "createLiveAssistant" in src
    assert "document.getElementById" not in src  # the factory must stay DOM-free


def test_index_html_has_accessible_live_assistant_panel():
    html = _read("templates/index.html")
    assert 'id="liveAssistantPanel"' in html
    assert 'aria-label="Live Assistant"' in html
    # Status uses a polite live region (not assertive).
    assert 'id="liveAssistantStatus"' in html and 'aria-live="polite"' in html
    for token in (
        'id="liveAssistantStartBtn"',
        'id="liveAssistantPauseBtn"',
        'id="liveAssistantStopSpeakBtn"',
        'id="liveAssistantRepeatBtn"',
    ):
        assert token in html, token
    for label in (
        'aria-label="Start live assistant"',
        'aria-label="Pause or resume listening"',
        'aria-label="Stop speaking"',
        'aria-label="Repeat last response"',
    ):
        assert label in html, label
    # Loaded before app.js so the factory exists when app.js instantiates it.
    assert html.index("/static/live-assistant.js") < html.index("/static/app.js")


def test_app_js_wires_live_assistant_through_existing_command_path():
    src = _read("static/app.js")
    assert "window.LiveAssistant = (typeof createLiveAssistant === 'function')" in src
    # Meta commands are intercepted before the /voice-command fetch...
    assert "window.LiveAssistant.handleMetaCommand(txt)" in src
    # ...and ordinary commands still record a turn after the backend responds.
    assert "window.LiveAssistant.recordTurn(txt" in src
    assert "_wireLiveAssistantButtons" in src


def test_live_assistant_does_not_auto_start_microphone():
    # The factory must not start listening on construction; only start() does.
    src = _read("static/live-assistant.js")
    construct = src[src.index("function createLiveAssistant"):src.index("function start()")]
    assert "startListening()" not in construct
