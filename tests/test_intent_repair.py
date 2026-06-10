"""Tests for the Key 2 intent-repair layer: messy natural speech maps to existing
actions, spoken inserts become valid beginner Python (text/variable/number from
editor context), unknown Key 2 actions are rejected, and the deterministic path
works when Key 2 is unavailable.
"""
import pytest

import app as app_module
import intent_repair as ir


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
# Natural command intent -> existing actions
# ---------------------------------------------------------------------------
class TestNaturalCommands:
    @pytest.mark.parametrize("text", ["alright stop listening", "can you stop listening please", "okay stop listening"])
    def test_stop_listening(self, client, text):
        assert _vc(client, text)["action"] == "pause_voice"

    def test_please_run_code_now(self, client):
        assert _vc(client, "please run code now", code="print(1)")["action"] == "run"

    def test_explain_this_program(self, client):
        assert _vc(client, "can you explain this program", code="print(1)")["action"] in ("mentor_chat", "walk_through")

    def test_tell_me_what_this_code_does(self, client):
        assert _vc(client, "tell me what this code does", code="print(1)")["action"] in ("mentor_chat", "walk_through")

    @pytest.mark.parametrize("text", [
        "write a program for the first five even numbers",
        "ok generate code for the first five even numbers",
        "make me a python program that prints hello",
    ])
    def test_generation_phrases(self, client, text):
        assert _vc(client, text)["action"] == "generate_code"

    def test_start_tutorial_phrase(self, client):
        assert _vc(client, "hey code up start tutorial")["action"] == "start_tutorial"

    def test_could_you_map_my_code(self, client):
        assert _vc(client, "could you map my code", code="def f():\n    pass\n")["action"] == "code_map"


# ---------------------------------------------------------------------------
# Spoken insert -> valid beginner Python (via the route)
# ---------------------------------------------------------------------------
class TestSpokenInsert:
    def _code(self, client, text, code=""):
        d = _vc(client, text, code=code)
        assert d["action"] == "conversational_edit", d
        return d["ai_action"]["code"]

    def test_insert_print_hello_is_quoted(self, client):
        assert self._code(client, "insert print hello") == 'print("hello")'

    def test_put_print_hello_in_editor(self, client):
        assert self._code(client, "put print hello in the editor") == 'print("hello")'

    def test_add_print_line_that_says(self, client):
        assert self._code(client, "add a print line that says hello world") == 'print("hello world")'

    def test_insert_print_number_is_bare(self, client):
        assert self._code(client, "insert print 5") == "print(5)"

    def test_undefined_word_is_text(self, client):
        assert self._code(client, "insert print name") == 'print("name")'

    def test_defined_variable_is_bare(self, client):
        assert self._code(client, "insert print name", code='name = "Taknoor"\n') == "print(name)"

    def test_explicit_variable_is_bare_not_text(self, client):
        code = self._code(client, "insert print variable name")
        assert code == "print(name)"
        assert code != 'print("variable name")'

    def test_indented_print(self, client):
        assert self._code(client, "insert an indented print hello") == '    print("hello")'

    def test_for_loop_that_prints(self, client):
        code = self._code(client, "insert a for loop that prints hello three times")
        assert code == 'for i in range(3):\n    print("hello")'
        compile(code, "<t>", "exec")  # syntactically valid


# ---------------------------------------------------------------------------
# Broken-code preservation
# ---------------------------------------------------------------------------
def test_broken_quote_stays_broken(client):
    d = _vc(client, "insert print hello world without closing quote")
    assert d["action"] == "conversational_edit"
    assert d["ai_action"]["code"] == 'print("hello world)'


# ---------------------------------------------------------------------------
# Repair + validation
# ---------------------------------------------------------------------------
class TestRepairValidation:
    def test_unknown_key2_action_is_rejected(self):
        assert ir.validate_decision({"action": "delete_everything", "confidence": 0.9}) is False
        # An AI reply with an unknown action is dropped -> deterministic no_op.
        decision = ir.repair("frobnicate the doohickey", ai_fn=lambda s, u: '{"action":"delete_everything","confidence":0.95}')
        assert decision["action"] == "no_op"

    def test_low_confidence_asks_one_clarification(self):
        decision = ir.repair(
            "uh do the thing",
            ai_fn=lambda s, u: '{"handled":false,"intent":"clarify","action":"clarify","confidence":0.4,"clarification":"Do you want to generate code, edit, or explain?"}',
        )
        assert decision["action"] == "clarify"
        assert decision["clarification"]

    def test_key2_unavailable_still_handles_deterministic(self):
        assert ir.repair("alright stop listening", ai_fn=None)["action"] == "stop_listening"
        assert ir.repair("could you map my code", ai_fn=None)["action"] == "code_map"

    def test_generate_decision_requires_a_prompt(self):
        assert ir.validate_decision({"action": "generate_code", "confidence": 0.9, "slots": {}}) is False
        assert ir.validate_decision({"action": "generate_code", "confidence": 0.9, "slots": {"prompt": "print hi"}}) is True

    def test_unrelated_chat_is_no_op(self):
        assert ir.repair("hey there how are you", ai_fn=None)["action"] == "no_op"


# ---------------------------------------------------------------------------
# Context detection
# ---------------------------------------------------------------------------
class TestDefinedNames:
    def test_detects_assignment_and_loop_and_def(self):
        names = ir.defined_names("name = 'x'\nfor item in items:\n    pass\ndef greet(who):\n    pass\n")
        assert {"name", "item", "greet", "who"}.issubset(names)

    def test_detects_loop_var_in_incomplete_code(self):
        # The tutorial builds line by line; a bare for-header has no body yet.
        assert "i" in ir.defined_names("for i in range(3):")
