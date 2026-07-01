"""Project Map: deterministic, non-visual map of single-file and multi-file work.

Covers the module logic (AST analysis, dependency detection, entry-point
detection, readable narration) and the /voice-command routing that exposes it.
"""

import pytest

from codeup.projects import project_map
from app import app
from codeup.commands.intent_parser import parse_intent


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


QUIZ_PROJECT = {
    "is_project": True,
    "entry": "main.py",
    "files": {
        "main.py": (
            "import questions\n"
            "import score\n\n"
            "def run():\n"
            "    for q in questions.QUESTIONS:\n"
            "        print(q)\n\n"
            "if __name__ == \"__main__\":\n"
            "    run()\n"
        ),
        "questions.py": "QUESTIONS = [\"q1\", \"q2\"]\n",
        "score.py": "def calculate(a):\n    return a\n\ndef save(s):\n    print(s)\n",
    },
}


def voice(client, text, **payload):
    payload.setdefault("source", "typed")
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


# ---- module logic -------------------------------------------------------

def test_single_file_map_is_readable():
    state = {"is_project": False, "code": "total = 0\nfor i in range(3):\n    total += 1\nprint(total)\n"}
    speech = project_map.narrate(state)
    assert "single file" in speech
    assert "1 loop" in speech
    assert "starts at line" in speech


def test_single_file_lists_functions():
    state = {"is_project": False, "code": "def ask():\n    pass\n\ndef main():\n    ask()\n\nmain()\n"}
    speech = project_map.narrate(state)
    assert "2 functions: ask, main" in speech


def test_multi_file_map_counts_files_and_roles():
    speech = project_map.narrate(QUIZ_PROJECT)
    assert "There are 3 files." in speech
    assert "main.py starts the program." in speech
    assert "questions.py defines 1 variable: QUESTIONS." in speech
    assert "score.py defines 2 functions: calculate, save." in speech


def test_import_dependency_detection():
    speech = project_map.narrate(QUIZ_PROJECT)
    assert "main.py imports questions and score." in speech
    data = project_map.build_map(QUIZ_PROJECT)
    by_name = {f["name"]: f for f in data["files"]}
    assert by_name["main.py"]["project_imports"] == ["questions", "score"]
    assert by_name["score.py"]["project_imports"] == []


def test_function_list_per_file():
    data = project_map.build_map(QUIZ_PROJECT)
    by_name = {f["name"]: f for f in data["files"]}
    assert by_name["score.py"]["functions"] == ["calculate", "save"]
    assert by_name["main.py"]["functions"] == ["run"]


def test_entry_point_detection_prefers_main_guard():
    data = project_map.build_map(QUIZ_PROJECT)
    assert data["entry_file"] == "main.py"
    assert data["entry_line"] == 8  # the `if __name__ == "__main__":` line
    assert "The program starts in main.py, line 8." in project_map.narrate(QUIZ_PROJECT)


def test_entry_point_falls_back_to_main_py_without_guard():
    project = {
        "is_project": True,
        "files": {
            "main.py": "import helper\nhelper.go()\n",
            "helper.py": "def go():\n    print('hi')\n",
        },
    }
    data = project_map.build_map(project)
    assert data["entry_file"] == "main.py"
    assert data["entry_line"] == 2  # first executable line after the import


def test_find_file_by_main_function():
    assert project_map.find_file_for(QUIZ_PROJECT, "open the file with the main function") == "main.py"


def test_find_file_by_keyword():
    assert project_map.find_file_for(QUIZ_PROJECT, "open the file that handles score") == "score.py"


def test_syntax_error_file_is_graceful_not_crashing():
    project = {
        "is_project": True,
        "files": {"main.py": "def broken(\n", "ok.py": "def fine():\n    return 1\n"},
    }
    speech = project_map.narrate(project)
    assert "main.py has a syntax error" in speech
    assert "ok.py defines 1 function: fine." in speech


def test_empty_single_file_is_handled():
    speech = project_map.narrate({"is_project": False, "code": ""})
    assert "no code yet" in speech.lower()


def test_no_local_imports_is_stated():
    project = {
        "is_project": True,
        "files": {"main.py": "print('hi')\n", "notes.py": "x = 1\n"},
    }
    speech = project_map.narrate(project)
    assert "No file imports another project file." in speech


# ---- voice-command routing ---------------------------------------------

def test_project_map_intent_is_registered():
    for command in (
        "project map",
        "give me a project map",
        "summarize project",
        "where does the program start",
        "what functions are in this project",
        "what imports what",
    ):
        assert parse_intent(command)["intent"] == "project_map", command


def test_project_map_command_routes_with_multi_file_project(client):
    result = voice(client, "project map", project={
        "files": QUIZ_PROJECT["files"], "entry": "main.py"})
    assert result["action"] == "deterministic_message"
    assert "Project map:" in result["speech"]
    assert "There are 3 files." in result["speech"]
    assert "main.py imports questions and score." in result["speech"]


def test_where_does_program_start_routes_for_single_file(client):
    result = voice(client, "where does the program start",
                   code="def main():\n    print('hi')\n\nmain()\n")
    assert result["action"] == "deterministic_message"
    assert "single file" in result["speech"]


def test_what_functions_in_project_routes(client):
    result = voice(client, "what functions are in this project", project={
        "files": QUIZ_PROJECT["files"], "entry": "main.py"})
    assert result["action"] == "deterministic_message"
    assert "score.py defines 2 functions: calculate, save." in result["speech"]


def test_project_map_does_not_call_ai_provider(client, monkeypatch):
    import app as app_module

    def fail(*args, **kwargs):
        raise AssertionError("AI provider was called for the deterministic project map")

    monkeypatch.setattr(app_module, "call_gemini", fail)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail)
    result = voice(client, "project map", code="print('hi')\n")
    assert result["success"] is not False
    assert "Project map:" in result["speech"]
