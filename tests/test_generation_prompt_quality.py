import json

import pytest

import app as app_module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.delenv("CODEUP_AI_ENABLED", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as flask_client:
        yield flask_client


def test_beginner_generation_prompt_and_response_are_safe(client, monkeypatch):
    captured = {}

    def fake_call(system, user, **kwargs):
        captured["system"] = system
        captured["user"] = user
        return (
            "score = 0\n"
            "questions = ['2 + 2']\n"
            "answers = ['4']\n"
            "student_answer = '4'\n"
            "if student_answer == answers[0]:\n"
            "    score += 1\n"
            "print('Score:', score)\n"
        )

    monkeypatch.setattr(app_module, "call_gemini", fake_call)

    data = client.post("/generate-code", json={"prompt": "make a quiz game"}).get_json()

    assert data["success"] is True
    compile(data["code"], "<generated>", "exec")
    assert "make a quiz game" not in data["code"]
    assert "explanation" in data and "runnable in CodeUp" in data["explanation"]
    system = captured["system"].lower()
    assert "complete, runnable python" in system
    assert "network/file operations" in system
    assert "numpy or pandas only if the user explicitly asks" in system


def test_unsafe_generated_code_is_rejected(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "call_gemini",
        lambda *a, **k: "import os\nprint(os.getcwd())\n",
    )

    data = client.post("/generate-code", json={"prompt": "show the current folder"}).get_json()

    assert data["success"] is False
    assert "blocked" in data["error"].lower() or "unsupported" in data["error"].lower()
    assert "os.getcwd" not in data.get("code", "")


def test_multifile_generation_respects_main_entry(client, monkeypatch):
    project = {
        "name": "Tiny Greeting",
        "entry": "main.py",
        "active_file": "main.py",
        "requirements": [],
        "speech": "Tiny Greeting has main.py and helper.py. Run main.py.",
        "files": {
            "main.py": "from helper import greeting\n\nprint(greeting())\n",
            "helper.py": "def greeting():\n    return 'Hello CodeUp'\n",
            "requirements.txt": "",
        },
    }

    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: json.dumps(project))

    data = client.post(
        "/generate-code",
        json={"prompt": "create a tiny greeting project split into multiple files"},
    ).get_json()

    assert data["success"] is True
    assert data["project"] is True
    assert data["entry"] == "main.py"
    assert "main.py" in data["files"]
    assert data["requirements"] == []
    assert data["speech"]
