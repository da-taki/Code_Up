from pathlib import Path

import pytest

import app as app_module
from codeup.commands import deterministic_code_tools as tools
from app import app
from codeup.commands.intent_parser import parse_intent


CODE = """import math as maths
total = 0
for count in range(3):
    total += count
print(total)

def calculate_total(value):
    return value + total

class Student:
    pass
"""

COMMANDS = {
    "goto_definition": [
        "go to definition of total", "go to function calculate_total",
        "find definition of score", "where is main defined",
    ],
    "find_references": [
        "where is total used", "find uses of score", "find references to name",
        "where do I use count",
    ],
    "file_outline": ["outline this file", "summarize file structure", "read file outline"],
    "list_audio_breakpoints": ["list breakpoints"],
    "clear_breakpoints": ["clear breakpoints"],
    "remove_breakpoint": ["remove breakpoint line 5"],
    "disable_breakpoints": ["disable breakpoints"],
    "enable_breakpoints": ["enable breakpoints"],
    "next_step": ["next step"],
    "previous_step": ["previous step"],
    "repeat_step": ["repeat step"],
    "first_step": ["go to first step"],
    "last_step": ["go to last step"],
    "safe_rename": [
        "rename total to score", "rename variable count to index",
        "change name from total to score",
    ],
    "name_conflicts": ["check names", "find name problems", "check for shadowing"],
}


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def test_definition_supports_assignments_loops_functions_classes_and_imports():
    assert tools.find_definition(CODE, "total")["line"] == 2
    assert tools.find_definition(CODE, "count")["line"] == 3
    assert tools.find_definition(CODE, "calculate_total")["message"] == "Function calculate_total starts on line 7."
    assert tools.find_definition(CODE, "Student")["message"] == "Class Student starts on line 10."
    assert tools.find_definition(CODE, "maths")["message"] == "maths is imported on line 1."
    assert "could not find" in tools.find_definition(CODE, "missing")["message"]


def test_references_separate_assignments_and_uses():
    result = tools.find_references(CODE, "total")
    assert result["assigned_lines"] == [2, 4]
    assert result["used_lines"] == [4, 5, 8]
    assert "assigned on lines 2, 4" in result["message"]
    assert "used on lines 4, 5, 8" in result["message"]


def test_file_outline_is_short_and_structural():
    outline = tools.file_outline(CODE)
    assert "1 import" in outline
    assert "1 top-level variable" in outline
    assert "1 function" in outline
    assert "1 class" in outline
    assert "Function calculate_total starts on line 7" in outline


def test_safe_rename_uses_tokens_and_ast_positions():
    code = """total = 0
text = "total"
# total stays in this comment
obj.total = total
for total in range(2):
    print(total)
"""
    result = tools.rename_variable(code, "total", "score")
    assert result["success"] is True
    assert result["count"] == 4
    assert 'text = "total"' in result["code"]
    assert "# total stays" in result["code"]
    assert "obj.total = score" in result["code"]
    assert "for score in" in result["code"]


def test_safe_rename_handles_parameters_and_refuses_collisions_and_imports():
    renamed = tools.rename_variable("def show(count):\n    return count\n", "count", "index")
    assert renamed["success"] is True and renamed["count"] == 2
    collision = tools.rename_variable("total = 1\nscore = 2\n", "total", "score")
    assert collision["success"] is False and "already exists" in collision["message"]
    builtin = tools.rename_variable("total = 1\n", "total", "list")
    assert builtin["success"] is False and "Python builtin" in builtin["message"]
    imported = tools.rename_variable("import math\nprint(math.pi)\n", "math", "numbers")
    assert imported["success"] is False and "local variable" in imported["message"]


def test_name_conflict_checks_duplicates_and_builtin_shadowing():
    duplicate = "def calculate():\n    pass\ndef calculate():\n    pass\n"
    assert tools.name_conflicts(duplicate) == "Function calculate is defined twice, on lines 1 and 3."
    assert tools.name_conflicts("list = []\n") == "Variable list shadows a Python builtin on line 1."
    assert tools.name_conflicts("value = 1\n") == "I do not see obvious name conflicts."


@pytest.mark.parametrize("text,intent", [
    (text, intent) for intent, phrases in COMMANDS.items() for text in phrases
])
def test_every_command_phrase_has_an_explicit_intent(text, intent):
    assert parse_intent(text)["intent"] == intent


@pytest.mark.parametrize("text,action", [
    ("go to definition of total", "navigate_code"),
    ("where is total used", "navigate_code"),
    ("outline this file", "deterministic_message"),
    ("rename total to score", "conversational_edit"),
    ("check names", "deterministic_message"),
    ("list breakpoints", "list_audio_breakpoints"),
    ("remove breakpoint line 5", "remove_breakpoint"),
    ("disable breakpoints", "disable_breakpoints"),
    ("enable breakpoints", "enable_breakpoints"),
    ("repeat step", "deterministic_message"),
    ("go to first step", "deterministic_message"),
    ("go to last step", "deterministic_message"),
])
def test_voice_routes_are_deterministic(client, monkeypatch, text, action):
    def fail_ai(*args, **kwargs):
        raise AssertionError("AI provider must not be called")

    monkeypatch.setattr(app_module, "call_gemini", fail_ai)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail_ai)
    data = client.post("/voice-command", json={"text": text, "code": CODE}).get_json()
    assert data["action"] == action


@pytest.mark.parametrize("text,intent", [
    (text, intent) for intent, phrases in COMMANDS.items() for text in phrases
])
def test_every_phrase_routes_through_voice_command(client, text, intent):
    action_by_intent = {
        "goto_definition": "navigate_code",
        "find_references": "navigate_code",
        "file_outline": "deterministic_message",
        "safe_rename": "conversational_edit",
        "name_conflicts": "deterministic_message",
        "repeat_step": "deterministic_message",
        "first_step": "deterministic_message",
        "last_step": "deterministic_message",
    }
    data = client.post("/voice-command", json={"text": text, "code": CODE}).get_json()
    assert data["action"] == action_by_intent.get(intent, intent)


def test_navigation_routes_return_target_line(client):
    definition = client.post(
        "/voice-command", json={"text": "go to function calculate_total", "code": CODE},
    ).get_json()
    references = client.post(
        "/voice-command", json={"text": "where do I use count", "code": CODE},
    ).get_json()
    assert definition["line"] == 7 and "Function calculate_total" in definition["speech"]
    assert references["line"] == 3 and "used on line 4" in references["speech"]


def test_safe_rename_route_returns_existing_replace_code_action(client):
    data = client.post(
        "/voice-command",
        json={"text": "rename total to score", "code": "total = 1\nprint(total)\n"},
    ).get_json()
    assert data["action"] == "conversational_edit"
    assert data["ai_action"]["action"] == "replace_code"
    assert data["ai_action"]["code"] == "score = 1\nprint(score)"
    assert data["speech"] == "Renamed total to score in 2 places."


def test_trace_aliases_use_existing_session_trace(client):
    run = client.post("/run", json={"code": "total = 0\ntotal += 1\nprint(total)\n"}).get_json()
    assert run["success"] is True
    first = client.post("/voice-command", json={"text": "go to first step"}).get_json()
    repeated = client.post("/voice-command", json={"text": "repeat step"}).get_json()
    last = client.post("/voice-command", json={"text": "go to last step"}).get_json()
    assert first["speech"].startswith("Step 1 of")
    assert repeated["speech"] == first["speech"]
    assert last["speech"].startswith("Step ")


def test_breakpoint_frontend_wires_existing_client_state():
    source = Path("static/app.js").read_text(encoding="utf-8")
    assert "function listBreakpoints()" in source
    assert "function removeBreakpoint(lineNum)" in source
    assert "function disableBreakpoints()" in source
    assert "function enableBreakpoints()" in source
    assert "if (!_breakpointsEnabled)" in source
    assert "requestAudioBreakpoint('clear', null, { silent: true })" in source
    assert "out('Cleared all breakpoints.')" in source
