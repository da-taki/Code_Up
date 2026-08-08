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
        assert "speak(formatRunOutputSpeech(data.output), { forceFull: true, speechKind: 'program-output' })" in app_js
        assert "speak('Program output:');" not in app_js

    def test_run_handler_still_stores_full_output(self, app_js):
        assert "window.lastRunOutput = data.output" in app_js

    def test_read_output_reads_the_full_stored_output(self, app_js):
        start = app_js.index("function speakOutput(")
        block = app_js[start:start + 500]
        assert "formatFullOutputSpeech" in block
        assert "window.lastRunOutput" in block
        assert "speechKind: 'program-output-replay'" in block

    def test_error_run_is_spoken(self, app_js):
        assert "speak(`Error${lineHint}: ${lastLine}`, { sr: false, priority: 'assertive' })" in app_js

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


class TestRunOutputExecutionCompleteness:

    def test_prime_numbers_through_50_are_preserved_in_run_output(self, client):
        code = """for n in range(2, 51):
    prime = True
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            prime = False
            break
    if prime:
        print(n)
"""
        data = client.post("/run", json={"code": code, "language": "en", "inputs": []}).get_json()
        assert data["success"] is True
        assert data["output"].strip().splitlines() == [
            "2", "3", "5", "7", "11", "13", "17", "19", "23", "29", "31", "37", "41", "43", "47"
        ]

    def test_long_multiline_output_is_not_truncated_in_run_output(self, client):
        code = "for i in range(120):\n    print(f'line {i}')\n"
        data = client.post("/run", json={"code": code, "language": "en", "inputs": []}).get_json()
        assert data["success"] is True
        lines = data["output"].strip().splitlines()
        assert lines[0] == "line 0"
        assert lines[-1] == "line 119"
        assert len(lines) == 120
