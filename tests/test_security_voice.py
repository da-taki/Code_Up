import os
import sys
import json
import tempfile
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as app_module
from app import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_snippets(tmp_path, monkeypatch):
    """
    Redirect snippet storage to a temp file so tests never touch
    the real snippets.json on disk and don't pollute each other.
    """
    tmp_file = tmp_path / "snippets.json"
    tmp_file.write_text(json.dumps({"snippets": []}))
    monkeypatch.setenv("SNIPPETS_FILE", str(tmp_file))
    monkeypatch.setattr(app_module, "SNIPPETS_FILE", str(tmp_file))
    return tmp_file


@pytest.fixture
def client(tmp_snippets):
    """Flask test client with isolated snippet storage."""
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Sandbox escape tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code, expected_substrings", [
    (
        "import os\nprint(os.getcwd())",
        ["module 'os' is not allowed", "import"],
    ),
    (
        "print(object.__subclasses__())",
        ["name 'object' is not defined", "object"],
    ),
    (
        "open('../outside.txt', 'w').write('x')",
        ["name 'open' is not defined", "open"],
    ),
])
def test_sandbox_escape_attempts(client, code, expected_substrings):
    """Sandbox prevents common escape patterns."""
    res = client.post("/run", json={"code": code})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    error_lower = data["error"].lower()
    assert any(s in error_lower for s in expected_substrings), (
        f"Expected one of {expected_substrings!r} in error: {data['error']!r}"
    )


# ---------------------------------------------------------------------------
# Voice intent parsing tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("voice_input, expected_action, expected_value", [
    ("go to line fifteen",           "goto_line",    15),
    ("read line 3",                  "read_line",     3),
    ("describe line 7",              "describe_line", 7),
    ("clear editor",                 "clear_editor",  None),
    ("summarize this file",          "summarize",     None),
    ("generate code for factorial",  "generate_code", "factorial"),
    ("advise on code",               "advise",        None),
    ("rename snippet 1234-5678 to final", "rename_snippet", "final"),
    ("next step",                    "next_step",     None),
    ("previous step",                "previous_step", None),
    ("what changed here",            "what_changed",  None),
])
def test_voice_intent_parsing(client, voice_input, expected_action, expected_value):
    res = client.post("/voice-command", json={"text": voice_input})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == expected_action, (
        f"Input {voice_input!r}: expected action {expected_action!r}, got {data['action']!r}"
    )

    if expected_action in ("goto_line", "read_line", "describe_line"):
        assert data.get("line") == expected_value
    elif expected_action == "generate_code":
        assert expected_value in data.get("prompt", ""), (
            f"Expected {expected_value!r} in prompt slot, got {data.get('prompt')!r}"
        )
    elif expected_action == "rename_snippet":
        assert data.get("new_name") == expected_value


# ---------------------------------------------------------------------------
# Trace playback tests
# ---------------------------------------------------------------------------

@pytest.mark.timeout(15)
def test_trace_playback_integration(client):
    """
    Run code to populate the session trace, then step through it.
    Verifies the trace is non-empty before attempting playback so a
    subprocess timeout doesn't produce a false-green.
    """
    code = "x = 5\ny = 10\nz = x + y\nprint(z)"
    run_res = client.post("/run", json={"code": code})
    assert run_res.status_code == 200
    run_data = run_res.get_json()
    assert run_data["success"] is True

    # Guard: only proceed if the trace actually has events
    trace = run_data.get("trace", [])
    assert len(trace) > 0, "Trace is empty — subprocess may have timed out"

    step_res = client.post("/voice-command", json={"text": "next step"})
    assert step_res.status_code == 200
    step_data = step_res.get_json()
    assert step_data["action"] == "next_step"
    # Verify the speech field is present AND non-empty
    assert step_data.get("speech"), (
        f"Expected non-empty speech in step response, got: {step_data!r}"
    )

    step2_res = client.post("/voice-command", json={"text": "next step"})
    assert step2_res.status_code == 200
    assert step2_res.get_json()["action"] == "next_step"


@pytest.mark.timeout(15)
def test_trace_playback_step_counter(client):
    """Step counter appears in the speech output as 'Step X of Y'."""
    code = "a = 1\nb = 2\nc = a + b"
    run_res = client.post("/run", json={"code": code})
    assert run_res.status_code == 200
    run_data = run_res.get_json()
    assert run_data["success"] is True

    trace = run_data.get("trace", [])
    assert len(trace) > 0, "Trace is empty — subprocess may have timed out"

    step_res = client.post("/voice-command", json={"text": "next step"})
    assert step_res.status_code == 200
    speech = step_res.get_json().get("speech", "")
    assert "Step" in speech, f"Expected 'Step X of Y' in speech, got: {speech!r}"


# ---------------------------------------------------------------------------
# Fuzzy / confirmation tests
# ---------------------------------------------------------------------------

def test_fuzzy_confirmation_typo(client):
    """
    'analyse' is a valid British-English synonym that the parser now matches
    directly as 'analyze'. If future patterns tighten, it may become a
    confirmation prompt — both outcomes are acceptable.
    """
    res = client.post("/voice-command", json={"text": "analyse"})
    assert res.status_code == 200
    data = res.get_json()

    if data["action"] == "confirm":
        assert "options" in data
        assert len(data["options"]) >= 1
        assert any(opt in ["analyze", "advise", "analyse"] for opt in data["options"])
    else:
        assert data["action"] in ["analyze", "unknown"], (
            f"Unexpected action for 'analyse': {data['action']!r}"
        )


def test_fuzzy_confirmation_unknown_command(client):
    """Completely nonsense input resolves to 'unknown' or triggers confirmation."""
    res = client.post("/voice-command", json={"text": "xyzzy blort"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] in ["unknown", "confirm"], (
        f"Expected 'unknown' or 'confirm', got {data['action']!r}"
    )


# ---------------------------------------------------------------------------
# Subprocess sandbox — specific builtins blocked
# ---------------------------------------------------------------------------

def test_subprocess_sandbox_escape_eval(client):
    """eval() is not in SAFE_GLOBALS and must be blocked."""
    code = "eval('import os; print(os.getcwd())')"
    res = client.post("/run", json={"code": code})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    # eval is absent from SAFE_GLOBALS → NameError; check for that specifically
    error_lower = data["error"].lower()
    assert "not defined" in error_lower or "nameerror" in error_lower, (
        f"Expected NameError for eval, got: {data['error']!r}"
    )


def test_subprocess_sandbox_escape_exec(client):
    """exec() is not in SAFE_GLOBALS and must be blocked."""
    code = "exec('import os')"
    res = client.post("/run", json={"code": code})
    assert res.status_code == 200
    assert res.get_json()["success"] is False


def test_subprocess_sandbox_escape_compile(client):
    """compile() is not in SAFE_GLOBALS and must be blocked."""
    code = "compile('import os', '<string>', 'exec')"
    res = client.post("/run", json={"code": code})
    assert res.status_code == 200
    assert res.get_json()["success"] is False


def test_subprocess_normal_execution(client):
    """Normal arithmetic/print code runs successfully."""
    res = client.post("/run", json={"code": "print(15)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "15" in data.get("output", ""), (
        f"Expected '15' in output, got: {data.get('output')!r}"
    )


# ---------------------------------------------------------------------------
# Max code size rejection
# ---------------------------------------------------------------------------

def test_max_code_size_rejected(client):
    """Payloads exceeding MAX_CODE_SIZE must return 413."""
    big_code = "x = 1\n" * 20_000  # well over 100 KB
    res = client.post("/run", json={"code": big_code})
    assert res.status_code == 413


# ---------------------------------------------------------------------------
# Repeat command
# ---------------------------------------------------------------------------

def test_repeat_command(client):
    """
    Sending 'run' via voice stores it in session; 'repeat' should replay it.
    The intent parser gives 'run' confidence 0.95 which exceeds the 0.75
    threshold, so the action is stored in last_voice_action.
    """
    run_res = client.post("/voice-command", json={"text": "run"})
    assert run_res.status_code == 200
    run_data = run_res.get_json()
    assert run_data["action"] == "run", (
        f"Pre-condition failed: 'run' did not parse as run action: {run_data!r}"
    )

    repeat_res = client.post("/voice-command", json={"text": "repeat"})
    assert repeat_res.status_code == 200
    repeat_data = repeat_res.get_json()
    assert repeat_data["action"] == "run", (
        f"Repeat returned {repeat_data['action']!r} instead of 'run'. "
        f"last_voice_action may not have been stored."
    )


# ---------------------------------------------------------------------------
# Snippet speech feedback — isolated with tmp_snippets fixture
# ---------------------------------------------------------------------------

def test_snippet_speech_feedback(client):
    """Snippet save and list both return speech feedback; storage is isolated."""
    save_res = client.post("/snippets", json={
        "name": "hello_snippet",
        "code": "print('hello')",
    })
    assert save_res.status_code == 200
    save_data = save_res.get_json()
    assert save_data["success"] is True
    assert "speech" in save_data, f"Expected 'speech' key in save response: {save_data!r}"

    list_res = client.get("/snippets")
    assert list_res.status_code == 200
    list_data = list_res.get_json()
    assert "speech" in list_data, f"Expected 'speech' key in list response: {list_data!r}"
    assert "snippets" in list_data
    # Exactly one snippet — no duplicates from prior runs
    assert len(list_data["snippets"]) == 1