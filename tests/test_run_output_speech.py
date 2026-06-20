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



class TestReadOutputRouting:

    @pytest.mark.parametrize("text", [
        "read output", "speak output", "say the output",
        "read full output", "read the full output", "read last output",
        "read all output", "read the whole output", "read output again",
    ])
    def test_read_output_variants_route(self, client, text):
        expected = "deterministic_message" if text == "read last output" else "read_output"
        assert _vc(client, text)["action"] == expected

    def test_read_the_code_is_not_read_output(self, client):
        assert _vc(client, "read the code")["action"] != "read_output"



class TestRunOutputSpeechWiring:

    def test_formatter_helper_exists(self, app_js):
        assert "function formatRunOutputSpeech(" in app_js
        assert "function formatFullOutputSpeech(" in app_js

    def test_run_handler_speaks_output_through_the_formatter(self, app_js):
        assert "speak(formatRunOutputSpeech(data.output))" in app_js
        assert "speak('Program output:');" not in app_js

    def test_run_handler_still_stores_full_output(self, app_js):
        assert "window.lastRunOutput = data.output" in app_js

    def test_read_output_reads_the_full_stored_output(self, app_js):
        start = app_js.index("function speakOutput(")
        block = app_js[start:start + 500]
        assert "formatFullOutputSpeech" in block
        assert "window.lastRunOutput" in block

    def test_error_run_is_spoken(self, app_js):
        assert "speak(`Error${lineHint}: ${lastLine}`)" in app_js

    def test_dispatch_routes_read_output_to_speak_output(self, app_js):
        assert "action === 'read_output') speakOutput();" in app_js

    def test_stop_everything_preserves_the_output_box(self, app_js):
        start = app_js.index("action === 'stop_everything'")
        block = app_js[start:start + 600]
        assert "Preserve the last program output" in block
        assert "out('')" not in block
        assert "clearOutput" not in block

    def test_stop_speaking_does_not_clear_output(self, app_js):
        assert "function cancelAll()" in app_js
