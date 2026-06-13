"""
Run-output speech (Problem 3) — a critical accessibility contract.

A blind learner cannot read the visible output box, so every run result MUST be
spoken. This module covers:
  * backend routing of the "read output" family ("read full output", etc.),
  * frontend wiring (structural assertions over the shipped static/app.js, the
    way this repo tests frontend behaviour): the run handler speaks the output
    through a single well-formed utterance, the explicit read-back reads the full
    stored output, the error path still speaks, and stop never clears the box.

The pure formatting logic (multi-line -> "0, 1, 2.", long -> summary + offer) is
exercised directly in tests/spoken_code.test.js (bridged via the node tests).
"""
import os

import pytest

import app as app_module

_STATIC = os.path.join(os.path.dirname(__file__), "..", "static")


@pytest.fixture(scope="module")
def app_js():
    with open(os.path.join(_STATIC, "app.js"), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


# =====================================================================
# Backend: the "read output" family routes to read_output
# =====================================================================

class TestReadOutputRouting:

    @pytest.mark.parametrize("text", [
        "read output", "speak output", "say the output",
        "read full output", "read the full output", "read last output",
        "read all output", "read the whole output", "read output again",
    ])
    def test_read_output_variants_route(self, client, text):
        assert _vc(client, text)["action"] == "read_output"

    def test_read_the_code_is_not_read_output(self, client):
        # Guard: reading the code is narration, not the output read-back.
        assert _vc(client, "read the code")["action"] != "read_output"


# =====================================================================
# Frontend wiring (structural over static/app.js)
# =====================================================================

class TestRunOutputSpeechWiring:

    def test_formatter_helper_exists(self, app_js):
        assert "function formatRunOutputSpeech(" in app_js
        assert "function formatFullOutputSpeech(" in app_js

    def test_run_handler_speaks_output_through_the_formatter(self, app_js):
        assert "speak(formatRunOutputSpeech(data.output))" in app_js
        # The fragile two-call pattern must be gone (it could collide / be cut off).
        assert "speak('Program output:');" not in app_js

    def test_run_handler_still_stores_full_output(self, app_js):
        assert "window.lastRunOutput = data.output" in app_js

    def test_read_output_reads_the_full_stored_output(self, app_js):
        start = app_js.index("function speakOutput(")
        block = app_js[start:start + 500]
        assert "formatFullOutputSpeech" in block
        assert "window.lastRunOutput" in block

    def test_error_run_is_spoken(self, app_js):
        # The failure branch must speak the error (line + last line of traceback).
        assert "speak(`Error${lineHint}: ${lastLine}`)" in app_js

    def test_dispatch_routes_read_output_to_speak_output(self, app_js):
        assert "action === 'read_output') speakOutput();" in app_js

    def test_stop_everything_preserves_the_output_box(self, app_js):
        # stop_everything cancels speech but must NOT clear the visible output.
        start = app_js.index("action === 'stop_everything'")
        block = app_js[start:start + 600]
        assert "Preserve the last program output" in block
        assert "out('')" not in block
        assert "clearOutput" not in block

    def test_stop_speaking_does_not_clear_output(self, app_js):
        # The dedicated stop-speaking path only cancels speech.
        assert "function cancelAll()" in app_js
