"""
Confidence-based staged hints (Sprint 2, Feature 4).

Deterministic hints at small / bigger / answer levels (hint_engine.py), with the
session escalating the level for "another hint" and "show me the answer".
"""
import pytest

import app as app_module
import hint_engine

IND_ERR = "IndentationError: expected an indented block"
BROKEN = "for i in range(3):\nprint(i)"

_MUTATING = {"conversational_edit", "generate_code", "fix", "insert_line", "append_line"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestHintBuilder:

    def test_empty_asks_for_task(self):
        h = hint_engine.build_hint({"code": "", "error": ""}, "small")
        assert h["has_problem"] is False
        assert "trying to solve" in h["hint"] or "run your code" in h["hint"]

    def test_indentation_small_is_gentle(self):
        h = hint_engine.build_hint({"code": BROKEN, "error": IND_ERR}, "small")
        assert h["problem_type"] == "indentation"
        assert "check" in h["hint"].lower()

    def test_indentation_bigger_more_specific(self):
        small = hint_engine.build_hint({"error": IND_ERR}, "small")["hint"]
        bigger = hint_engine.build_hint({"error": IND_ERR}, "bigger")["hint"]
        assert small != bigger
        assert "indent" in bigger.lower()

    def test_answer_is_direct(self):
        h = hint_engine.build_hint({"error": IND_ERR}, "answer")
        assert "four spaces" in h["hint"].lower()

    def test_nameerror_mentions_name(self):
        h = hint_engine.build_hint({"error": "NameError: name 'total' is not defined"}, "bigger")
        assert "total" in h["hint"]
        assert h["problem_type"] == "nameerror"

    def test_levels_distinct(self):
        hints = {lvl: hint_engine.build_hint({"error": IND_ERR}, lvl)["hint"] for lvl in ("small", "bigger", "answer")}
        assert len(set(hints.values())) == 3


class TestHintRoute:

    def test_small_hint(self, client):
        d = client.post("/voice-command", json={"text": "give me a small hint", "code": BROKEN, "error": IND_ERR}).get_json()
        assert d["action"] == "deterministic_message"
        assert d["hint_level"] == "small"

    def test_another_hint_escalates(self, client):
        c = client
        c.post("/voice-command", json={"text": "give me a small hint", "code": BROKEN, "error": IND_ERR})
        d2 = c.post("/voice-command", json={"text": "give me another hint", "code": BROKEN, "error": IND_ERR}).get_json()
        assert d2["hint_level"] == "bigger"
        d3 = c.post("/voice-command", json={"text": "give me another hint", "code": BROKEN, "error": IND_ERR}).get_json()
        assert d3["hint_level"] == "answer"

    def test_show_answer(self, client):
        d = client.post("/voice-command", json={"text": "show me the answer", "code": BROKEN, "error": IND_ERR}).get_json()
        assert d["hint_level"] == "answer"
        assert "four spaces" in d["message"].lower()

    def test_do_not_give_answer_yet(self, client):
        d = client.post("/voice-command", json={"text": "do not give me the answer yet", "code": BROKEN, "error": IND_ERR}).get_json()
        assert d["hint_level"] == "small"
        assert "won't give the answer" in d["message"].lower()

    def test_hint_does_not_modify_code(self, client):
        d = client.post("/voice-command", json={"text": "give me a hint", "code": BROKEN, "error": IND_ERR}).get_json()
        assert d["action"] not in _MUTATING
        assert "ai_action" not in d

    def test_tiny_hint_still_goes_to_mentor(self, client):
        # Regression: the existing mentor "tiny hint" command is not hijacked.
        d = client.post("/voice-command", json={"text": "give me a tiny hint"}).get_json()
        assert d["action"] == "mentor_chat"
