import json
import os

import pytest

import app as app_module
from conversation_orchestrator import (
    frontend_actions,
    orchestrate_command,
    strip_wake_phrase,
    validate_plan,
)


ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as flask_client:
        yield flask_client


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hey code up run", "run"),
        ("hey cold up run", "run"),
        ("cold up explain this program", "explain this program"),
        ("codup map my code", "map my code"),
    ],
)
def test_wake_phrase_normalization(text, expected):
    stripped = strip_wake_phrase(text)
    assert stripped["wake_detected"] is True
    assert stripped["text"] == expected


def test_no_wake_phrase_still_plans_when_forced_by_multi_intent():
    plan = orchestrate_command("generate code to print zero to two then run it")
    assert [action["type"] for action in plan["actions"]] == ["generate_code", "run"]


def test_missing_second_key_falls_back_to_deterministic_parser(client):
    data = client.post("/voice-command", json={"text": "hey code up run", "code": "print('ok')"}).get_json()
    assert data["success"] is True
    assert data["action"] == "run"
    assert data["normalized_text"] == "run"


def test_second_key_service_error_falls_back_safely(monkeypatch):
    def failing_ai(*args, **kwargs):
        raise RuntimeError("service busy")

    plan = orchestrate_command("hey code up run", ai_plan_fn=failing_ai)
    assert plan["actions"][0]["type"] == "run"


def test_no_secret_is_logged_for_second_key(client, monkeypatch, capsys):
    monkeypatch.setenv("GROQ_API_KEY_2", "secret-second-key-value")
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", lambda *a, **k: "")
    data = client.post("/voice-command", json={"text": "hey code up run", "code": "print('ok')"}).get_json()
    assert data["action"] == "run"
    captured = capsys.readouterr()
    assert "secret-second-key-value" not in captured.out
    assert "secret-second-key-value" not in captured.err


def test_multi_intent_generate_run_explain_plan():
    plan = orchestrate_command("Hey CodeUp, generate code to print numbers zero to two, then run it, then explain it.")
    assert [action["type"] for action in plan["actions"]] == ["generate_code", "run", "explain"]
    assert plan["actions"][0]["prompt"] == "print numbers zero to two"
    assert "generate code" in plan["spoken_summary"]


def test_multi_file_plan_maps_to_frontend_actions():
    plan = orchestrate_command("create a quiz game split into multiple files then open main and run main")
    actions = frontend_actions(plan)
    assert [action["action"] for action in actions] == ["generate_code", "open_project_file", "run_project_file"]
    assert actions[1]["path"] == "main"
    assert actions[2]["path"] == "main"


def test_ambiguous_multi_action_asks_clarification():
    plan = orchestrate_command("make a cube then run it")
    assert plan["needs_clarification"] is True
    assert "2D pattern" in plan["clarification_question"]


def test_noisy_asterisk_command_preserves_exact_constraints():
    plan = orchestrate_command(
        "hey cold up generate code to print a pattern of five by five cube with ast risks but third line has six not five",
        source="voice",
    )
    assert plan["needs_clarification"] is False
    assert plan["actions"][0]["type"] == "generate_code"
    assert plan["actions"][0]["requires_exact_symbols"] is True
    assert "row 3 has 6" in plan["actions"][0]["constraints"]["understood"]
    actions = frontend_actions(plan, input_source="voice")
    assert actions[0]["action"] == "generate_code"
    assert actions[0]["input_source"] == "voice"


def test_unsupported_ambiguous_cube_asks_clarification():
    plan = orchestrate_command("make a cube")
    assert plan["needs_clarification"] is True
    assert plan["clarification_question"] == "Do you mean a printed 2D pattern or a 3D cube concept?"


def test_symbol_normalization_is_available_to_orchestrator():
    plan = orchestrate_command("make a five by five hash pattern")
    assert plan["actions"][0]["requires_exact_symbols"] is True
    assert "5 rows and 5 columns using #" in plan["actions"][0]["constraints"]["understood"]


def test_ai_prompt_rewrite_plan_is_validated_and_preserved():
    def fake_ai(system, user):
        assert "CodeUp's conversation orchestrator" in system
        assert "make a marks thing" in user
        return json.dumps({
            "confidence": 0.84,
            "normalized_text": "Generate a beginner-friendly marks dictionary program, then explain it.",
            "needs_clarification": False,
            "clarification_question": "",
            "actions": [
                {
                    "type": "generate_code",
                    "source": "ai_orchestrated",
                    "prompt": "Generate a beginner-friendly Python program that stores marks for three students in a dictionary, calculates the average using a function, and prints students whose marks are above average. Keep the code runnable without input(), and add simple comments.",
                    "constraints": {},
                    "requires_exact_symbols": False,
                },
                {
                    "type": "explain",
                    "source": "ai_orchestrated",
                    "prompt": "",
                    "constraints": {},
                    "requires_exact_symbols": False,
                },
            ],
            "spoken_summary": "I will generate a clearer marks program, then explain it.",
        })

    plan = orchestrate_command("hey code up make a marks thing and explain it", ai_plan_fn=fake_ai)
    assert plan["actions"][0]["source"] == "ai_orchestrated"
    assert "dictionary" in plan["actions"][0]["prompt"]
    assert [action["type"] for action in plan["actions"]] == ["generate_code", "explain"]


def _marks_rewrite_plan(actions=None):
    return json.dumps({
        "confidence": 0.83,
        "normalized_text": "Generate a beginner-friendly marks program.",
        "needs_clarification": False,
        "clarification_question": "",
        "actions": actions or [
            {
                "type": "generate_code",
                "source": "ai_orchestrated",
                "prompt": (
                    "Generate a beginner-friendly Python program that stores marks for "
                    "three students in a dictionary, computes the average with a function, "
                    "and prints who scored above average. Keep it runnable without input()."
                ),
                "constraints": {},
                "requires_exact_symbols": False,
            },
        ],
        "spoken_summary": "Generating a clearer marks program.",
    })


def test_rough_generation_is_rewritten_by_key2_before_coding_path():
    # No wake phrase and no connector: a vague generation ask must still be sent
    # to Key 2, which rewrites the rough prompt before the coding path runs.
    def fake_ai(system, user):
        assert "make a marks thing" in user
        return _marks_rewrite_plan()

    plan = orchestrate_command("make a marks thing", ai_plan_fn=fake_ai)
    assert plan["actions"][0]["source"] == "ai_orchestrated"
    assert "dictionary" in plan["actions"][0]["prompt"]
    assert plan["actions"][0]["prompt"] != "a marks thing"


def test_non_forced_multi_action_generation_is_rewritten_before_split():
    # "make a marks thing and explain it" has no wake and no "then"; Key 2 still
    # gets first crack so the rough prompt is rewritten rather than passed raw.
    def fake_ai(system, user):
        return _marks_rewrite_plan(actions=[
            {
                "type": "generate_code",
                "source": "ai_orchestrated",
                "prompt": "Generate a beginner-friendly Python marks dictionary program with an average function.",
                "constraints": {},
                "requires_exact_symbols": False,
            },
            {"type": "explain", "source": "ai_orchestrated", "prompt": "", "constraints": {}, "requires_exact_symbols": False},
        ])

    plan = orchestrate_command("make a marks thing and explain it", ai_plan_fn=fake_ai)
    assert [action["type"] for action in plan["actions"]] == ["generate_code", "explain"]
    assert "average" in plan["actions"][0]["prompt"]


def test_generation_falls_back_to_deterministic_when_key2_missing():
    # Key 2 missing/busy (no ai_plan_fn): the deterministic split still yields a
    # safe action sequence instead of crashing or dropping the request.
    plan = orchestrate_command("make a marks thing and explain it")
    assert [action["type"] for action in plan["actions"]] == ["generate_code", "explain"]


def test_simple_commands_do_not_consult_key2():
    calls = []

    def spy_ai(system, user):
        calls.append(user)
        return ""

    for command in [
        "run",
        "clear editor",
        "open main",
        "map my code",
        "sonify block",
        "help",
        "start tutorial",
        "exit tutorial",
    ]:
        orchestrate_command(command, ai_plan_fn=spy_ai)
    assert calls == []


def test_exact_symbol_request_never_consults_key2():
    def boom_ai(*args, **kwargs):
        raise AssertionError("Key 2 must not be consulted for a confident exact-symbol task")

    plan = orchestrate_command(
        "make a 5 by 5 pattern where the third row has 6 stars",
        ai_plan_fn=boom_ai,
    )
    assert plan["actions"][0]["requires_exact_symbols"] is True
    assert "row 3 has 6" in plan["actions"][0]["constraints"]["understood"]


def test_voice_route_sends_rough_generation_to_key2(client, monkeypatch):
    def fake_orchestrator_ai(system, user):
        assert "marks" in user
        return _marks_rewrite_plan()

    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fake_orchestrator_ai)
    # The coding model (Key 1) must not be invoked during the orchestration step.
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Key 1 called during orchestration")))
    data = client.post("/voice-command", json={"text": "make a marks thing", "code": ""}).get_json()
    assert data["action"] == "generate_code"
    assert data["source"] == "ai_orchestrated"
    assert "dictionary" in data["prompt"]
    assert data["next_action"] == "Generating code."


def test_ai_plan_rejects_unknown_action_type():
    plan = validate_plan({
        "confidence": 1,
        "normalized_text": "bad",
        "actions": [{"type": "delete_everything", "source": "ai_orchestrated"}],
    })
    assert plan["actions"] == []


def test_voice_command_route_returns_action_sequence_for_multi_intent(client):
    data = client.post(
        "/voice-command",
        json={"text": "hey code up generate code to print numbers zero to two then run it then walk me through it", "code": ""},
    ).get_json()
    assert data["action"] == "action_sequence"
    assert [action["action"] for action in data["actions"]] == ["generate_code", "run", "walk_through"]
    assert data["next_action"] == "Generating code."


def test_voice_command_route_exact_symbol_stays_deterministic(client, monkeypatch):
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: (_ for _ in ()).throw(AssertionError("coding AI called")))
    data = client.post(
        "/voice-command",
        json={"text": "hey cold up generate code for a five by five pattern with ast risks but third line has six not five", "source": "voice"},
    ).get_json()
    assert data["action"] == "generate_code"
    assert data["source"] == "deterministic_exact"
    assert "row 3 has 6" in data["normalized_text"]
    assert data["next_action"] == "Creating deterministic pattern code."


def test_new_command_after_clarification_is_not_concatenated(client):
    first = client.post("/voice-command", json={"text": "make a cube", "code": ""}).get_json()
    assert first["action"] == "orchestrator_clarification"
    assert "2D pattern" in first["message"]

    second = client.post("/voice-command", json={"text": "make a pattern with symbols", "code": ""}).get_json()
    assert second["action"] == "exact_symbol_clarification"
    assert second["normalized_text"] == "make a pattern with symbols"
    assert "make a cube" not in second["normalized_text"]


def test_fix_and_run_without_code_asks_for_clarification(client):
    data = client.post("/voice-command", json={"text": "fix it and run it", "code": ""}).get_json()
    assert data["action"] == "orchestrator_clarification"
    assert data["normalized_text"] == "fix it and run it"
    assert "no code" in data["message"].lower()


def test_generate_zero_to_two_fallback_needs_no_ai(client, monkeypatch):
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: (_ for _ in ()).throw(AssertionError("coding AI called")))
    data = client.post("/generate-code", json={"prompt": "print numbers zero to two"}).get_json()
    assert data["success"] is True
    assert data["source"] == "local_fallback"
    assert data["code"] == "for i in range(3):\n    print(i)\n"


def test_transcript_ui_contract_present():
    html = _read("templates/index.html")
    app_js = _read("static/app.js")
    voice_engine = _read("static/voice-engine.js")
    assert 'id="commandUnderstanding"' in html
    assert 'aria-live="polite"' in html
    assert "Heard:" in html
    assert "Understood:" in html
    assert "Next action:" in html
    assert "function updateCommandUnderstanding" in app_js
    assert "window.updateTranscriptStatus = updateCommandUnderstanding" in app_js
    assert "applyCommandUnderstanding(data" in app_js
    assert "recognition.interimResults  = true" in app_js
    assert "_recognition.interimResults = true" in voice_engine
