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


class TestSyntaxErrorExplanationIsNotDuplicated:
    """Regression: for IndentationError "expected an indented block", `error` (from
    _syntax_error_message) and `explanation` (from _local_error_explanation) used to
    independently restate the same "must be indented" / "add four spaces" advice.
    The frontend speaks both fields back to back (static/app.js: `speak(...)` for
    data.error, then `if (data.explanation) speak(data.explanation)`), so a screen
    reader/TTS user heard near-identical guidance twice for one error. Live repro:
    running "for i in range(3):\\nprint(i)" produced two speak() calls, 193 and 181
    chars, both containing "the line after the loop ... must be indented" and
    "add four spaces before".
    """

    def test_indentation_expected_block_error_has_no_duplicate_explanation(self, client):
        code = "for i in range(3):\nprint(i)"
        data = client.post("/run", json={"code": code, "language": "en", "inputs": []}).get_json()
        assert data["success"] is False
        assert "IndentationError" in data["error"]
        assert "must be indented" in data["error"]
        assert not data["explanation"], (
            "explanation should be suppressed here since _syntax_error_message() "
            "already gives the full beginner guidance for this exact error - "
            "sending both makes the frontend speak the same advice twice"
        )

    def test_other_syntax_errors_still_get_an_explanation(self, client):
        code = "if True\n    print('missing colon')"
        data = client.post("/run", json={"code": code, "language": "en", "inputs": []}).get_json()
        assert data["success"] is False
        assert data["explanation"], "non-indentation syntax errors should still get an explanation"

    def test_unexpected_indent_still_gets_an_explanation(self, client):
        # Only the "expected an indented block" IndentationError variant is a verbatim
        # duplicate; "unexpected indent" gives different, non-overlapping advice from
        # _syntax_error_message, so its explanation should NOT be suppressed.
        code = "x = 1\n    y = 2\n"
        data = client.post("/run", json={"code": code, "language": "en", "inputs": []}).get_json()
        assert data["success"] is False
        assert "IndentationError" in data["error"]
        assert data["explanation"]


class TestLastRunOutputTruncation:
    """Regression: session_memory.record_run() clipped last_run_output to 800 chars
    with zero indication of truncation (plain text[:800]), while the run response
    itself and spoken narration allow up to 4000/unbounded. Voice queries like
    "what did the program print" or "repeat last output" read mem["last_run_output"]
    directly, so any run producing more than 800 chars of output (common - a 200
    line loop easily exceeds 17,000 chars) was silently cut to a random byte
    boundary with no hint anything was missing.
    """

    def test_last_run_output_keeps_far_more_than_the_old_800_char_cap(self, client):
        code = "for i in range(200):\n    print('line', i, 'padding text to make this long')\n"
        run_data = client.post("/run", json={"code": code, "language": "en", "inputs": []}).get_json()
        assert run_data["success"] is True
        full_len = len(run_data["output"])
        assert full_len > 4000, "test fixture should produce output longer than the new cap to exercise truncation"

        # "repeat output" -> repeat_last_output intent, which speaks mem["last_run_output"]
        # directly (unlike "what did the program print" -> program_output, which always
        # caps to 5 lines regardless of char count - a separate, intentional limit).
        reply = client.post("/voice-command", json={"text": "repeat output"}).get_json()
        assert reply["intent"] == "repeat_last_output"
        spoken = reply.get("speech") or reply.get("message") or ""
        assert len(spoken) > 800, "should carry far more than the old 800-char silent cap"
        assert "truncated after 4000 characters" in spoken

    def test_short_output_is_preserved_exactly_with_no_truncation_notice(self, client):
        code = "print('hello world')"
        client.post("/run", json={"code": code, "language": "en", "inputs": []})
        reply = client.post("/voice-command", json={"text": "repeat output"}).get_json()
        assert reply["intent"] == "repeat_last_output"
        spoken = reply.get("speech") or reply.get("message") or ""
        assert "hello world" in spoken
        assert "truncated" not in spoken.lower()

    def test_session_memory_record_run_appends_a_truncation_notice_when_cut(self):
        from codeup.runtime import session_memory

        mem = session_memory.new_memory()
        long_output = "x" * 6000
        session_memory.record_run(mem, output=long_output, ran_ok=True)
        stored = mem["last_run_output"]
        assert len(stored) > 4000
        assert "truncated after 4000 characters" in stored
        assert "2000 more characters omitted" in stored

    def test_session_memory_record_run_leaves_short_output_untouched(self):
        from codeup.runtime import session_memory

        mem = session_memory.new_memory()
        session_memory.record_run(mem, output="hello world", ran_ok=True)
        assert mem["last_run_output"] == "hello world"
