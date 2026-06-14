import pytest

import app as app_module
import ask_code


LOOP = "for i in range(3):\nprint(i)\n"
FIXED_LOOP = "for i in range(3):\n    print(i)\n"
INDENT_ERR = "IndentationError: expected an indented block on line 2"

MARKS_PROGRAM = (
    "marks = [80, 45, 67]\n"
    "total = 0\n"
    "for mark in marks:\n"
    "    total += mark\n"
    "    if mark >= 50:\n"
    "        print('pass')\n"
    "    else:\n"
    "        print('practice')\n"
    "average = total / len(marks)\n"
    "print(average)\n"
)


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


def _spoken(data):
    return (
        data.get("speech")
        or data.get("message")
        or (data.get("ai_action") or {}).get("spoken_confirmation")
        or ""
    ).strip()


@pytest.mark.parametrize(
    "text",
    [
        "analyze",
        "analyse",
        "analyze this",
        "analyze this code",
        "analyze my code",
        "explain this code",
        "explain my code",
        "teach me this code",
        "teach me this scored",
        "teach me this court",
        "teach me this cod",
        "teach me scored",
        "teach this code",
    ],
)
def test_demo_explanation_aliases_route_to_analyze(client, text):
    data = _vc(client, text, code=FIXED_LOOP)
    assert data["action"] == "analyze"
    assert data.get("concept_lesson") is not True


def test_teach_me_this_scored_matches_analyze_route(client):
    assert _vc(client, "analyze", code=FIXED_LOOP)["action"] == "analyze"
    assert _vc(client, "teach me this scored", code=FIXED_LOOP)["action"] == "analyze"


@pytest.mark.parametrize(
    "text",
    [
        "what line control the loop",
        "which line control the loop",
        "where does the loop start",
    ],
)
def test_loop_control_grammar_finds_line_one_in_broken_demo_loop(client, text):
    data = _vc(client, text, code=LOOP)
    assert data["action"] == "navigate_code"
    assert data.get("ask_my_code") is True
    assert data["line"] == 1
    assert "Line 1 controls or starts the loop." in data["speech"]


def test_ask_code_loop_control_source_fallback_handles_bad_indentation():
    result = ask_code.answer_code_question("what line control the loop", LOOP)
    assert result["action"] == "navigate_code"
    assert result["line"] == 1
    assert "Line 1 controls or starts the loop." in result["message"]


def test_debug_this_like_a_teacher_repairs_indentation_without_clarifying(client):
    data = _vc(client, "debug this like a teacher", code=LOOP, error=INDENT_ERR)
    assert data["action"] == "conversational_edit"
    assert data["ai_action"]["action"] == "indent_line"
    assert data["ai_action"]["target"]["line_number"] == 2
    assert app_module._indent_line_in_code(LOOP, 2) == FIXED_LOOP
    speech = _spoken(data)
    assert "what do you want to do" not in speech.lower()
    assert "The print line needed to be indented inside the loop." in speech


def test_main_help_prioritizes_demo_commands_without_bookmarks(client):
    data = _vc(client, "what can I do here")
    assert data["action"] == "deterministic_message"
    speech = _spoken(data).lower()
    for expected in (
        "generate code",
        "run code",
        "read output",
        "analyze",
        "explain this code",
        "fix this code",
        "replay mistake",
        "summarize structure",
        "make project report",
        "stop everything",
    ):
        assert expected in speech
    assert "bookmark this loop" not in speech
    assert "list bookmarks" not in speech


@pytest.mark.parametrize(
    "text",
    [
        "explain how the loop, condition, and average calculation work together",
        "explain how loop condition average work together",
        "explain this program conceptually",
    ],
)
def test_conceptual_marks_aliases_explain_loop_condition_and_average(client, text):
    data = _vc(client, text, code=MARKS_PROGRAM)
    assert data["action"] in {"deterministic_message", "navigate_code"}
    assert data.get("ask_my_code") is True
    speech = _spoken(data).lower()
    assert "loop processes each student" in speech
    assert "condition checks pass/practice" in speech
    assert "average summarizes the class" in speech
