"""Non-visual navigation upgrades (slice 7).

Fills gaps around the existing navigation (go-to-line, read function, next/prev
function, file outline, breadcrumbs, next error/change). Covers the new commands
(what file am I in, read comments, jump to changed line, go to main function,
open file by role, what does this file do), the richer breadcrumb context, and
Audio-Blocks-mode safety. Reuses existing modules; deterministic; no AI.
"""

import pytest

from codeup.projects import structure_tools
from app import app
from codeup.commands.intent_parser import parse_intent


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def vc(client, text, **payload):
    payload.setdefault("source", "typed")
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


CODE = ("import math\n\n# square helper\ndef square(x):\n    return x * x\n\n"
        "def main():\n    for i in range(3):\n        print(square(i))\n\nmain()\n")
PROJECT = {"files": {
    "main.py": "import score\nif __name__ == \"__main__\":\n    print(score.calc())\n",
    "score.py": "def calc():\n    return 1\n"}, "entry": "main.py"}


# ---- module: comments --------------------------------------------------

def test_collect_comments():
    comments = structure_tools.collect_comments(CODE)
    assert any("square helper" in c["text"] for c in comments)
    assert comments[0]["line"] == 3


def test_collect_comments_broken_code_fallback():
    # tokenize fails on this; the regex fallback still finds the comment.
    comments = structure_tools.collect_comments("def f(\n    # oops\n")
    assert any("oops" in c["text"] for c in comments)


def test_read_comments_speech_empty_and_present():
    assert "no comments" in structure_tools.read_comments_speech("x = 1\n").lower()
    speech = structure_tools.read_comments_speech(CODE)
    assert "1 comment" in speech and "square helper" in speech


# ---- intents registered ------------------------------------------------

def test_new_nav_intents_registered():
    assert parse_intent("what file am i in")["intent"] == "nav_what_file"
    assert parse_intent("read comments")["intent"] == "nav_read_comments"
    assert parse_intent("jump to changed line")["intent"] == "nav_changed_line"
    assert parse_intent("go to main function")["intent"] == "nav_go_main"
    assert parse_intent("open file with main function")["intent"] == "nav_open_file"
    assert parse_intent("what does this file do")["intent"] == "nav_what_file_does"


def test_existing_nav_routing_preserved():
    # Literal file open and go-to-line keep their existing intents.
    assert parse_intent("open main.py")["intent"] == "open_project_file"
    assert parse_intent("go to line 5")["intent"] == "goto_line"
    assert parse_intent("next function")["intent"] == "adjacent_symbol"


# ---- routing: new commands ---------------------------------------------

def test_what_file_am_i_in_single_and_project(client):
    assert "single editor file" in vc(client, "what file am i in", code=CODE)["speech"]
    data = vc(client, "what file am i in", project=PROJECT, file="score.py")
    assert "score.py" in data["speech"]


def test_read_comments_command(client):
    data = vc(client, "read comments", code=CODE)
    assert data["action"] == "deterministic_message"
    assert "square helper" in data["speech"]


def test_go_to_main_function(client):
    data = vc(client, "go to main function", code=CODE)
    assert data["action"] == "navigate_code"
    assert data["line"] == 7


def test_go_to_main_function_missing(client):
    data = vc(client, "go to main function", code="x = 1\n")
    assert data["action"] == "deterministic_message"
    assert "could not find" in data["speech"].lower()


def test_what_does_this_file_do(client):
    data = vc(client, "what does this file do", code=CODE)
    assert data["action"] == "deterministic_message"
    assert "square" in data["speech"] and "main" in data["speech"]


def test_open_file_by_role(client):
    assert "main.py" in vc(client, "open file with main function", project=PROJECT)["speech"]
    assert "score.py" in vc(client, "open the file that handles score", project=PROJECT)["speech"]


def test_open_file_by_role_no_match(client):
    data = vc(client, "open the file that handles nonsense", project=PROJECT)
    assert data["action"] == "deterministic_message"


def test_jump_to_changed_line(client):
    client.post("/run", json={"code": "for i in range(3):\nprint(i)\n"})
    vc(client, "fix with explanation", code="for i in range(3):\nprint(i)\n")
    vc(client, "apply", code="for i in range(3):\nprint(i)\n")
    data = vc(client, "jump to changed line")
    assert data["action"] == "navigate_code"
    assert data["line"] == 2
    assert "indented" in data["speech"].lower()


def test_jump_to_changed_line_no_changes(client):
    data = vc(client, "jump to changed line")
    assert data["action"] == "deterministic_message"
    assert "no code changes" in data["speech"].lower()


# ---- richer "where am I" breadcrumb ------------------------------------

def test_breadcrumb_gives_function_context(client):
    d = client.post("/breadcrumbs", json={"code": CODE, "line": 8}).get_json()
    assert d["success"] is True
    assert "main" in d["breadcrumb"]
    assert "for loop" in d["breadcrumb"]


def test_breadcrumb_includes_error_context(client):
    client.post("/run", json={"code": "for i in range(3):\nprint(i)\n"})  # stores the error
    # Breadcrumb runs on parseable code; the recent error shows in the context.
    d = client.post("/breadcrumbs", json={"code": "for i in range(3):\n    print(i)\n", "line": 1}).get_json()
    assert "latest error is on line" in d.get("context", "")


def test_where_am_i_action_preserved(client):
    # The frontend position command keeps its action contract.
    assert vc(client, "where am i")["action"] == "where_am_i"


# ---- Audio Blocks mode safety ------------------------------------------

@pytest.mark.parametrize("cmd", ["read comments", "go to main function", "jump to changed line"])
def test_python_nav_in_audio_blocks_is_safe(client, cmd):
    data = vc(client, cmd, code=CODE, active_mode="audio_blocks")
    assert data["action"] == "deterministic_message"   # never an edit
    assert "ai_action" not in data                       # never mutates a workspace


# ---- robustness --------------------------------------------------------

def test_missing_data_does_not_crash(client):
    for cmd in ["what file am i in", "read comments", "what does this file do",
                "go to main function", "jump to changed line"]:
        assert vc(client, cmd)["success"] is not False


def test_navigation_does_not_call_ai(client, monkeypatch):
    import app as app_module

    def fail(*args, **kwargs):
        raise AssertionError("AI provider called for deterministic navigation")

    monkeypatch.setattr(app_module, "call_gemini", fail)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail)
    assert vc(client, "read comments", code=CODE)["success"] is not False
    assert vc(client, "go to main function", code=CODE)["action"] == "navigate_code"
