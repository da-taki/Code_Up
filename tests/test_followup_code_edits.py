import pytest

import app as app_module
import session_memory


EVEN = "for i in range(5):\n    if i % 2 == 0:\n        print(i)\n"


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


def _generate_first(client):
    d = _vc(client, "write a program for the first five even numbers")
    assert d["action"] == "generate_code"
    return d


def _is_clarify(d):
    return d.get("needs_clarification") is True



class TestDetectCorrection:

    @pytest.mark.parametrize("text,expected_in", [
        ("oh I meant make it first ten even numbers", "make it first ten even numbers"),
        ("actually use a while loop instead", "while loop"),
        ("oh I meant print odd numbers instead", "odd numbers"),
        ("actually make it a function", "function"),
    ])
    def test_detects_corrections(self, text, expected_in):
        out = session_memory.detect_correction(text)
        assert out is not None and expected_in in out.lower()

    @pytest.mark.parametrize("text", ["actually run it", "run the code", "stop speaking"])
    def test_plain_commands_are_not_corrections(self, text):
        assert session_memory.detect_correction(text) is None



class TestFollowupEdits:

    def test_oh_i_meant_edits_existing_code(self, client):
        _generate_first(client)
        d = _vc(client, "oh I meant make it first ten even numbers", code=EVEN)
        assert d["action"] == "generate_code"
        assert d.get("source") == "memory_followup"
        assert d.get("followup_edit") is True
        assert "ten" in d["prompt"].lower()

    def test_actually_use_odd_instead_edits_existing_code(self, client):
        _generate_first(client)
        d = _vc(client, "actually use odd numbers instead", code=EVEN)
        assert d["action"] == "generate_code"
        assert d.get("source") == "memory_followup"
        assert "odd" in d["prompt"].lower()

    def test_change_variable_name_edits_current_code(self, client):
        _generate_first(client)
        d = _vc(client, "change the variable name to total", code=EVEN)
        assert d["action"] == "generate_code"
        assert d.get("source") == "memory_followup"
        assert "total" in d["prompt"].lower()

    def test_correction_grounds_in_prior_prompt(self, client):
        _generate_first(client)
        d = _vc(client, "oh I meant make it first ten even numbers", code=EVEN)
        assert "even" in d["prompt"].lower()



class TestFollowupClarifies:

    def test_ambiguous_correction_asks_clarification(self, client):
        _generate_first(client)
        d = _vc(client, "actually, change that", code=EVEN)
        assert _is_clarify(d)
        assert "change" in d["message"].lower()

    def test_no_context_asks_clarification(self, client):
        d = _vc(client, "oh I meant first ten even numbers", code="")
        assert _is_clarify(d)



class TestGenerationValidatesParse:

    def test_empty_prompt_is_rejected(self, client):
        resp = client.post("/generate-code", json={"prompt": ""})
        assert resp.status_code == 400

    def test_generated_code_parses(self, client):
        data = client.post("/generate-code", json={"prompt": "print numbers zero to two"}).get_json()
        assert data["success"] is True and data.get("code")
        compile(data["code"], "<gen>", "exec")



class TestNotAFollowup:

    def test_new_generation_still_generates(self, client):
        d = _vc(client, "write a program for the first five even numbers")
        assert d["action"] == "generate_code"
        assert d.get("source") != "memory_followup"
