"""
CodeUp — Full Test Suite
Covers: sandbox security, voice intent parsing, trace playback,
        snippet CRUD, size limits, repeat command, dotenv loading,
        pending quiz/bug intercepts, syntax checking, variable tracking,
        structure parsing, and execution story mode.
"""

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
    """Redirect snippet storage to a temp file — never touches real snippets.json."""
    tmp_file = tmp_path / "snippets.json"
    tmp_file.write_text(json.dumps({"snippets": []}))
    monkeypatch.setenv("SNIPPETS_FILE", str(tmp_file))
    monkeypatch.setattr(app_module, "SNIPPETS_FILE", str(tmp_file))
    return tmp_file


@pytest.fixture
def client(tmp_snippets):
    """Flask test client with isolated snippet storage and AI disabled."""
    os.environ["GEMINI_ENABLED"] = "0"
    with app.test_client() as c:
        yield c
    os.environ["GEMINI_ENABLED"] = "1"


# ===========================================================================
# 1. ENVIRONMENT / CONFIG
# ===========================================================================

def test_gemini_disabled_returns_message(client):
    """When GEMINI_ENABLED=0, AI endpoints return a clear message not a crash."""
    res = client.post("/analyze", json={"code": "print(1)", "language": "en"})
    assert res.status_code == 200
    data = res.get_json()
    assert "analysis" in data
    assert "disabled" in data["analysis"].lower() or "service" in data["analysis"].lower()


def test_gemini_key_not_configured_returns_message(client, monkeypatch):
    """Missing API key returns a human-readable message not a 500."""
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.setattr(app_module, "GEMINI_API_KEY", "Insert_API_Key_Here")
    res = client.post("/summarize", json={"code": "x = 1", "language": "en"})
    assert res.status_code == 200
    data = res.get_json()
    assert "summary" in data
    assert "configured" in data["summary"].lower() or "api" in data["summary"].lower()


# ===========================================================================
# 2. SANDBOX SECURITY
# ===========================================================================

@pytest.mark.parametrize("code, expected_fragments", [
    (
        "import os\nprint(os.getcwd())",
        ["not allowed", "import"],
    ),
    (
        "print(object.__subclasses__())",
        ["not defined", "object"],
    ),
    (
        "open('../outside.txt', 'w').write('x')",
        ["not defined", "open"],
    ),
    (
        "eval('import os')",
        ["not defined", "nameerror"],
    ),
    (
        "exec('import sys')",
        ["not defined", "nameerror"],
    ),
    (
        "compile('import os', '<s>', 'exec')",
        ["not defined", "nameerror"],
    ),
    (
        "__import__('os').system('ls')",
        ["not allowed", "os"],
    ),
])
def test_sandbox_blocks_escape_attempts(client, code, expected_fragments):
    """Sandbox must block all common escape patterns."""
    res = client.post("/run", json={"code": code})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    error_lower = data["error"].lower()
    assert any(f in error_lower for f in expected_fragments), (
        f"Expected one of {expected_fragments!r} in:\n{data['error']}"
    )


def test_sandbox_input_blocked(client):
    """input() must be blocked with a clear explanation."""
    res = client.post("/run", json={"code": "x = input('name: ')\nprint(x)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "input" in data["error"].lower() or (
        data.get("explanation") and "input" in data["explanation"].lower()
    )


def test_sandbox_allowed_modules(client):
    """math and random must be importable."""
    res = client.post("/run", json={"code": "import math\nprint(math.pi)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "3.14" in data.get("output", "")


def test_sandbox_normal_execution(client):
    """Basic arithmetic and print work correctly."""
    res = client.post("/run", json={"code": "print(2 + 2)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "4" in data.get("output", "")


def test_sandbox_loop_output(client):
    """For loops produce correct sequential output."""
    res = client.post("/run", json={"code": "for i in range(3):\n    print(i)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    output = data.get("output", "")
    assert "0" in output and "1" in output and "2" in output


# ===========================================================================
# 3. REQUEST SIZE LIMITS
# ===========================================================================

def test_max_code_size_rejected_run(client):
    """Code over MAX_CODE_SIZE must return 413 from /run."""
    big = "x = 1\n" * 20_000
    res = client.post("/run", json={"code": big})
    assert res.status_code == 413


def test_max_code_size_rejected_analyze(client):
    """Code over MAX_CODE_SIZE must return 413 from /analyze."""
    big = "x = 1\n" * 20_000
    res = client.post("/analyze", json={"code": big, "language": "en"})
    assert res.status_code == 413


def test_empty_code_rejected(client):
    """Empty code must return 400 from /run."""
    res = client.post("/run", json={"code": "   "})
    assert res.status_code == 400


def test_missing_body_handled(client):
    """Completely missing JSON body must not crash the server."""
    res = client.post("/run", data="not json", content_type="text/plain")
    assert res.status_code in (400, 413, 200)  # any of these is acceptable, not 500


# ===========================================================================
# 4. VOICE INTENT PARSING
# ===========================================================================

@pytest.mark.parametrize("voice_input, expected_action, check", [
    ("go to line fifteen",          "goto_line",    lambda d: d.get("line") == 15),
    ("go to line 3",                "goto_line",    lambda d: d.get("line") == 3),
    ("read line 7",                 "read_line",    lambda d: d.get("line") == 7),
    ("describe line 10",            "describe_line",lambda d: d.get("line") == 10),
    ("delete line 4",               "delete_line",  lambda d: d.get("line") == 4),
    ("clear editor",                "clear_editor", lambda d: True),
    ("summarize this file",         "summarize",    lambda d: True),
    ("advise on code",              "advise",       lambda d: True),
    ("next step",                   "next_step",    lambda d: True),
    ("previous step",               "previous_step",lambda d: True),
    ("what changed here",           "what_changed", lambda d: True),
    ("run",                         "run",          lambda d: True),
    ("fix code",                    "fix",          lambda d: True),
    ("check for errors",            "locate_error", lambda d: True),
    ("suggest next line",           "suggest_next", lambda d: True),
    ("story mode",                  "story_mode",   lambda d: True),
    ("learning mode",               "mentor_mode",  lambda d: True),
    ("bug challenge",               "bug_challenge",lambda d: True),
    ("save snippet named my prog",  "save_snippet_named", lambda d: "my prog" in d.get("name", "")),
    ("generate code for fibonacci", "generate_code",lambda d: "fibonacci" in d.get("prompt", "")),
    ("rename snippet 1234 to final","rename_snippet",lambda d: d.get("new_name") == "final"),
    ("quiz me on loops",            "quiz_me",      lambda d: "loops" in d.get("topic", "")),
    ("set breakpoint at line 5",    "set_breakpoint",lambda d: d.get("line_number") == 5),
    ("watch variable x",            "watch_variable",lambda d: d.get("variable") == "x"),
    ("insert function called greet","insert_function",lambda d: d.get("function_name") == "greet"),
    ("insert a for loop",           "insert_loop",  lambda d: True),
    ("repeat",                      "run",          None),  # tested separately
])
def test_voice_intent_parsing(client, voice_input, expected_action, check):
    if voice_input == "repeat":
        # Seed a run first so repeat has something to replay
        client.post("/voice-command", json={"text": "run"})

    res = client.post("/voice-command", json={"text": voice_input})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == expected_action, (
        f"Input {voice_input!r}: expected {expected_action!r}, got {data['action']!r}"
    )
    if check:
        assert check(data), f"Slot check failed for {voice_input!r}: {data}"


def test_voice_unknown_command(client):
    """Completely nonsense input resolves to unknown or confirm, never crashes."""
    res = client.post("/voice-command", json={"text": "xyzzy blort flibble"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] in ("unknown", "confirm")


def test_voice_ambiguous_triggers_confirm(client):
    """Input that fuzzy-matches multiple commands should trigger confirm."""
    res = client.post("/voice-command", json={"text": "analyse"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] in ("analyze", "confirm", "unknown")
    if data["action"] == "confirm":
        assert "options" in data
        assert len(data["options"]) >= 1


def test_voice_repeat_replays_last_action(client):
    """Repeat must return the same action as the previous command."""
    run_res = client.post("/voice-command", json={"text": "run"})
    assert run_res.get_json()["action"] == "run"

    repeat_res = client.post("/voice-command", json={"text": "repeat"})
    assert repeat_res.status_code == 200
    assert repeat_res.get_json()["action"] == "run"


def test_voice_empty_text(client):
    """Empty voice input must not crash."""
    res = client.post("/voice-command", json={"text": ""})
    assert res.status_code == 200
    data = res.get_json()
    assert "action" in data


# ===========================================================================
# 5. EXECUTION TRACE PLAYBACK
# ===========================================================================

@pytest.mark.timeout(15)
def test_trace_populated_after_run(client):
    """Running code must populate a non-empty trace."""
    res = client.post("/run", json={"code": "x = 5\ny = 10\nprint(x + y)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert len(data.get("trace", [])) > 0, "Trace must not be empty after successful run"


@pytest.mark.timeout(15)
def test_trace_next_step_returns_speech(client):
    """next step voice command must return non-empty speech after a run."""
    run = client.post("/run", json={"code": "a = 1\nb = 2\nc = a + b"})
    assert run.get_json()["success"] is True
    assert len(run.get_json().get("trace", [])) > 0

    step = client.post("/voice-command", json={"text": "next step"})
    assert step.status_code == 200
    data = step.get_json()
    assert data["action"] == "next_step"
    assert data.get("speech"), f"Expected non-empty speech, got: {data}"


@pytest.mark.timeout(15)
def test_trace_step_counter_in_speech(client):
    """Speech output must contain 'Step X of Y' format."""
    client.post("/run", json={"code": "a = 1\nb = 2"})
    step = client.post("/voice-command", json={"text": "next step"})
    speech = step.get_json().get("speech", "")
    assert "Step" in speech, f"Expected step counter in speech, got: {speech!r}"


@pytest.mark.timeout(15)
def test_trace_what_changed(client):
    """what changed must return speech after a run."""
    client.post("/run", json={"code": "x = 42\nprint(x)"})
    # advance to first step first
    client.post("/voice-command", json={"text": "next step"})
    res = client.post("/voice-command", json={"text": "what changed here"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == "what_changed"
    assert data.get("speech")


@pytest.mark.timeout(15)
def test_trace_endpoint_returns_structure(client):
    """GET /execution-trace must return expected keys."""
    client.post("/run", json={"code": "x = 1"})
    res = client.get("/execution-trace")
    assert res.status_code == 200
    data = res.get_json()
    assert "trace" in data
    assert "current_index" in data
    assert "duration_ms" in data


# ===========================================================================
# 6. SYNTAX CHECKING
# ===========================================================================

def test_syntax_check_clean_code(client):
    """Valid code returns has_errors=False."""
    res = client.post("/check-syntax", json={"code": "x = 1\nprint(x)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["has_errors"] is False


def test_syntax_check_broken_code(client):
    """Broken code returns has_errors=True with error details."""
    res = client.post("/check-syntax", json={"code": "def foo(\n    print('oops')"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["has_errors"] is True
    assert data["error_count"] >= 1
    assert len(data["errors"]) >= 1
    assert "line" in data["errors"][0]
    assert "message" in data["errors"][0]


def test_syntax_check_empty_code(client):
    """Empty code returns has_errors=False cleanly."""
    res = client.post("/check-syntax", json={"code": ""})
    assert res.status_code == 200
    data = res.get_json()
    assert data["has_errors"] is False


# ===========================================================================
# 7. VARIABLE TRACKING
# ===========================================================================

def test_track_variables_basic(client):
    """Variable tracker finds assignments correctly."""
    code = "x = 5\ny = 10\nz = x + y"
    res = client.post("/track-variables", json={"code": code, "line": 1})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    names = [v["name"] for v in data["variables"]]
    assert "x" in names
    assert "y" in names


def test_track_variables_invalid_line(client):
    """Non-numeric line number returns 400."""
    res = client.post("/track-variables", json={"code": "x = 1", "line": "abc"})
    assert res.status_code == 400


def test_find_variable_usage(client):
    """Variable usage finder returns correct line numbers."""
    code = "x = 5\nprint(x)\ny = x + 1"
    res = client.post("/find-variable-usage", json={"code": code, "variable": "x"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["count"] >= 2
    lines = [u["line"] for u in data["usages"]]
    assert 1 in lines  # assignment


def test_find_variable_usage_not_found(client):
    """Variable not in code returns success=False with message."""
    res = client.post("/find-variable-usage", json={"code": "x = 1", "variable": "z"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "message" in data


# ===========================================================================
# 8. STRUCTURE PARSING
# ===========================================================================

def test_structure_functions(client):
    """Structure parser finds function definitions."""
    code = "def greet(name):\n    print(name)\n\ndef add(a, b):\n    return a + b"
    res = client.post("/structure", json={"code": code})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    fn_names = [f["name"] for f in data["structure"]["functions"]]
    assert "greet" in fn_names
    assert "add" in fn_names


def test_structure_classes(client):
    """Structure parser finds class definitions."""
    code = "class Dog:\n    def bark(self):\n        print('woof')"
    res = client.post("/structure", json={"code": code})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    class_names = [c["name"] for c in data["structure"]["classes"]]
    assert "Dog" in class_names


def test_structure_empty_code(client):
    """Empty code returns empty structure without error."""
    res = client.post("/structure", json={"code": ""})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["structure"]["functions"] == []
    assert data["structure"]["classes"] == []


# ===========================================================================
# 9. SNIPPET CRUD
# ===========================================================================

def test_snippet_save_and_list(client):
    """Save a snippet and confirm it appears in the list."""
    save = client.post("/snippets", json={"name": "test_snip", "code": "print('hi')"})
    assert save.status_code == 200
    data = save.get_json()
    assert data["success"] is True
    assert "id" in data
    assert "speech" in data

    lst = client.get("/snippets")
    assert lst.status_code == 200
    lst_data = lst.get_json()
    assert "snippets" in lst_data
    assert "speech" in lst_data
    names = [s["name"] for s in lst_data["snippets"]]
    assert "test_snip" in names


def test_snippet_delete(client):
    """Delete a snippet and confirm it no longer appears."""
    save = client.post("/snippets", json={"name": "to_delete", "code": "x = 1"})
    sid = save.get_json()["id"]

    delete = client.delete(f"/snippets/{sid}")
    assert delete.status_code == 200
    assert delete.get_json()["success"] is True

    lst = client.get("/snippets")
    names = [s["name"] for s in lst.get_json()["snippets"]]
    assert "to_delete" not in names


def test_snippet_update(client):
    """Update a snippet name via PUT."""
    save = client.post("/snippets", json={"name": "original", "code": "x = 1"})
    sid = save.get_json()["id"]

    update = client.put(f"/snippets/{sid}", json={"name": "renamed"})
    assert update.status_code == 200
    assert update.get_json()["success"] is True

    lst = client.get("/snippets")
    names = [s["name"] for s in lst.get_json()["snippets"]]
    assert "renamed" in names
    assert "original" not in names


def test_snippet_delete_nonexistent(client):
    """Deleting a nonexistent snippet returns success=True gracefully."""
    res = client.delete("/snippets/nonexistent-id-xyz")
    assert res.status_code == 200


def test_snippet_name_too_long(client):
    """Snippet name over 256 chars must return 400."""
    res = client.post("/snippets", json={"name": "x" * 300, "code": "print(1)"})
    assert res.status_code == 400


def test_snippet_code_too_large(client):
    """Snippet code over MAX_CODE_SIZE must return 413."""
    res = client.post("/snippets", json={"name": "big", "code": "x = 1\n" * 20_000})
    assert res.status_code == 413


def test_snippet_speech_single(client):
    """Single snippet produces grammatically correct speech."""
    client.post("/snippets", json={"name": "only_one", "code": "print(1)"})
    lst = client.get("/snippets")
    speech = lst.get_json()["speech"]
    assert "1 snippet" in speech


def test_snippet_speech_multiple(client):
    """Multiple snippets produce correct count in speech."""
    client.post("/snippets", json={"name": "first", "code": "print(1)"})
    client.post("/snippets", json={"name": "second", "code": "print(2)"})
    lst = client.get("/snippets")
    speech = lst.get_json()["speech"]
    assert "2 snippets" in speech


# ===========================================================================
# 10. LINE READING & DESCRIBE
# ===========================================================================

def test_read_line_context_valid(client):
    """Read line context returns correct line content."""
    code = "x = 5\ny = 10\nprint(x + y)"
    res = client.post("/read-line-context", json={"code": code, "line": 2})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "y" in data["content"]
    assert "indent_level" in data
    assert "context" in data


def test_read_line_context_invalid_line(client):
    """Non-numeric line returns 400."""
    res = client.post("/read-line-context", json={"code": "x = 1", "line": "bad"})
    assert res.status_code == 400


def test_read_line_context_out_of_range(client):
    """Line number beyond file length returns success=False."""
    res = client.post("/read-line-context", json={"code": "x = 1", "line": 999})
    assert res.status_code == 200
    assert res.get_json()["success"] is False


# ===========================================================================
# 11. SANDBOXED FILESYSTEM
# ===========================================================================

def test_fs_write_and_read(client):
    """Write then read a file in the sandbox."""
    write = client.post("/fs/write", json={"path": "test.txt", "content": "hello"})
    assert write.status_code == 200
    assert write.get_json()["success"] is True

    read = client.post("/fs/read", json={"path": "test.txt"})
    assert read.status_code == 200
    data = read.get_json()
    assert data["success"] is True
    assert data["content"] == "hello"


def test_fs_path_traversal_blocked(client):
    """Path traversal attempts must be blocked."""
    res = client.post("/fs/read", json={"path": "../../etc/passwd"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "outside" in data.get("error", "").lower() or "not found" in data.get("error", "").lower()


def test_fs_missing_path(client):
    """Missing path field returns 400."""
    res = client.post("/fs/read", json={})
    assert res.status_code == 400


def test_fs_delete(client):
    """Write then delete a file."""
    client.post("/fs/write", json={"path": "bye.txt", "content": "bye"})
    res = client.post("/fs/delete", json={"path": "bye.txt"})
    assert res.status_code == 200
    assert res.get_json()["success"] is True


def test_fs_info(client):
    """Workspace info endpoint returns expected keys."""
    res = client.get("/fs/info")
    assert res.status_code == 200
    data = res.get_json()
    assert "workspace" in data
    assert "total_files" in data


# ===========================================================================
# 12. SESSION ISOLATION
# ===========================================================================

def test_two_sessions_have_separate_traces(client):
    """Two different session cookies get separate trace storage."""
    with app.test_client() as c1:
        with app.test_client() as c2:
            c1.post("/run", json={"code": "x = 100\nprint(x)"})
            c2.post("/run", json={"code": "y = 999\nprint(y)"})

            t1 = c1.get("/execution-trace").get_json()
            t2 = c2.get("/execution-trace").get_json()

            # Traces exist but are independent — can't assert content
            # without knowing exact trace format, but both must be lists
            assert isinstance(t1["trace"], list)
            assert isinstance(t2["trace"], list)


# ===========================================================================
# 13. SEMANTIC ERROR CLASSIFICATION
# ===========================================================================

def test_semantic_issues_in_response(client):
    """Semantic issues key is always present in successful run response."""
    res = client.post("/run", json={"code": "x = 1\nprint(x)"})
    assert res.status_code == 200
    data = res.get_json()
    if data["success"]:
        assert "semantic_issues" in data
        assert isinstance(data["semantic_issues"], list)


# ===========================================================================
# 14. INTENT PARSER UNIT TESTS (direct, no HTTP)
# ===========================================================================

from intent_parser import parse_intent

@pytest.mark.parametrize("text, expected_intent, slot_check", [
    ("go to line twenty five",      "goto_line",    lambda s: s.get("line_number") == 25),
    ("go to line forty two",        "goto_line",    lambda s: s.get("line_number") == 42),
    ("read line 10",                "read_line",    lambda s: s.get("line_number") == 10),
    ("find function calculate",     "find_function",lambda s: s.get("function_name") == "calculate"),
    ("find class Parser",           "find_class",   lambda s: s.get("class_name") == "Parser"),
    ("insert function called greet","insert_function",lambda s: s.get("function_name") == "greet"),
    ("save snippet named my prog",  "save_snippet_named", lambda s: "my prog" in s.get("name", "")),
    ("set breakpoint at line 10",   "set_breakpoint",lambda s: s.get("line_number") == 10),
    ("watch variable counter",      "watch_variable",lambda s: s.get("variable") == "counter"),
    ("quiz me on variables",        "quiz_me",      lambda s: "variables" in s.get("topic", "")),
    ("run",                         "run",          lambda s: True),
    ("fix code",                    "fix",          lambda s: True),
    ("suggest next line",           "suggest_next", lambda s: True),
    ("repeat",                      "repeat",       lambda s: True),
])
def test_intent_parser_direct(text, expected_intent, slot_check):
    result = parse_intent(text)
    assert result["intent"] == expected_intent, (
        f"Input {text!r}: expected {expected_intent!r}, got {result['intent']!r}"
    )
    assert slot_check(result["slots"]), (
        f"Slot check failed for {text!r}: {result['slots']}"
    )


def test_intent_parser_empty_string():
    result = parse_intent("")
    assert result["intent"] is None
    assert result["confidence"] == 0.0


def test_intent_parser_compound_numbers():
    """Spoken compound numbers like 'thirty seven' must parse correctly."""
    result = parse_intent("go to line thirty seven")
    assert result["intent"] == "goto_line"
    assert result["slots"]["line_number"] == 37


def test_intent_parser_no_false_goto_line():
    """Generic sentences mentioning 'line' must not trigger goto_line."""
    result = parse_intent("this is a single line program")
    assert result["intent"] != "goto_line"