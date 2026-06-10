import os

import pytest

import app as app_module


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
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _action(client, text, code="print('keep me')\n"):
    return client.post("/voice-command", json={"text": text, "code": code}).get_json()


def test_startup_guidance_uses_spoken_commands_not_tab_only():
    src = _read("templates/index.html")
    assert 'Say "start tutorial" to begin the guided tutorial' in src
    assert 'say "what can I do here" to hear example commands' in src
    assert "Press Tab to reach the Tutorial button" not in src


@pytest.mark.parametrize("text", [
    "what can I do here",
    "what can I do",
    "how do I use this",
])
def test_first_level_onboarding_returns_short_guidance_without_cloud_ai(client, monkeypatch, text):
    # First-level onboarding phrases now return a short beginner-friendly guide
    # (deterministic), not the old long command dump, and must not call cloud AI.
    def fail_call(*args, **kwargs):
        raise AssertionError("onboarding help must not call cloud AI")

    monkeypatch.setattr(app_module, "call_gemini", fail_call)
    data = _action(client, text)
    assert data["action"] == "deterministic_message"
    assert data["onboarding"] is True
    msg = data["message"].lower()
    for snippet in ("speaking or typing", "generate code", "run code",
                    "explain it", "start tutorial", "more examples"):
        assert snippet in msg, (text, snippet, msg)


@pytest.mark.parametrize("text", [
    "help",
    "show commands",
    "what commands can I try",
    "what should I say",
    "guide me",
])
def test_help_phrases_route_to_help_without_cloud_ai(client, monkeypatch, text):
    # Explicit help requests still route to the command help, without cloud AI.
    def fail_call(*args, **kwargs):
        raise AssertionError("help must not call cloud AI")

    monkeypatch.setattr(app_module, "call_gemini", fail_call)
    data = _action(client, text)
    assert data["action"] == "help"


@pytest.mark.parametrize("text", [
    "more examples",
    "show all commands",
    "full help",
    "command list",
])
def test_long_help_stays_behind_explicit_request_without_cloud_ai(client, monkeypatch, text):
    # The long command list stays behind an explicit "more/full" request, never
    # the first onboarding reply, and must not call cloud AI.
    def fail_call(*args, **kwargs):
        raise AssertionError("help must not call cloud AI")

    monkeypatch.setattr(app_module, "call_gemini", fail_call)
    data = _action(client, text)
    assert data["action"] == "more_help"


def test_visible_and_spoken_help_are_beginner_guides():
    src = _read("static/app.js")
    assert "You can type or speak natural commands." in src
    assert "put a loop from zero to two that prints each number in the editor" in src
    assert "create a quiz game split into multiple files" in src


def test_help_action_does_not_modify_editor_contents():
    src = _read("static/app.js")
    start = src.index("async function showHelp()")
    end = src.index("function showFullHelp()", start)
    block = src[start:end]
    assert "setCode(" not in block
    assert "clearEditor(" not in block
    assert ".setValue(" not in block
    assert "out(msg);" in block
    assert "speak(speech);" in block


@pytest.mark.parametrize("text,expected_action", [
    ("run", "run"),
    ("clear editor", "clear_editor"),
    ("walk me through this program", "walk_through"),
    ("create a quiz game split into multiple files", "generate_code"),
    ("open main", "open_project_file"),
    ("run main", "run_project_file"),
    ("start tutorial", "start_tutorial"),
])
def test_existing_demo_commands_still_route(client, text, expected_action):
    data = _action(client, text)
    assert data["action"] == expected_action
