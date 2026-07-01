import pytest

import app as app_module
from codeup.commands import deterministic_code_tools
from codeup.runtime import sandbox_runner
from app import app


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


@pytest.mark.parametrize(
    "text,payload,expected",
    [
        ("what can I do here?", {}, "generate code"),
        ("read errors only", {"error": "NameError: name 'total' is not defined"}, "used before it has a value"),
        ("preflight check", {"code": "print('ready')\n"}, "ready to run"),
        ("show code stats", {"code": "for i in range(3):\n    print(i)\n"}, "1 loop"),
        ("show safe imports", {}, "math, random"),
    ],
)
def test_requested_deterministic_commands_have_meaningful_speech(client, text, payload, expected):
    data = client.post("/voice-command", json={"text": text, **payload}).get_json()
    speech = str(data.get("speech") or data.get("message") or "").strip()
    assert data["action"] == "deterministic_message"
    assert expected in speech.lower()


@pytest.mark.parametrize(
    "text,payload",
    [
        ("preflight check", {"code": "print('ok')\n"}),
        ("show code stats", {"code": "value = 1\n"}),
        ("show safe imports", {}),
        ("go to definition of value", {"code": "value = 1\n"}),
        ("comment current line", {"code": "value = 1\n", "cursor_line": 1}),
        ("show requirements", {"code": "import pandas\n"}),
    ],
)
def test_deterministic_tools_do_not_touch_any_ai_provider(client, monkeypatch, text, payload):
    def fail_ai(*args, **kwargs):
        raise AssertionError("deterministic tool called an AI provider")

    monkeypatch.setattr(app_module, "call_gemini", fail_ai)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail_ai)
    monkeypatch.setattr(app_module, "_call_ollama", fail_ai)

    data = client.post("/voice-command", json={"text": text, **payload}).get_json()
    assert data["action"] in {"deterministic_message", "navigate_code", "conversational_edit"}


def test_deterministic_import_guidance_matches_execution_sandbox_policy():
    assert deterministic_code_tools.ALLOWED_MODULES == sandbox_runner.ALLOWED_MODULES
    assert "os" not in sandbox_runner.ALLOWED_MODULES
    assert deterministic_code_tools.import_policy_summary("os").startswith("os is blocked")
    for module in ("math", "random", "statistics", "datetime", "json", "csv"):
        assert deterministic_code_tools.import_policy_summary(module) == (
            f"{module} is allowed in the lesson sandbox."
        )


def test_top_level_trace_step_uses_speech_friendly_name(client):
    run = client.post("/run", json={"code": "value = 1\nprint(value)\n"}).get_json()
    assert run["success"] is True

    step = client.post("/voice-command", json={"text": "next step"}).get_json()

    assert step["action"] == "next_step"
    assert "top-level code" in step["speech"]
    assert "<module" not in step["speech"]
