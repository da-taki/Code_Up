"""Tests for confidence-aware clarification of risky/ambiguous voice commands.

Unit tests exercise command_clarifier.assess directly; route tests drive
/voice-command. AI is disabled, so these assert the deterministic clarification
path that runs whenever Key 2 is missing/busy.
"""
import pytest

import app as app_module
import command_clarifier as cc
import session_memory as sm
from symbolic_specs import build_exact_symbol_generation


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as flask_client:
        yield flask_client


NAME_AGE = 'name = input("Enter name: ")\nage = int(input("Enter age: "))\nprint(name, age)\n'
VAGUE_PATTERN = "make a five by five thing with the third line different"


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


# ---------------------------------------------------------------------------
# Unit: assess()
# ---------------------------------------------------------------------------
class TestAssess:
    def test_clean_exact_pattern_is_not_flagged(self):
        ex = build_exact_symbol_generation("make a 5 by 5 star pattern", source="voice")
        assert cc.assess("make a 5 by 5 star pattern", exact_result=ex) is None

    def test_vague_pattern_is_flagged_with_specific_question(self):
        d = cc.assess(VAGUE_PATTERN, exact_result=None)
        assert d["needs_clarification"] is True
        assert "5 by 5" in d["message"]
        assert "third line" in d["message"]
        assert d["reason"] == "ambiguous_pattern"

    def test_sized_but_non_pattern_generation_is_not_flagged(self):
        # A concrete non-pattern request with a size must still generate normally.
        assert cc.assess("make a 3 by 3 grid of random numbers", exact_result=None) is None

    def test_delete_vague_without_memory_asks_which_file(self):
        d = cc.assess("delete that file", mem=sm.new_memory())
        assert d["needs_clarification"] is True
        assert "which file" in d["message"].lower()
        assert d["reason"] == "missing_file_reference"

    def test_delete_vague_with_memory_confirms_the_file(self):
        mem = sm.new_memory()
        sm.record_file_open(mem, "main.py")
        d = cc.assess("delete that file", mem=mem)
        assert d["needs_clarification"] is True
        assert "main.py" in d["message"]
        assert d["reason"] == "confirm_destructive"

    def test_rename_vague_asks_which_file(self):
        d = cc.assess("rename that file", mem=sm.new_memory())
        assert d["needs_clarification"] is True
        assert "rename" in d["message"].lower()

    def test_concrete_delete_is_not_flagged(self):
        assert cc.assess("delete main.py", mem=sm.new_memory()) is None

    @pytest.mark.parametrize("text", [
        "run code", "run", "read my code", "help", "what can i do here",
        "start tutorial", "hint", "insert print hello world",
        "run with name Taknoor and age 16", "make a 5 by 5 star pattern",
    ])
    def test_safe_commands_are_not_flagged(self, text):
        ex = build_exact_symbol_generation(text, source="voice")
        assert cc.assess(text, mem=sm.new_memory(), exact_result=ex) is None

    def test_key2_unavailable_uses_deterministic_message(self):
        d = cc.assess(VAGUE_PATTERN, exact_result=None, ai_fn=None)
        assert d["message"].startswith("Do you want a 5 by 5 pattern")

    def test_key2_failure_falls_back_to_deterministic(self):
        def boom(system, user):
            raise RuntimeError("service busy")

        d = cc.assess(VAGUE_PATTERN, exact_result=None, ai_fn=boom)
        assert d["message"].startswith("Do you want a 5 by 5 pattern")

    def test_decision_holds_no_secrets(self, monkeypatch):
        # A clarification decision must never embed keys/secrets.
        monkeypatch.setenv("GROQ_API_KEY_2", "super-secret-key-value")
        d = cc.assess("delete that file", mem=sm.new_memory())
        assert "super-secret-key-value" not in repr(d)


# ---------------------------------------------------------------------------
# Route integration — the required behaviors
# ---------------------------------------------------------------------------
class TestRoute:
    def test_clear_exact_symbol_not_blocked(self, client):
        d = _vc(client, "make a 5 by 5 star pattern", source="voice")
        assert d["action"] == "generate_code"
        assert d["source"] == "deterministic_exact"

    def test_clear_run_code_not_blocked(self, client):
        assert _vc(client, "run", code="print(1)")["action"] == "run"

    def test_clear_tutorial_command_not_blocked(self, client):
        assert _vc(client, "start tutorial")["action"] == "start_tutorial"

    def test_clear_input_concierge_not_blocked(self, client):
        assert _vc(client, "run with name Taknoor and age 16", code=NAME_AGE)["action"] == "action_sequence"

    def test_delete_that_file_with_no_memory_asks_which_file(self, client):
        d = _vc(client, "delete that file")
        assert d["action"] == "clarify"
        assert "which file" in d["message"].lower()
        assert d.get("needs_clarification") is True

    def test_open_that_file_again_with_no_memory_asks(self, client):
        d = _vc(client, "open that file again")
        assert d.get("needs_clarification") is True
        assert "file" in d["message"].lower()

    def test_do_the_same_with_no_generation_memory_asks(self, client):
        d = _vc(client, "do the same with 10")
        assert d.get("needs_clarification") is True

    def test_do_the_same_with_generation_memory_proceeds(self, client):
        client.post("/generate-code", json={"prompt": "print the first five even numbers"})
        d = _vc(client, "do the same with 10", code="for i in range(5):\n    print(i * 2)")
        assert d["action"] == "generate_code"
        assert "first five even numbers" in d["prompt"]

    def test_vague_pattern_asks_for_clarification(self, client):
        d = _vc(client, VAGUE_PATTERN, source="voice")
        assert d["action"] == "clarify"
        assert d["reason"] == "ambiguous_pattern"
        assert "5 by 5" in d["message"]

    def test_key2_unavailable_still_returns_deterministic_clarification(self, client):
        d = _vc(client, VAGUE_PATTERN, source="voice")
        assert d["action"] == "clarify"
        assert d["message"].startswith("Do you want a 5 by 5 pattern")

    def test_clarification_response_carries_no_secret(self, client, monkeypatch, capsys):
        monkeypatch.setenv("GROQ_API_KEY_2", "secret-second-key-value")
        d = _vc(client, "delete that file")
        assert "secret-second-key-value" not in repr(d)
        captured = capsys.readouterr()
        assert "secret-second-key-value" not in captured.out
        assert "secret-second-key-value" not in captured.err
