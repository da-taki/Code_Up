"""
Repair-command explanations (Problem 5).

A direct repair command ("fix the indentation issue") must, in a single step:
  * apply the fix (a conversational_edit, not a code dump),
  * explain what changed and why it works,
  * tell the learner the next action (run),
without first requiring the staged hint commands. Deterministic — no cloud AI.
The spoken_confirmation IS the audible explanation (applyConversationalEdit
speaks it), so the contract is asserted on that field.
"""
import pytest

import app as app_module

LOOP_BAD_INDENT = "for i in range(3):\nprint(i)\n"
LOOP_OK = "for i in range(3):\n    print(i)\n"


@pytest.fixture
def client(monkeypatch):
    # Force the deterministic local repair path (no Key 2 / cloud AI).
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
    # The edit targets the print line under the loop header.
    assert (ai.get("target") or {}).get("line_number") == 2
    conf = ai.get("spoken_confirmation", "").lower()
    # What changed:
    assert "indent" in conf
    # Why it works (indentation decides what the loop repeats):
    assert "loop" in conf
    # Next action:
    assert "run" in conf


def test_repair_confirmation_is_grounded_and_not_empty(client):
    _d, ai = _edit(client, "fix the indentation issue", LOOP_BAD_INDENT)
    conf = ai.get("spoken_confirmation", "")
    # A real, multi-clause explanation — not the bare "I applied that edit." default.
    assert conf and conf != "I applied that edit."
    assert len(conf) > 60


def test_repair_does_not_require_staged_hints_first(client):
    # A single, first command must already apply + explain the fix — no need to
    # first ask for "a bigger hint" / "show me the answer".
    d, ai = _edit(client, "fix the indentation issue", LOOP_BAD_INDENT)
    assert ai.get("action") == "indent_line"
    assert "run" in ai.get("spoken_confirmation", "").lower()


def test_remove_indentation_explains_the_resulting_error(client):
    # The "make the error visible" command should explain what it did and that
    # running will now surface the error to debug.
    d, ai = _edit(client, "remove the indentation before the print statement so I can see the error", LOOP_OK)
    assert d["action"] == "conversational_edit"
    assert ai.get("action") == "dedent_line"
    conf = ai.get("spoken_confirmation", "").lower()
    assert "indent" in conf
    assert "error" in conf
    assert "run" in conf


def test_repair_speech_payload_exists(client):
    # The frontend speaks ai_action.spoken_confirmation; assert it is present so a
    # blind learner always hears what changed.
    _d, ai = _edit(client, "fix the indentation issue", LOOP_BAD_INDENT)
    assert ai.get("spoken_confirmation")
