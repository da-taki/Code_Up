"""End-to-end voice-repair tests: short onboarding, real multi-turn pattern
clarification, intentional broken-code examples, global concept Q&A, and the
guards that keep clean commands fast. AI is disabled, so these assert the
deterministic path that runs whenever Key 2 is missing/busy.
"""
import os

import pytest

import app as app_module


ROOT = os.path.dirname(os.path.dirname(__file__))
LOOP = "for i in range(3):\n    print(i)\n"


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


# 1 + 2 — onboarding vs full list
def test_first_help_returns_short_onboarding(client):
    d = _vc(client, "what can I do here")
    assert d["action"] == "deterministic_message"
    assert d.get("onboarding") is True
    assert "generate code to print the first five even numbers" in d["message"]
    assert "more examples" in d["message"].lower()
    assert len(d["message"]) < 320  # short, not the long command dump


@pytest.mark.parametrize("text", ["what can you do", "how do I use this", "what should I try", "help me start"])
def test_first_help_variants(client, text):
    assert _vc(client, text).get("onboarding") is True


def test_more_examples_returns_full_list(client):
    assert _vc(client, "more examples")["action"] == "more_help"


@pytest.mark.parametrize("text", ["show all commands", "full help", "command list"])
def test_second_level_help_variants(client, text):
    assert _vc(client, text)["action"] == "more_help"


# 3 + 4 — vague pattern asks a useful question, never "type a precise command"
def test_vague_pattern_asks_specific_question(client):
    d = _vc(client, "make a five by five thing with the third line different", source="voice")
    assert d["action"] == "clarify"
    assert "symbol" in d["message"].lower()
    assert "third line" in d["message"]


@pytest.mark.parametrize("text", [
    "make a five by five thing with the third line different",
    "make a 5x5 python cube yadi yadi yada",
])
def test_never_tells_user_to_type_a_precise_command(client, text):
    d = _vc(client, text, source="voice")
    assert "type a precise command" not in (d.get("message", "") + d.get("speech", "")).lower()


# 5 + 6 + 7 — pending pattern clarification understands the answer
def test_pending_pattern_accepts_rather_than_answer(client):
    first = _vc(client, "make a five by five star pattern with the third line different", source="voice")
    assert first["action"] == "clarify"
    second = _vc(client, "It should have 6 rather than 5", source="voice")
    assert second["action"] == "generate_code"
    assert second["source"] == "deterministic_exact"
    assert "row 3 has 6 stars" in second["prompt"]


def test_pending_pattern_accepts_combined_answer(client):
    _vc(client, "make a five by five thing with the third line different", source="voice")
    d = _vc(client, "stars, six on the third line", source="voice")
    assert d["action"] == "generate_code"
    assert d["source"] == "deterministic_exact"
    assert "row 3 has 6 stars" in d["prompt"]


def test_clarification_answer_is_never_unrecognized(client):
    _vc(client, "make a five by five thing with the third line different", source="voice")
    d = _vc(client, "six instead of five", source="voice")
    # Still missing the symbol -> ask one more specific question, never "unknown".
    assert d["action"] in ("generate_code", "clarify")
    assert d["action"] != "unknown"


# 8 + 9 — intentional broken-code examples
def test_insert_without_closing_quote_makes_broken_code(client):
    d = _vc(client, "insert print hello world without closing quote")
    assert d["action"] == "conversational_edit"
    assert d["ai_action"]["action"] == "append_code"
    assert d["ai_action"]["code"] == 'print("hello world)'


def test_intentional_indentation_error(client):
    d = _vc(client, "make an indentation error with if age greater than 10")
    code = d["ai_action"]["code"]
    # The print is NOT indented under the if -> a real IndentationError example.
    assert "if age > 10:" in code
    assert "\nprint(" in code and "\n    print(" not in code


def test_normal_insert_is_not_treated_as_broken(client):
    d = _vc(client, "insert print hello")
    assert d.get("intentional_error") is not True
    assert d["action"] == "append_line"


# 10 + 11 — global concept Q&A outside the tutorial
def test_why_quotes_works_outside_tutorial(client):
    d = _vc(client, "why do we use quotes", code='print("Hello")')
    assert d["action"] == "deterministic_message"
    assert "Hello" in d["message"]
    assert "text" in d["message"].lower()


def test_what_does_range_mean_works_outside_tutorial(client):
    d = _vc(client, "what does range 3 mean", code=LOOP)
    assert d["action"] == "deterministic_message"
    assert "range(3)" in d["message"]
    assert "0, 1, and 2" in d["message"]


# 12 - 15 — clean / existing commands still route, never hijacked
def test_run_code_routes_normally(client):
    assert _vc(client, "run", code="print(1)")["action"] == "run"


def test_clean_exact_symbol_pattern_still_deterministic(client):
    d = _vc(client, "make a 5 by 5 star pattern", source="voice")
    assert d["action"] == "generate_code"
    assert d["source"] == "deterministic_exact"


def test_do_the_same_with_10_still_works(client):
    client.post("/generate-code", json={"prompt": "print the first five even numbers"})
    d = _vc(client, "do the same with 10", code="x = 1")
    assert d["action"] == "generate_code"
    assert "first five even numbers" in d["prompt"] and "10" in d["prompt"]


def test_delete_that_file_asks_clarification_not_invented_file(client):
    d = _vc(client, "delete that file")
    assert d["action"] == "clarify"
    assert "which file" in d["message"].lower()
    assert ".py" not in d["message"]


# 16 — Key 2 unavailable still gives deterministic clarification
def test_key2_unavailable_gives_deterministic_clarification(client, monkeypatch):
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", lambda *a, **k: "")
    d = _vc(client, "make a five by five thing with the third line different", source="voice")
    assert d["action"] == "clarify"
    assert "third line" in d["message"]


# 17 — tutorial navigation cancels current speech before acting
def test_tutorial_navigation_cancels_speech_before_acting():
    with open(os.path.join(ROOT, "static", "tutorial.js"), encoding="utf-8") as fh:
        src = fh.read()
    start = src.index("handleUtterance: function")
    block = src[start:start + 3000]
    cancel = block.index("SpeechManager.cancelAll")
    switch = block.index("switch (kind)")
    # The shared stop-speech path is invoked before the navigation switch runs.
    assert cancel < switch
