from pathlib import Path

import pytest

import literacy_mode
from app import app
from intent_parser import parse_intent


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def post_voice(client, text, **payload):
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


@pytest.mark.parametrize(
    "text",
    [
        "start literacy mode",
        "start programming literacy mode",
        "start lesson",
        "start first lesson",
        "list lessons",
        "show lessons",
        "start lesson 1",
        "start loops lesson",
        "next lesson",
        "previous lesson",
        "lesson status",
        "what am I learning",
        "what should I do next",
        "give me lesson starter code",
        "practice the mistake",
        "check lesson understanding",
        "complete lesson",
        "teacher lesson report",
        "graduation report",
        "am I ready for codex",
        "am I ready for VS Code",
    ],
)
def test_programming_literacy_commands_parse_deterministically(text):
    parsed = parse_intent(text)
    assert parsed["intent"] == "programming_literacy"
    assert parsed["confidence"] >= 0.9


def test_list_lessons_returns_six_missions(client):
    data = post_voice(client, "list lessons")
    assert data["action"] == "deterministic_message"
    assert data["intent"] == "programming_literacy"
    assert data["literacy_command"] == "list_lessons"
    assert data["message"].count("\n") >= 6
    for title in ("Print and output", "Variables and values", "If statements", "For loops", "Lists", "Functions"):
        assert title in data["message"]
    assert data.get("needs_clarification") is not True


def test_start_literacy_mode_routes_without_ai(client):
    data = post_voice(client, "start literacy mode")
    assert data["literacy_command"] == "start_mode"
    assert "Programming Literacy Mode is on" in data["message"]
    assert "ai_action" not in data


def test_start_lesson_1_returns_starter_code_safely(client):
    data = post_voice(client, "start lesson 1", code="print('existing')\n")
    assert data["literacy_command"] == "start_lesson"
    assert data["lesson_id"] == "print"
    assert 'print("Hello, CodeUp")' in data["message"]
    assert data["starter_code"].strip() == 'print("Hello, CodeUp")'
    assert "did not overwrite your editor" in data["message"]
    assert "ai_action" not in data


def test_start_loops_lesson_selects_loop_mission(client):
    data = post_voice(client, "start loops lesson")
    assert data["lesson_id"] == "loops"
    assert "For loops" in data["message"]
    assert "for i in range(3)" in data["starter_code"]


def test_current_concept_and_next_command(client):
    post_voice(client, "start loops lesson")
    learning = post_voice(client, "what am I learning")
    assert "for loops" in learning["message"].lower()
    assert "repeats" in learning["message"].lower()

    next_step = post_voice(client, "what should I do next")
    assert "run the program" in next_step["message"].lower()
    assert "show program state" in next_step["message"].lower()


def test_practice_mistake_and_understanding_are_lesson_specific(client):
    post_voice(client, "start loops lesson")
    mistake = post_voice(client, "practice the mistake")
    assert "remove the spaces before print(i)" in mistake["message"].lower()

    check = post_voice(client, "check lesson understanding", code="for i in range(3):\n    print(i)\n")
    assert "Lesson check:" in check["message"]
    assert "last loop run" in check["message"].lower()


def test_complete_lesson_marks_complete_and_next_lesson(client):
    post_voice(client, "start loops lesson")
    complete = post_voice(client, "complete lesson")
    assert "Lesson complete: For loops" in complete["message"]
    assert "Next lesson: Lists" in complete["message"]

    status = post_voice(client, "lesson status")
    assert "Completed 1 of 6" in status["message"]


def test_reports_and_readiness_are_honest(client):
    post_voice(client, "start loops lesson")
    post_voice(client, "practice the mistake")
    post_voice(client, "check lesson understanding", code="for i in range(3):\n    print(i)\n")
    report = post_voice(client, "teacher lesson report")
    assert "# CodeUp Teacher Lesson Report" in report["message"]
    assert "Lesson attempted: For loops" in report["message"]
    assert "Understanding checks:" in report["message"]
    assert "Fixes applied:" in report["message"]

    graduation = post_voice(client, "graduation report")
    assert "Graduation Readiness" in graduation["message"]
    assert "does not claim full independence" in graduation["message"]

    codex = post_voice(client, "am I ready for Codex")
    assert "not fully yet" in codex["message"].lower()
    assert "large projects" not in codex["message"].lower() or "not expected" in codex["message"].lower()

    vscode = post_voice(client, "am I ready for VS Code")
    assert "not fully yet" in vscode["message"].lower()
    assert "before vs code" in vscode["message"].lower()


def test_missing_lesson_context_does_not_crash(client):
    data = post_voice(client, "what am I learning")
    assert data["action"] == "deterministic_message"
    assert "No literacy lesson is active" in data["message"]


def test_audio_blocks_boundary_for_python_lesson_commands(client):
    data = post_voice(client, "start loops lesson", active_mode="audio_blocks")
    assert data["action"] == "deterministic_message"
    assert "Audio Blocks Mode" in data["message"]
    assert "Switch to Python Code Mode" in data["message"]

    neutral = post_voice(client, "list lessons", active_mode="audio_blocks")
    assert "Programming Literacy Mode lessons" in neutral["message"]


def test_literacy_module_list_works_without_ai():
    assert len(literacy_mode.LESSONS) == 6
    result = literacy_mode.handle_command({"kind": "list_lessons"}, {})
    assert "Programming Literacy Mode lessons" in result["message"]
    assert "Functions" in result["message"]


def test_readme_mentions_programming_literacy_mode_honestly():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "## Modes" in readme
    assert "Programming Literacy Mode" in readme
    assert "learning missions" in readme
    assert "replace professional coding agents" in readme
    assert "validated with blind users" not in readme.lower()
