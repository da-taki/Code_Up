import os

import pytest

import app as app_module
from intent_parser import parse_intent


ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()


def test_generate_code_exact_pattern_bypasses_cloud_ai(client, monkeypatch):
    def fail_call(*args, **kwargs):
        raise AssertionError("exact-symbol generation must not call cloud AI")

    monkeypatch.setattr(app_module, "call_gemini", fail_call)
    data = client.post("/generate-code", json={"prompt": "make a 5 by 5 star pattern", "source": "voice"}).get_json()
    assert data["success"] is True
    assert data["source"] == "deterministic_exact"
    assert data["exact_symbol"] is True
    assert data["code"] == 'for row in range(5):\n    print("*" * 5)\n'
    assert "type it in the command box" in data["speech"]


@pytest.mark.parametrize(
    "text",
    [
        "print five stars",
        "make a 5 by 5 star pattern",
        "generate code to print a 5x5 pattern",
        "make a 5 by 5 pattern where the third row has 6 stars",
    ],
)
def test_voice_command_exact_patterns_route_to_generation_without_cloud_ai(client, monkeypatch, text):
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cloud AI called")))
    data = client.post("/voice-command", json={"text": text, "source": "voice"}).get_json()
    assert data["success"] is True
    assert data["action"] == "generate_code"
    assert data["source"] == "deterministic_exact"
    assert data["input_source"] == "voice"


def test_sized_pattern_without_symbol_defaults_to_stars(client, monkeypatch):
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cloud AI called")))
    data = client.post("/generate-code", json={"prompt": "generate code to print a 5x5 pattern", "source": "typed"}).get_json()
    assert data["success"] is True
    assert data["source"] == "deterministic_exact"
    assert data["code"] == 'for row in range(5):\n    print("*" * 5)\n'


def test_voice_command_ambiguous_exact_task_routes_to_clarification(client, monkeypatch):
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cloud AI called")))
    data = client.post("/voice-command", json={"text": "make a pattern with exact symbols", "source": "voice"}).get_json()
    assert data["success"] is True
    assert data["action"] == "exact_symbol_clarification"
    assert "could not identify the symbol or the exact counts" in data["message"]


def test_generate_code_normal_ai_path_still_works_for_general_prompt(client, monkeypatch):
    captured = {}

    def fake_call(system, user, **kwargs):
        captured["user"] = user
        return "def average_scores(scores):\n    return sum(scores.values()) / len(scores)\n\nprint(average_scores({'Asha': 8, 'Noor': 10}))\n"

    monkeypatch.setattr(app_module, "call_gemini", fake_call)
    data = client.post(
        "/generate-code",
        json={"prompt": "generate a function that averages numbers from a dictionary", "language": "en"},
    ).get_json()
    assert data["success"] is True
    assert "def average_scores" in data["code"]
    assert "Task description:" in captured["user"]


@pytest.mark.parametrize("text", ["why not use vs code", "how is codeup different from codex"])
def test_product_positioning_routes_without_cloud_ai(client, monkeypatch, text):
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cloud AI called")))
    data = client.post("/voice-command", json={"text": text}).get_json()
    assert data["action"] == "deterministic_message"
    assert "not a replacement for VS Code" in data["message"]
    assert "beginner stage" in data["message"]


@pytest.mark.parametrize("text", ["what happens after codeup", "how do students move to vs code"])
def test_transition_guidance_routes_without_cloud_ai(client, monkeypatch, text):
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cloud AI called")))
    data = client.post("/voice-command", json={"text": text}).get_json()
    assert data["action"] == "deterministic_message"
    assert "professional tools" in data["message"]
    assert "screen reader" in data["message"]


def test_positioning_intents_are_deterministic_before_mentor_chat():
    assert parse_intent("why not use vs code")["intent"] == "product_positioning"
    assert parse_intent("what happens after codeup")["intent"] == "professional_transition"


def test_help_guide_mentions_typed_precision_for_symbols():
    src = _read("static/app.js")
    assert "For exact symbols, patterns, quotes, or long prompts, typing is more reliable." in src
    assert "print five stars" in src
    assert "make a 5 by 5 star pattern where row 3 has 6 stars" in src


@pytest.mark.parametrize(
    "text,expected_action",
    [
        ("clear editor", "clear_editor"),
        ("put a loop from zero to two that prints each number in the editor", "conversational_edit"),
        ("run", "run"),
        ("walk me through this program", "walk_through"),
        ("create a quiz game split into multiple files", "generate_code"),
        ("read project files", "read_project_files"),
        ("open main", "open_project_file"),
        ("run main", "run_project_file"),
        ("start tutorial", "start_tutorial"),
        ("exit tutorial", "skip_tutorial"),
    ],
)
def test_demo_regression_commands_still_route(client, text, expected_action):
    data = client.post("/voice-command", json={"text": text, "code": "print('keep')\n"}).get_json()
    assert data["action"] == expected_action
