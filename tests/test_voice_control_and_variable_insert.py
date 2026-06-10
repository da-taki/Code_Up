"""Speech-control semantics (speech-cancel vs mic-pause vs stop-everything are
distinct) and natural variable-assignment inserts with pending clarification.
AI disabled, so these assert the deterministic path.
"""
import pytest

import app as app_module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as flask_client:
        yield flask_client


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


# ---------------------------------------------------------------------------
# Speech-control semantics
# ---------------------------------------------------------------------------
class TestSpeechControl:
    @pytest.mark.parametrize("text", ["stop speaking", "stop talking", "be quiet",
                                      "pause speech", "stop narration", "alright stop speaking"])
    def test_speech_cancel_only(self, client, text):
        # Cancels narration, never the microphone or the app.
        assert _vc(client, text)["action"] == "stop_speaking"

    @pytest.mark.parametrize("text", ["stop listening", "turn off microphone",
                                      "pause voice input", "disable voice input", "alright stop listening"])
    def test_microphone_pause(self, client, text):
        assert _vc(client, text)["action"] == "pause_voice"

    @pytest.mark.parametrize("text", ["stop everything", "cancel everything", "stop all"])
    def test_stop_everything_is_distinct(self, client, text):
        d = _vc(client, text)
        assert d["action"] == "stop_everything"

    def test_speech_cancel_is_not_microphone_pause(self, client):
        assert _vc(client, "stop speaking")["action"] != "pause_voice"
        assert _vc(client, "stop speaking")["action"] != "stop_everything"

    def test_stop_everything_is_not_microphone_pause(self, client):
        assert _vc(client, "stop everything")["action"] != "pause_voice"


# ---------------------------------------------------------------------------
# Natural variable assignments
# ---------------------------------------------------------------------------
class TestVariableInsert:
    def _code(self, client, text, code=""):
        d = _vc(client, text, code=code)
        assert d["action"] == "conversational_edit", d
        return d["ai_action"]["code"]

    @pytest.mark.parametrize("text,expected", [
        ("insert a variable named name with the value Taki", 'name = "Taki"'),
        ("insert a variable called student with value Aman", 'student = "Aman"'),
        ("make a variable score equal to 7", "score = 7"),
        ("set age to 16", "age = 16"),
        ("create variable city equals Patiala", 'city = "Patiala"'),
    ])
    def test_complete_variable_builds_python(self, client, text, expected):
        assert self._code(client, text) == expected

    def test_number_word_value_becomes_number(self, client):
        assert self._code(client, "insert a variable named age with value sixteen") == "age = 16"


# ---------------------------------------------------------------------------
# Pending clarification for variable assignment
# ---------------------------------------------------------------------------
class TestVariablePending:
    def test_missing_name_asks_then_completes(self, client):
        first = _vc(client, "insert a variable with the value Taki")
        assert first["action"] == "clarify"
        assert "name the variable" in first["message"].lower()
        second = _vc(client, "name")
        assert second["action"] == "conversational_edit"
        assert second["ai_action"]["code"] == 'name = "Taki"'

    def test_missing_name_accepts_call_it_phrasing(self, client):
        _vc(client, "insert a variable with the value Taki")
        d = _vc(client, "call it name")
        assert d["ai_action"]["code"] == 'name = "Taki"'

    def test_missing_value_asks_then_completes(self, client):
        first = _vc(client, "insert a variable named score")
        assert first["action"] == "clarify"
        assert "value" in first["message"].lower() and "score" in first["message"].lower()
        second = _vc(client, "7")
        assert second["action"] == "conversational_edit"
        assert second["ai_action"]["code"] == "score = 7"

    def test_unusable_answer_reasks_not_unknown(self, client):
        _vc(client, "insert a variable with the value Taki")
        d = _vc(client, "123")  # not a valid identifier
        assert d["action"] != "unknown"
        assert d.get("needs_clarification") is True

    def test_keyword_name_is_clarified_not_inserted(self, client):
        # "class" is a Python keyword -> ask for a real name instead of breaking.
        d = _vc(client, "insert a variable named class with value 5")
        assert d["action"] == "clarify"
        assert "name the variable" in d["message"].lower()


# ---------------------------------------------------------------------------
# Previous hotfix behavior preserved
# ---------------------------------------------------------------------------
class TestRegressions:
    def test_print_insert_still_quotes(self, client):
        d = _vc(client, "insert print hello")
        assert d["ai_action"]["code"] == 'print("hello")'

    def test_intentional_broken_code_preserved(self, client):
        d = _vc(client, "insert print hello world without closing quote")
        assert d["ai_action"]["code"] == 'print("hello world)'

    def test_set_inputs_not_hijacked_as_variable(self, client):
        assert _vc(client, "set inputs to Alice and 17")["action"] == "set_inputs"

    def test_run_still_routes(self, client):
        assert _vc(client, "run", code="print(1)")["action"] == "run"
