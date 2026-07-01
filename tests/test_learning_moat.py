import pytest

from codeup.learning import learning_moat
from app import app
from codeup.commands.intent_parser import parse_intent


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


INDENT_CODE = "for i in range(3):\nprint(i)\n"
INDENT_ERROR = "Line 2: IndentationError: expected an indented block after 'for' statement"


def post_voice(client, text, **payload):
    body = {"text": text, **payload}
    return client.post("/voice-command", json=body).get_json()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("start tutor mode", "tutor_mode"),
        ("turn on tutor mode", "tutor_mode"),
        ("stop tutor mode", "tutor_mode"),
        ("tutor mode status", "tutor_mode"),
        ("give me a hint", "tutor_mode"),
        ("show fix", "tutor_mode"),
        ("make codex handoff", "codex_handoff"),
        ("check my understanding", "understanding_check"),
        ("quiz me on this code", "understanding_check"),
        ("give me a similar exercise", "understanding_check"),
        ("grade my attempt", "understanding_check"),
    ],
)
def test_learning_bridge_parser_routes_are_deterministic(text, expected):
    parsed = parse_intent(text)
    assert parsed["intent"] == expected
    assert parsed["confidence"] >= 0.9


def test_tutor_mode_start_stop_status_routes(client):
    start = post_voice(client, "start tutor mode")
    assert start["action"] == "deterministic_message"
    assert start["intent"] == "tutor_on"
    assert start["tutor_mode"] is True
    assert "hints and explanations before fixes" in start["message"]

    status = post_voice(client, "tutor mode status")
    assert status["intent"] == "tutor_status"
    assert "on" in status["message"].lower()

    stop = post_voice(client, "stop tutor mode")
    assert stop["intent"] == "tutor_off"
    assert stop["tutor_mode"] is False


def test_tutor_hint_for_indentation_error_does_not_auto_fix(client):
    data = post_voice(client, "give me a hint", code=INDENT_CODE, error=INDENT_ERROR)
    assert data["action"] == "deterministic_message"
    assert data["intent"] == "tutor_hint"
    assert "indented block" in data["message"].lower()
    assert "show fix" in data["message"].lower()
    assert "ai_action" not in data
    assert "print(i)" not in data.get("ai_action", {}).get("code", "")


def test_show_fix_proposes_but_does_not_apply(client):
    data = post_voice(client, "show fix", code=INDENT_CODE, error=INDENT_ERROR)
    assert data["action"] == "deterministic_message"
    assert data["intent"] == "tutor_show_fix"
    assert data["proposed_fix"] is True
    assert "Proposed change" in data["message"]
    assert "Say apply" in data["message"]
    assert "ai_action" not in data


def test_tutor_missing_context_gives_safe_next_step(client):
    data = post_voice(client, "give me a hint")
    assert data["intent"] == "tutor_hint"
    assert "write or paste" in data["message"].lower()
    assert data.get("needs_clarification") is not True


def test_tutor_mode_intercepts_bare_fix_without_ai_apply(client):
    post_voice(client, "start tutor mode")
    data = post_voice(client, "fix this code", code=INDENT_CODE, error=INDENT_ERROR)
    assert data["intent"] == "tutor_hint"
    assert "will not change the code" in data["message"].lower()
    assert "ai_action" not in data


def test_handoff_command_routes_with_short_speech(client):
    data = post_voice(client, "make codex handoff", code="print('hi')\n")
    assert data["action"] == "deterministic_message"
    assert data["intent"] == "handoff"
    assert data["codex_handoff"] is True
    assert "# CodeUp Handoff Pack" in data["message"]
    assert "## Current code / project" in data["message"]
    assert len(data["speech"]) < 180
    assert data.get("needs_clarification") is not True


def test_handoff_pack_includes_sections_redacts_and_summarizes_large_code():
    large_code = "API_KEY = 'sk-secretsecret'\n" + "\n".join(f"print({i})" for i in range(80))
    mem = {
        "latest_user_request": "practice loops",
        "last_run_error": INDENT_ERROR,
        "run_count": 2,
        "command_count": 5,
        "change_history": [{"before": "print(1)", "after": "print(2)", "file": "main.py"}],
        "watched_variables": ["i"],
        "last_state_trace": {"vars": {"i": {"value": "2"}}, "loop": "Loop i ended at 2."},
    }
    pack = learning_moat.build_handoff_pack(mem, large_code, project_state={"is_project": False, "code": large_code})
    text = pack["message"]
    for heading in (
        "## What I am trying to do",
        "## Current code / project",
        "## Project structure",
        "## Current error",
        "## What changed recently",
        "## Current program state",
        "## What I already tried",
        "## Questions to ask Codex",
    ):
        assert heading in text
    assert "Large code file recorded" in text
    assert "sk-secretsecret" not in text
    assert "[redacted" in text
    assert "Loop i ended at 2" in text


def test_handoff_missing_data_is_honest():
    pack = learning_moat.build_handoff_pack({}, "")
    text = pack["message"]
    assert "not recorded yet" in text
    assert "No current code was recorded yet" in text
    assert "No recent Python error is recorded" in text


def test_understanding_loop_code_produces_loop_question(client):
    data = post_voice(client, "check my understanding", code="for i in range(3):\n    print(i)\n")
    assert data["intent"] == "understanding_question"
    assert "last loop run" in data["message"].lower()
    assert data.get("needs_clarification") is not True


def test_understanding_indentation_error_produces_error_question(client):
    data = post_voice(client, "quiz me on this code", code=INDENT_CODE, error=INDENT_ERROR)
    assert data["intent"] == "understanding_question"
    assert "why does python need spaces" in data["message"].lower()


def test_similar_exercise_and_grade_missing_info(client):
    exercise = post_voice(client, "give me a similar exercise", code="for i in range(3):\n    print(i)\n")
    assert exercise["intent"] == "understanding_practice"
    assert exercise["message"].startswith("Practice:")
    assert len(exercise["message"]) < 180

    grade = post_voice(client, "grade my attempt")
    assert grade["intent"] == "understanding_grade"
    assert "do not have an answer to grade" in grade["message"].lower()


def test_learning_commands_respect_audio_blocks_boundary(client):
    data = post_voice(
        client,
        "check my understanding",
        code="for i in range(3):\n    print(i)\n",
        active_mode="audio_blocks",
    )
    assert data["action"] == "deterministic_message"
    assert "Audio Blocks Mode" in data["message"]
    assert "switch to Python Code Mode" in data["message"]
