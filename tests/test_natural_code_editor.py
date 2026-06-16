import json

import pytest

import app as app_module
import natural_code_editor
import natural_command_mapper
import session_memory


HELLO = 'print("Hello")'
LOOP_3 = "for i in range(3):\n    print(i)"
RANGE_1_5 = "for i in range(1, 6):\n    print(i)"
PATTERN_5 = 'for row in range(5):\n    print("*" * 5)\n'
MARKS = 'marks = 75\n\nif marks >= 40:\n    print("Pass")\nelse:\n    print("Needs practice")'


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


def _mapped(intent, confidence=0.9, slots=None):
    return {
        "status": "mapped",
        "band": natural_command_mapper.confidence_band(confidence),
        "mapping": {
            "intent": intent,
            "confidence": confidence,
            "slots": slots or {},
            "reason": "test",
        },
    }


def _planned(code, confidence=0.91, summary="Updated the current code."):
    return {
        "status": "planned",
        "plan": {
            "action": "replace_current_code",
            "confidence": confidence,
            "updated_code": code,
            "summary": summary,
            "needs_clarification": False,
            "clarification_question": "",
            "safety_notes": [],
        },
    }


def test_planner_prompt_includes_required_context_and_schema():
    seen = {}

    def fake_ai(system, user):
        seen["system"] = system
        seen["user"] = user
        return json.dumps({
            "action": "replace_current_code",
            "confidence": 0.91,
            "updated_code": 'print("Welcome to CodeUp")',
            "summary": "Changed the greeting.",
            "needs_clarification": False,
            "clarification_question": "",
            "safety_notes": [],
        })

    result = natural_code_editor.plan_edit(
        current_code=HELLO,
        edit_instruction="make it say welcome to codeup instead",
        previous_generation_request="print hello",
        last_run_output="Hello",
        last_error="",
        last_edit_summary="",
        mapper_slots={"target": "message"},
        ai_fn=fake_ai,
    )

    assert result["status"] == "planned"
    assert "visually impaired learner" in seen["system"]
    assert "Return JSON only" in seen["system"]
    assert "replace_current_code" in seen["user"]
    assert HELLO in seen["user"]
    assert "make it say welcome to codeup instead" in seen["user"]


@pytest.mark.parametrize("raw,reason", [
    ("not json", "invalid_json"),
    ("```json\n{}\n```", "markdown_response"),
    (json.dumps({"action": "shell", "confidence": 0.99}), "action_not_allowed"),
    (json.dumps({
        "action": "replace_current_code",
        "confidence": 0.99,
        "updated_code": "",
        "summary": "",
    }), "empty_code"),
    (json.dumps({
        "action": "replace_current_code",
        "confidence": 0.99,
        "updated_code": "make it print 5 numbers instead",
        "summary": "",
    }), "transcript_as_code"),
    (json.dumps({
        "action": "replace_current_code",
        "confidence": 0.99,
        "updated_code": "import os\nos.system('dir')",
        "summary": "",
    }), "unsafe_import"),
    (json.dumps({
        "action": "replace_current_code",
        "confidence": 0.99,
        "updated_code": "while True:\n    print('x')",
        "summary": "",
    }), "obvious_infinite_loop"),
    (json.dumps({
        "action": "replace_current_code",
        "confidence": 0.99,
        "updated_code": "for i in range(3)\n    print(i)",
        "summary": "",
    }), "syntax_error"),
])
def test_planner_rejects_invalid_or_unsafe_outputs(raw, reason):
    plan, actual = natural_code_editor.parse_plan_response(
        raw,
        current_code=LOOP_3,
        transcript="make it print 5 numbers instead",
    )

    assert plan is None
    assert actual == reason


def test_ai_mapper_is_consulted_for_natural_edit_and_planner_replaces_code(client, monkeypatch):
    calls = []

    monkeypatch.setattr(app_module, "_structured_ai_available", lambda: True)
    monkeypatch.setattr(
        app_module,
        "_call_ai_natural_command_mapper",
        lambda text, code: calls.append((text, code)) or _mapped("edit_current_code", 0.92, {"target": "message"}),
    )

    def fake_planner(text, current_code, mem, body, slots):
        assert text == "make it say welcome to codeup instead"
        assert current_code == HELLO
        assert slots == {"target": "message"}
        return _planned('print("Welcome to CodeUp")', summary="Changed the greeting.")

    monkeypatch.setattr(app_module, "_call_ai_code_edit_planner", fake_planner)

    data = _vc(client, "make it say welcome to codeup instead", code=HELLO)

    assert calls == [("make it say welcome to codeup instead", HELLO)]
    assert data["action"] == "conversational_edit"
    assert data["ai_action"]["action"] == "replace_code"
    assert data["ai_action"]["code"] == 'print("Welcome to CodeUp")'
    assert data["ai_action"]["requires_confirmation"] is False
    assert data["natural_code_edit"] is True
    assert "Changed the greeting" in data["speech"]


def test_exact_deterministic_commands_do_not_call_ai_mapper(client, monkeypatch):
    monkeypatch.setattr(app_module, "_structured_ai_available", lambda: True)

    def fail_mapper(_text, _code):
        raise AssertionError("exact deterministic commands should not call AI")

    monkeypatch.setattr(app_module, "_call_ai_natural_command_mapper", fail_mapper)

    assert _vc(client, "run", code=HELLO)["action"] == "run"
    assert _vc(client, "stop everything", code=HELLO)["action"] == "stop_everything"
    assert _vc(client, "what can I do here", code=HELLO)["action"] == "deterministic_message"


@pytest.mark.parametrize("confidence,reason", [(0.70, "medium_confidence"), (0.40, "low_confidence")])
def test_low_and_medium_confidence_edit_plans_clarify(client, monkeypatch, confidence, reason):
    monkeypatch.setattr(app_module, "_structured_ai_available", lambda: True)
    monkeypatch.setattr(
        app_module,
        "_call_ai_natural_command_mapper",
        lambda _text, _code: _mapped("edit_current_code", 0.90),
    )
    monkeypatch.setattr(
        app_module,
        "_call_ai_code_edit_planner",
        lambda *_args, **_kw: _planned("for i in range(5):\n    print(i)", confidence=confidence),
    )

    data = _vc(client, "make it print 5 numbers instead", code=LOOP_3)

    assert data["action"] == "clarify"
    assert data["needs_clarification"] is True
    assert data["reason"] == reason
    assert "ai_action" not in data


def test_invalid_ai_edit_plan_is_rejected_and_api_key_redacted(client, monkeypatch):
    secret = "gsk_live_test_secret_789"
    monkeypatch.setenv("GROQ_API_KEY", secret)
    monkeypatch.setattr(app_module, "_structured_ai_available", lambda: True)
    monkeypatch.setattr(
        app_module,
        "_call_ai_natural_command_mapper",
        lambda _text, _code: _mapped("edit_current_code", 0.90),
    )
    monkeypatch.setattr(
        app_module,
        "_call_ai_code_edit_planner",
        lambda *_args, **_kw: {"status": "invalid", "reason": f"invalid_json {secret}"},
    )

    data = _vc(client, "make it print 5 numbers instead", code=LOOP_3)

    dumped = json.dumps(data)
    assert data["action"] == "clarify"
    assert secret not in dumped
    assert "<redacted-api-key>" in dumped


@pytest.mark.parametrize("code,command,expected", [
    (HELLO, "make it say welcome to codeup instead", 'print("Welcome to CodeUp")'),
    (LOOP_3, "make it print 5 numbers instead", "for i in range(5):\n    print(i)"),
    (RANGE_1_5, "make it go from 1 to 10", "for i in range(1, 11):\n    print(i)"),
    (MARKS, "change passing marks from 40 to 50", MARKS.replace("marks >= 40", "marks >= 50")),
    (MARKS, "make it ask the user for marks", MARKS.replace("marks = 75", 'marks = int(input("Enter your marks: "))')),
    (
        PATTERN_5,
        "make the third row have 6 rather than 5 of the pattern",
        'for row in range(5):\n    if row == 2:\n        print("*" * 6)\n    else:\n        print("*" * 5)',
    ),
    (
        PATTERN_5,
        "make it use numbers instead of stars",
        "for row in range(5):\n    print(str(row + 1) * 5)",
    ),
])
def test_local_natural_editor_supports_beginner_followup_edits(client, code, command, expected):
    data = _vc(client, command, code=code)

    assert data["action"] == "conversational_edit", data
    assert data["ai_action"]["action"] == "replace_code"
    assert data["ai_action"]["code"] == expected
    assert command.lower() not in data["ai_action"]["code"].lower()
    assert data["natural_code_edit"] is True


def test_no_code_context_asks_for_code_before_editing(client):
    data = _vc(client, "change this", code="")

    assert data["action"] == "clarify"
    assert data["needs_clarification"] is True
    assert "generate code" in data["speech"].lower()
    assert "ai_action" not in data


def test_unsafe_edit_refuses_without_inserting_infinite_loop(client):
    data = _vc(client, "make it run forever", code=LOOP_3)

    assert data["action"] == "clarify"
    assert data["needs_clarification"] is True
    assert "will not" in data["speech"].lower()
    assert "ai_action" not in data


def test_natural_edit_stores_old_new_code_and_summary_in_memory(client):
    data = _vc(client, "make it print 5 numbers instead", code=LOOP_3)
    assert data["action"] == "conversational_edit"

    mem = session_memory.get_memory(app_module.get_trace_storage())
    assert mem["last_edit_request"] == "make it print 5 numbers instead"
    assert mem["last_edit_old_code"] == LOOP_3
    assert mem["last_edit_new_code"] == "for i in range(5):\n    print(i)"
    assert "5 numbers" in mem["last_edit_summary"]


def test_followup_local_edits_use_latest_code_supplied_by_editor(client):
    bigger = _vc(client, "make it bigger", code=PATTERN_5)
    assert bigger["action"] == "conversational_edit"
    bigger_code = bigger["ai_action"]["code"]
    assert "range(6)" in bigger_code

    smaller = _vc(client, "now make it smaller", code=bigger_code)

    assert smaller["action"] == "conversational_edit"
    assert smaller["ai_action"]["code"] == PATTERN_5.strip()
