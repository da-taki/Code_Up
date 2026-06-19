import pytest

import app as app_module
import grounded_ai as g


DET_Q = "Do you want a 5 by 5 pattern, and should the third line have a different number of symbols?"
DET_DELETE = "Which file should I delete?"
DET_NO_ERROR = "There is no recent error to fix. What would you like me to look at?"
QUOTES_NOTE = "Quotes tell Python the words inside are text to show, not the name of a variable."


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as flask_client:
        yield flask_client


def test_clarification_preserving_facts_is_accepted():
    ai = "Do you want a 5 by 5 pattern where the third line has a different number of symbols?"
    out = g.ground(ai, DET_Q, required_facts=["5 by 5", "third line"], context=DET_Q, single_sentence=True)
    assert out == ai


def test_clarification_dropping_facts_is_rejected():
    weak = "What type of thing are you trying to create?"
    out = g.ground(weak, DET_Q, required_facts=["5 by 5", "third line"], context=DET_Q, single_sentence=True)
    assert out == DET_Q


def test_ai_cannot_invent_filename_with_no_file_context():
    weak = "Do you want me to delete main.py?"
    out = g.ground(weak, DET_DELETE, context="delete that file")
    assert out == DET_DELETE


def test_ai_may_use_filename_present_in_context():
    det = "Do you want me to delete main.py? Say yes to confirm, or name another file."
    ai = "Do you want me to delete main.py?"
    out = g.ground(ai, det, context="delete that file main.py")
    assert out == ai


def test_ai_cannot_invent_an_error():
    weak = "The error is probably caused by indentation."
    out = g.ground(weak, DET_NO_ERROR, required_facts=["recent error"])
    assert out == DET_NO_ERROR


def test_coach_quotes_rephrase_preserves_meaning():
    good = "Quotes tell Python that these words are text, not a variable name."
    bad = "Quotes make the program run faster."
    facts = ["quotes", "text", "variable"]
    assert g.ground(good, QUOTES_NOTE, required_facts=facts, single_sentence=True) == good
    assert g.ground(bad, QUOTES_NOTE, required_facts=facts, single_sentence=True) == QUOTES_NOTE


def test_modify_followup_preserves_ten_and_even():
    facts = ["10", "even"]
    ctx = "do the same with 10 print the first five even numbers"
    assert g.ground("Print the first 10 even numbers.", "FALLBACK", required_facts=facts, context=ctx) == "Print the first 10 even numbers."
    assert g.ground("Print 10 random numbers.", "FALLBACK", required_facts=facts, context=ctx) == "FALLBACK"
    assert g.ground("Create a calculator.", "FALLBACK", required_facts=facts, context=ctx) == "FALLBACK"


def test_overlong_response_is_rejected():
    assert g.ground("word " * 200, "DET") == "DET"
    assert g.ground("x" * 400, "DET") == "DET"


def test_multiline_response_is_rejected_when_single_sentence_required():
    assert g.ground("First line.\nSecond line.", "DET", single_sentence=True) == "DET"
    assert g.ground("para one\n\npara two", "DET") == "DET"


def test_rejected_ai_returns_deterministic_fallback():
    assert g.ground("print('hi')", "Say the command.", single_sentence=True) == "Say the command."


def test_no_invented_number_even_when_facts_preserved():
    ai = "Do you want a 5 by 5 pattern with 9 stars on the third line?"
    out = g.ground(ai, DET_Q, required_facts=["5 by 5", "third line"], context=DET_Q)
    assert out == DET_Q


def test_validate_reports_reason():
    ok, reason = g.validate("What type of thing?", deterministic_text=DET_Q, required_facts=["5 by 5", "third line"])
    assert ok is False
    assert reason.startswith("dropped_fact")


class TestClarifierWiring:
    PATTERN_Q = "What symbol should I use, and how many symbols should the third line have?"

    def _vague(self, client):
        return client.post(
            "/voice-command",
            json={"text": "make a five by five thing with the third line different", "source": "voice"},
        ).get_json()

    def test_route_rejects_weaker_ai_and_keeps_specific_question(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai",
                            lambda system, user: "What type of thing are you trying to create?")
        data = self._vague(client)
        assert data["action"] == "clarify"
        assert data["message"] == self.PATTERN_Q

    def test_route_accepts_grounded_ai_rephrase(self, client, monkeypatch):
        better = "What symbol, and how many symbols should the third line have?"
        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", lambda system, user: better)
        data = self._vague(client)
        assert data["action"] == "clarify"
        assert data["message"] == better

    def test_route_rejects_ai_that_invents_a_filename(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai",
                            lambda system, user: "Do you want a 10 by 10 grid instead?")
        data = self._vague(client)
        assert data["message"] == self.PATTERN_Q


class TestCoachWiring:
    def test_coach_rejects_weakening_and_keeps_fact(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai",
                            lambda system, user: "Quotes make the program run faster.")
        data = client.post("/tutorial/coach", json={"module": "print", "text": "why do we use quotes"}).get_json()
        assert data["handled"] is True
        assert data["source"] == "deterministic"
        assert "text" in data["text"].lower()

    def test_coach_accepts_grounded_rephrase(self, client, monkeypatch):
        good = "Quotes tell Python that these words are text, not a variable name."
        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", lambda system, user: good)
        data = client.post("/tutorial/coach", json={"module": "print", "text": "why do we use quotes"}).get_json()
        assert data["handled"] is True
        assert data["source"] == "ai_coached"
        assert data["text"] == good
