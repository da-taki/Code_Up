import pytest

import app as app_module
import deterministic_code_tools as tools
from app import app
from intent_parser import parse_intent


COMMAND_PHRASES = {
    "preflight_check": ["check my code before running", "preflight check", "will this run"],
    "check_indentation": ["explain indentation", "check indentation", "where is indentation wrong"],
    "list_functions": ["list functions", "what functions are here", "show functions"],
    "list_imports": ["list imports", "what imports am I using", "check imports"],
    "sandbox_check": ["find risky code", "check for unsafe code", "sandbox check"],
    "repeat_last_output": ["read last output", "repeat output", "what did it print"],
    "repeat_last_error": ["read last error", "repeat error", "what was the error"],
    "project_health": ["check project", "project health check", "is my project ready"],
    "project_file_tree": ["read file tree", "summarize project files", "what files are in this project"],
    "loop_summary": ["explain loops", "how many times does this loop run", "check loops"],
}


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def test_preflight_checks_important_issues_first():
    assert "empty" in tools.preflight_check("").lower()
    assert "not indented" in tools.preflight_check("for i in range(3):\nprint(i)").lower()
    assert "os" in tools.preflight_check("import os\n")
    assert "eval" in tools.preflight_check("eval('1')\n")
    assert "input" in tools.preflight_check("name = input()\n")
    assert "no break" in tools.preflight_check("while True:\n    print('x')\n")
    assert tools.preflight_check("print('ok')\n") == "The code looks ready to run."


def test_indentation_functions_imports_safety_and_loops():
    assert "Line 1 starts a loop" in tools.indentation_check("for i in range(3):\n    print(i)\n")
    assert "calculate_total on line 1" in tools.list_functions("def calculate_total():\n    return 1\n")
    assert "math" in tools.import_summary("import math\nimport os\n")
    assert "os is blocked" in tools.import_summary("import math\nimport os\n")
    assert "open" in tools.sandbox_safety_check("open('x.txt')\n")
    assert "filesystem" in tools.sandbox_safety_check("Path('x').write_text('x')\n")
    assert tools.loop_summary("for i in range(1, 5):\n    pass\n") == "The loop on line 1 runs 4 times."
    assert "exact count is not known" in tools.loop_summary("for i in range(limit):\n    pass\n")
    assert "condition stays true" in tools.loop_summary("while ready:\n    break\n")


def test_project_health_and_file_tree():
    project = {
        "is_project": True, "entry": "main.py",
        "files": {"main.py": "from utils import answer\n", "utils.py": "answer = 42\n"},
    }
    assert tools.project_health_check(project).startswith("The project looks ready")
    assert "2 files" in tools.project_file_tree(project)
    broken = {"is_project": True, "entry": "missing.py", "files": project["files"]}
    assert "does not exist" in tools.project_health_check(broken)
    third_party = {"is_project": True, "entry": "main.py", "files": {"main.py": "import numpy\n"}}
    assert "requirements.txt is missing" in tools.project_health_check(third_party)


@pytest.mark.parametrize("text,intent", [
    (text, intent)
    for intent, phrases in COMMAND_PHRASES.items()
    for text in phrases
])
def test_intent_phrases(text, intent):
    assert parse_intent(text)["intent"] == intent


@pytest.mark.parametrize("text", [text for phrases in COMMAND_PHRASES.values() for text in phrases])
def test_every_command_phrase_routes_through_voice_pipeline(client, text):
    project = {"files": {"main.py": "print('ok')\n"}, "entry": "main.py"}
    data = client.post(
        "/voice-command",
        json={"text": text, "code": "for i in range(3):\n    print(i)\n", "project": project},
    ).get_json()
    assert data["action"] == "deterministic_message"


@pytest.mark.parametrize("text,code,fragment", [
    ("preflight check", "print('ok')", "ready to run"),
    ("check indentation", "for i in range(2):\n    print(i)", "indented inside"),
    ("show functions", "def greet():\n    pass", "greet on line 1"),
    ("list imports", "import math", "imports math"),
    ("find risky code", "eval('1')", "eval"),
    ("explain loops", "for i in range(3):\n    pass", "runs 3 times"),
])
def test_voice_routes_are_deterministic(client, monkeypatch, text, code, fragment):
    def fail_ai(*args, **kwargs):
        raise AssertionError("AI provider must not be called")

    monkeypatch.setattr(app_module, "call_gemini", fail_ai)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail_ai)
    data = client.post("/voice-command", json={"text": text, "code": code}).get_json()
    assert data["action"] == "deterministic_message"
    assert fragment in data["speech"]


def test_repeat_output_error_and_project_routes(client):
    assert client.post("/voice-command", json={"text": "read last output"}).get_json()["speech"] == "There is no previous output yet."
    assert client.post("/voice-command", json={"text": "read last error"}).get_json()["speech"] == "There is no previous error yet."
    assert "No multi-file project" in client.post("/voice-command", json={"text": "check project"}).get_json()["speech"]
    project = {"files": {"main.py": "print('ok')\n", "utils.py": "VALUE = 1\n"}, "entry": "main.py"}
    health = client.post("/voice-command", json={"text": "is my project ready", "project": project}).get_json()
    tree = client.post("/voice-command", json={"text": "what files are in this project", "project": project}).get_json()
    assert health["action"] == "deterministic_message" and "looks ready" in health["speech"]
    assert tree["action"] == "deterministic_message" and "2 files" in tree["speech"]


def test_repeat_output_and_error_use_stored_run_history(client):
    success = client.post("/run", json={"code": "print('saved output')\n"}).get_json()
    assert success["success"] is True
    output = client.post("/voice-command", json={"text": "what did it print"}).get_json()
    assert output["speech"] == "saved output"

    failed = client.post("/run", json={"code": "print(missing_name)\n"}).get_json()
    assert failed["success"] is False
    error = client.post("/voice-command", json={"text": "what was the error"}).get_json()
    assert error["speech"] != "There is no previous error yet."
    assert "line 1" in error["speech"].lower()
