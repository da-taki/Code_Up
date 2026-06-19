import pytest

import app as app_module

LOOP_BAD_INDENT = "for i in range(3):\nprint(i)\n"
LOOP_OK = "for i in range(3):\n    print(i)\n"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _edit(client, text, code):
    d = client.post("/voice-command", json={"text": text, "code": code}).get_json()
    return d, (d.get("ai_action") or {})


def test_fix_indentation_applies_edit_and_explains(client):
    d, ai = _edit(client, "fix the indentation issue", LOOP_BAD_INDENT)
    assert d["action"] == "conversational_edit"
    assert ai.get("action") == "indent_line"
    assert (ai.get("target") or {}).get("line_number") == 2
    conf = ai.get("spoken_confirmation", "").lower()
    assert "indent" in conf
    assert "loop" in conf
    assert "run" in conf


def test_repair_confirmation_is_grounded_and_not_empty(client):
    _d, ai = _edit(client, "fix the indentation issue", LOOP_BAD_INDENT)
    conf = ai.get("spoken_confirmation", "")
    assert conf and conf != "I applied that edit."
    assert len(conf) > 60


def test_repair_does_not_require_staged_hints_first(client):
    d, ai = _edit(client, "fix the indentation issue", LOOP_BAD_INDENT)
    assert ai.get("action") == "indent_line"
    assert "run" in ai.get("spoken_confirmation", "").lower()


def test_remove_indentation_explains_the_resulting_error(client):
    d, ai = _edit(client, "remove the indentation before the print statement so I can see the error", LOOP_OK)
    assert d["action"] == "conversational_edit"
    assert ai.get("action") == "dedent_line"
    conf = ai.get("spoken_confirmation", "").lower()
    assert "indent" in conf
    assert "error" in conf
    assert "run" in conf


def test_repair_speech_payload_exists(client):
    _d, ai = _edit(client, "fix the indentation issue", LOOP_BAD_INDENT)
    assert ai.get("spoken_confirmation")
