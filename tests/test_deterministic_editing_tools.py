import pytest

import app as app_module
from codeup.commands import deterministic_code_tools as tools
from app import app
from codeup.commands.intent_parser import parse_intent


CODE = """import math
total = 0
for count in range(3):
    if count > 0:
        total += count
print(total)

def first():
    pass

class Student:
    pass

def second():
    pass
# TODO handle empty input
"""

COMMANDS = {
    "current_block": ["read current block", "read this block", "describe current block"],
    "adjacent_symbol": [
        "go to next function", "next function", "go to previous function", "previous function",
        "go to next class", "go to previous class",
    ],
    "next_error": ["go to next error", "jump to error", "where is the error"],
    "check_brackets": ["check brackets", "check parentheses", "are my brackets balanced"],
    "check_strings": ["check strings", "check quotes", "are my strings closed"],
    "check_long_lines": ["check long lines", "find long lines", "readability check"],
    "comment_line": ["comment this line", "comment current line"],
    "uncomment_line": ["uncomment this line", "uncomment current line"],
    "duplicate_line": ["duplicate this line", "duplicate current line", "copy this line below"],
    "delete_blank_lines": ["delete blank lines", "remove blank lines", "clean blank lines"],
    "expected_output": ["expect output 6", "expected output hello", "compare output to 10", "should print 15"],
    "run_history": ["show run history", "what have I run", "run summary"],
    "reset_run_state": ["reset run state", "clear last output", "clear run history"],
    "code_stats": ["show code stats", "code statistics", "summarize code numbers"],
    "code_nesting": ["show nesting depth", "how nested is this code", "check nesting"],
    "show_todos": ["show todos", "list todos", "find todo comments"],
    "show_requirements": [
        "show requirements", "list requirements", "what packages does this project need",
    ],
    "missing_project_files": ["check missing files", "check project imports", "find missing files"],
    "csv_preview": ["preview csv", "preview csv file", "read csv preview"],
    "import_policy": [
        "explain blocked import", "why is os blocked", "show safe imports", "what imports are allowed",
    ],
}


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def test_current_block_and_adjacent_symbols():
    block = tools.current_block(CODE, 5)
    assert block["line"] == 4 and block["end_line"] == 5
    assert "conditional block" in block["message"]
    assert tools.adjacent_symbol(CODE, 7, "function", "next")["line"] == 8
    assert tools.adjacent_symbol(CODE, 14, "function", "previous")["line"] == 8
    assert tools.adjacent_symbol(CODE, 12, "class", "previous")["line"] == 11
    assert tools.adjacent_symbol(CODE, 20, "class", "next")["found"] is False


def test_bracket_string_and_long_line_checks_ignore_safe_tokens():
    safe = "text = '(]}'  # } ] )\nvalues = [1, 2]\n"
    assert tools.check_brackets(safe) == "Brackets look balanced."
    assert "opening parenthesis on line 1" in tools.check_brackets("value = (1 + 2\n")
    assert "closing square bracket" in tools.check_brackets("value = 1]\n")
    assert tools.check_strings("text = 'closed'\n") == "Strings look closed."
    assert "unclosed string near line 1" in tools.check_strings("text = 'open\n")
    assert tools.check_long_lines("x = 1\n") == "No long lines found."
    assert "Line 2 is long at 101 characters" in tools.check_long_lines("x\n" + "a" * 101)


def test_cursor_line_edits_are_scoped_and_preserve_indentation():
    commented = tools.comment_line("if ready:\n    print('yes')\n", 2)
    assert commented["code"] == "if ready:\n    # print('yes')\n"
    uncommented = tools.uncomment_line(commented["code"], 2)
    assert uncommented["code"] == "if ready:\n    print('yes')\n"
    duplicated = tools.duplicate_line("first\nsecond\n", 2)
    assert duplicated["code"] == "first\nsecond\nsecond\n"
    blank = tools.comment_line("first\n\n", 2)
    assert blank["success"] is False and "blank" in blank["message"]


def test_delete_extra_blank_lines_keeps_one_separator():
    result = tools.delete_extra_blank_lines("first\n\n\n\nsecond\n\nthird\n")
    assert result["success"] is True
    assert result["code"] == "first\n\nsecond\n\nthird\n"
    assert result["message"] == "Removed 2 extra blank lines."


def test_code_stats_nesting_and_todos():
    stats = tools.code_stats(CODE)
    assert "2 functions" in stats and "1 class" in stats
    assert "1 import" in stats and "2 loops" not in stats
    assert tools.nesting_depth(CODE) == "Maximum nesting depth is 2."
    todos = tools.todo_comments(CODE + "# FIXME check score\n")
    assert "I found 2 notes" in todos
    assert "Line 16: TODO handle empty input" in todos
    assert "Line 17: FIXME check score" in todos


def test_project_requirements_missing_files_and_csv_preview():
    project = {
        "is_project": True,
        "files": {
            "main.py": "from helper import answer\n",
            "requirements.txt": "pandas>=2\nnumpy\n",
            "marks.csv": "name,score\nAman,92\nBea,88\nChen,95\nDana,80\n",
        },
        "requirements": ["pandas", "numpy"],
    }
    assert tools.requirements_summary(project) == "requirements.txt lists pandas, numpy."
    assert tools.missing_project_files(project) == "main.py imports helper.py, but helper.py is missing."
    preview = tools.csv_preview(project)
    # Preview-row disclosure was added in Pass 4 (never silently claim a
    # sampled preview is the exact full dataset) - this file has exactly
    # 4 data rows, all of which are shown.
    assert preview == "marks.csv has columns name, score. Previewing 4 rows. First row: Aman, 92."
    assert tools.import_policy_summary("os").startswith("os is blocked")
    assert "math, random" in tools.import_policy_summary()
    assert "protect the lesson sandbox" in tools.import_policy_summary(explain_blocked=True)


@pytest.mark.parametrize("text,intent", [
    (text, intent) for intent, phrases in COMMANDS.items() for text in phrases
])
def test_every_command_phrase_has_explicit_intent(text, intent):
    assert parse_intent(text)["intent"] == intent


def _route_payload(intent, text):
    code = CODE
    cursor_line = 5
    payload = {"text": text, "code": code, "cursor_line": cursor_line,
               "error": "SyntaxError on line 3"}
    if intent == "uncomment_line":
        payload.update(code="# print('hello')\n", cursor_line=1)
    elif intent == "delete_blank_lines":
        payload.update(code="first\n\n\nsecond\n", cursor_line=1)
    elif intent in {"show_requirements", "missing_project_files", "csv_preview"}:
        payload["project"] = {
            "files": {
                "main.py": "from helper import answer\n",
                "requirements.txt": "pandas\n",
                "marks.csv": "name,score\nAman,92\n",
            },
            "entry": "main.py",
            "requirements": ["pandas"],
        }
    return payload


@pytest.mark.parametrize("text,intent", [
    (text, intent) for intent, phrases in COMMANDS.items() for text in phrases
])
def test_every_phrase_routes_through_voice_command(client, text, intent):
    edit_intents = {"comment_line", "uncomment_line", "duplicate_line", "delete_blank_lines"}
    navigate_intents = {"current_block", "adjacent_symbol", "next_error"}
    expected_action = ("conversational_edit" if intent in edit_intents
                       else "navigate_code" if intent in navigate_intents
                       else "deterministic_message")
    data = client.post("/voice-command", json=_route_payload(intent, text)).get_json()
    assert data["action"] == expected_action


@pytest.mark.parametrize("text", [
    "read current block", "check brackets", "comment this line", "expect output 6",
    "show run history", "show code stats", "preview csv", "why is os blocked",
])
def test_new_tools_never_call_ai(client, monkeypatch, text):
    def fail_ai(*args, **kwargs):
        raise AssertionError("AI provider must not be called")

    monkeypatch.setattr(app_module, "call_gemini", fail_ai)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail_ai)
    intent = parse_intent(text)["intent"]
    data = client.post("/voice-command", json=_route_payload(intent, text)).get_json()
    assert data["action"] in {"deterministic_message", "navigate_code", "conversational_edit"}


def test_navigation_and_edit_routes_return_lines_and_safe_replace_code(client):
    block = client.post(
        "/voice-command", json={"text": "read current block", "code": CODE, "cursor_line": 5},
    ).get_json()
    symbol = client.post(
        "/voice-command", json={"text": "next function", "code": CODE, "cursor_line": 7},
    ).get_json()
    error = client.post(
        "/voice-command", json={"text": "jump to error", "code": CODE, "error": "Error on line 3"},
    ).get_json()
    edit = client.post(
        "/voice-command", json={"text": "comment current line", "code": "x = 1\nprint(x)\n", "cursor_line": 2},
    ).get_json()
    assert block["line"] == 4 and block["end_line"] == 5
    assert symbol["line"] == 8
    assert error["line"] == 3
    assert edit["ai_action"]["action"] == "replace_code"
    assert edit["ai_action"]["code"] == "x = 1\n# print(x)"


def test_expected_output_history_and_reset_use_existing_session_state(client):
    run = client.post("/run", json={"code": "print(6)\n"}).get_json()
    assert run["success"] is True
    match = client.post("/voice-command", json={"text": "expect output 6"}).get_json()
    history = client.post("/voice-command", json={"text": "show run history"}).get_json()
    assert match["speech"] == "Output matches expected value 6."
    assert "run code 1 time" in history["speech"] and "printed 6" in history["speech"]

    reset = client.post("/voice-command", json={"text": "reset run state"}).get_json()
    after = client.post("/voice-command", json={"text": "expect output 6"}).get_json()
    trace = client.post("/voice-command", json={"text": "next step"}).get_json()
    assert "Cleared last output" in reset["speech"]
    assert after["speech"] == "There is no previous output yet."
    assert "No execution trace" in trace["speech"]


def test_single_file_requirements_infer_known_third_party_package(client):
    data = client.post(
        "/voice-command", json={"text": "show requirements", "code": "import pandas\n"},
    ).get_json()
    assert data["speech"] == "This file appears to require pandas."
