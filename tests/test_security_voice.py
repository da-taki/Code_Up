import os
import sys
import pytest

# Ensure the project root is importable when running pytest in the tests directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    # force subprocess sandbox behavior in tests
    monkeypatch.setenv("USE_SUBPROCESS_SANDBOX", "1")
    return monkeypatch


@pytest.fixture
def client():
    with app.test_client() as c:
        yield c


@pytest.mark.parametrize("code, expected_error_substr", [
    ("import os\nprint(os.getcwd())", "module 'os' is not allowed|import"),
    ("print(object.__subclasses__())", "name 'object' is not defined|object"),
    ("open('../outside.txt', 'w').write('x')", "name 'open' is not defined|open"),
])
def test_sandbox_escape_attempts(client, code, expected_error_substr):
    """Test that sandbox prevents common escape patterns."""
    res = client.post("/run", json={"code": code})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    # In subprocess mode, we may see different error messages - just check that execution failed
    error_lower = data["error"].lower()
    assert any(substr in error_lower for substr in expected_error_substr.split("|"))


@pytest.mark.parametrize("voice_input, expected_action, expected_value", [
    ("go to line fifteen", "goto_line", 15),
    ("read line 3", "read_line", 3),
    ("describe line 7", "describe_line", 7),
    ("clear editor", "clear_editor", None),
    ("summarize this file", "summarize", None),
    ("generate code for factorial", "generate_code", "factorial"),
    ("advise on code", "advise", None),
    ("rename snippet 1234-5678 to final", "rename_snippet", "final"),
    ("next step", "next_step", None),
    ("previous step", "previous_step", None),
    ("what changed here", "what_changed", None),
])
def test_voice_intent_parsing(client, voice_input, expected_action, expected_value):
    res = client.post("/voice-command", json={"text": voice_input})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == expected_action

    if expected_action in ("goto_line", "read_line", "describe_line"):
        assert data.get("line") == expected_value
    elif expected_action == "generate_code":
        assert expected_value in data.get("prompt", "")
    elif expected_action == "rename_snippet":
        assert data.get("new_name") == expected_value
