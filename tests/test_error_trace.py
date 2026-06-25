"""Error Trace Narration: make Python errors understandable non-visually.

Covers the deterministic error_trace module (parsing + per-exception narration,
multi-file frames, safe value handling) and the /voice-command routing that
exposes it, including read-errors-only and mistake-replay integration.
"""

import pytest

import error_trace
from app import app
from intent_parser import parse_intent


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def vc(client, text, **payload):
    payload.setdefault("source", "typed")
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


def run(client, code, **extra):
    return client.post("/run", json={"code": code, **extra}).get_json()


# ---- module: parsing + per-exception narration --------------------------

def test_syntaxerror_parsing_and_narration():
    a = error_trace.analyze('  File "<user>", line 2\n    x =\n       ^\nSyntaxError: invalid syntax')
    assert a["exception_type"] == "SyntaxError"
    assert a["line"] == 2
    assert "syntax" in error_trace.narrate(a).lower()


def test_indentationerror_narration():
    tb = ('Traceback (most recent call last):\n  File "<user>", line 2\n'
          '    print(i)\nIndentationError: expected an indented block')
    a = error_trace.analyze("", traceback_text=tb, code="for i in range(3):\nprint(i)\n")
    assert a["exception_type"] == "IndentationError"
    assert a["line"] == 2
    speech = error_trace.narrate(a)
    assert "indented block" in speech
    assert "print(i)" in speech  # code line fetched


def test_nameerror_narration():
    a = error_trace.analyze("NameError: name 'total' is not defined", code="print(total)\n")
    assert a["exception_type"] == "NameError"
    assert "total" in a["beginner_explanation"]
    assert "Define total" in a["next_steps"]


def test_typeerror_narration():
    a = error_trace.analyze("Line 1: TypeError: can only concatenate str (not \"int\") to str")
    assert a["exception_type"] == "TypeError"
    assert "wrong type" in a["likely_cause"]


def test_valueerror_narration_and_value():
    a = error_trace.analyze("Line 1: ValueError: invalid literal for int() with base 10: 'abc'")
    assert a["exception_type"] == "ValueError"
    assert "number" in a["likely_cause"]
    assert a["value"] == "'abc'"
    assert "'abc'" in error_trace.value_narration(a)


def test_zerodivisionerror_narration():
    a = error_trace.analyze("Line 3: ZeroDivisionError: division by zero")
    assert a["exception_type"] == "ZeroDivisionError"
    assert "zero" in a["likely_cause"]
    assert error_trace.value_narration(a).startswith("The value that caused")


def test_indexerror_and_keyerror_narration():
    idx = error_trace.analyze("Line 2: IndexError: list index out of range")
    assert "position" in idx["likely_cause"]
    key = error_trace.analyze("Line 2: KeyError: 'name'")
    assert key["exception_type"] == "KeyError"
    assert "'name'" in key["likely_cause"]


def test_modulenotfound_narration():
    a = error_trace.analyze("Line 1: ModuleNotFoundError: No module named 'reqests'")
    assert a["exception_type"] == "ModuleNotFoundError"
    assert "reqests" in a["likely_cause"]
    assert a["value"] == "'reqests'"


def test_generic_exception_fallback():
    a = error_trace.analyze("Line 5: RuntimeError: something odd happened")
    assert a["has_error"]
    assert a["next_steps"]  # always offers a next step
    assert "error" in error_trace.narrate(a).lower()


def test_file_line_and_code_line_extraction():
    code = "x = 1\ny = x + undefined\n"
    a = error_trace.analyze('  File "<user>", line 2, in <module>\nNameError: name \'undefined\' is not defined',
                            code=code, executed_file="main.py")
    assert a["line"] == 2
    assert a["file"] == "main.py"
    assert a["code_line"] == "y = x + undefined"


def test_multifile_traceback_identifies_crash_and_call_chain():
    tb = ('Traceback (most recent call last):\n'
          '  File "main.py", line 8, in <module>\n    run()\n'
          '  File "main.py", line 5, in run\n    print(score.calculate(x))\n'
          '  File "score.py", line 6, in calculate\n    return 10 / 0\n'
          'ZeroDivisionError: division by zero')
    files = {"main.py": "x=1\n", "score.py": "def calculate(a):\n    #\n    #\n    #\n    #\n    return 10 / 0\n"}
    a = error_trace.analyze("", traceback_text=tb, project_files=files, executed_file="main.py")
    assert a["file"] == "score.py"
    assert a["line"] == 6
    speech = error_trace.narrate(a)
    assert "score.py, line 6" in speech
    assert "main.py called calculate in score.py" in speech
    assert a["code_line"] == "return 10 / 0"


def test_what_value_safe_fallback_when_not_in_trace():
    a = error_trace.analyze("Line 2: IndexError: list index out of range",
                            code="nums = [1]\nprint(nums[5])\n")
    assert a["value"] == ""
    narration = error_trace.value_narration(a)
    assert "cannot see the exact runtime value" in narration
    assert "nums[5]" in narration  # still points to the failing line


def test_full_trace_available_flag():
    assert error_trace.analyze("Line 1: ValueError: bad")["full_trace_available"] is False
    tb = 'Traceback (most recent call last):\n  File "<user>", line 1\nValueError: bad'
    assert error_trace.analyze("", traceback_text=tb)["full_trace_available"] is True


def test_no_error_is_graceful():
    a = error_trace.analyze("")
    assert a["has_error"] is False
    assert "no recent" in error_trace.narrate(a).lower()


# ---- voice-command routing ---------------------------------------------

def test_error_trace_intents_registered():
    assert parse_intent("explain error")["intent"] == "explain_error_trace"
    assert parse_intent("trace error")["intent"] == "explain_error_trace"
    assert parse_intent("where did it crash")["intent"] == "crash_location"
    assert parse_intent("what caused this")["intent"] == "error_cause"
    assert parse_intent("what value caused this")["intent"] == "error_value"
    assert parse_intent("read full traceback")["intent"] == "read_full_traceback"
    assert parse_intent("what should I test next")["intent"] == "test_next"
    assert parse_intent("fix with explanation")["intent"] == "fix_with_explanation"


def test_explain_error_after_failed_run(client):
    run(client, 'age = int("abc")\nprint(age)\n')
    data = vc(client, "explain error", code='age = int("abc")\nprint(age)\n')
    assert data["action"] == "deterministic_message"
    assert "ValueError" in data["speech"]
    assert "number" in data["speech"]
    assert "ai_action" not in data  # narration never mutates code


def test_where_did_it_crash(client):
    run(client, 'age = int("abc")\n')
    data = vc(client, "where did it crash")
    assert data["action"] == "deterministic_message"
    assert "line 1" in data["speech"]


def test_what_caused_this(client):
    run(client, 'age = int("abc")\n')
    data = vc(client, "what caused this")
    assert "ValueError" in data["speech"]
    assert "number" in data["speech"]


def test_what_value_caused_this_does_not_hallucinate(client):
    run(client, 'age = int("abc")\n')
    data = vc(client, "what value caused this")
    assert "'abc'" in data["speech"]
    # IndexError has no value in the trace -> safe fallback
    run(client, "nums = [1]\nprint(nums[5])\n")
    data2 = vc(client, "what value caused this")
    assert "cannot see the exact runtime value" in data2["speech"]


def test_read_full_traceback_only_on_request(client):
    run(client, 'age = int("abc")\n')
    short = vc(client, "explain error")
    assert "Full traceback" not in short["speech"]
    full = vc(client, "read full traceback")
    assert full["action"] == "deterministic_message"
    assert "ValueError" in full["speech"]


def test_what_should_i_test_next(client):
    run(client, 'age = int("abc")\n')
    data = vc(client, "what should I test next")
    assert data["action"] == "deterministic_message"
    assert "test next" in data["speech"].lower()


def test_fix_with_explanation_proposes_not_applies(client):
    run(client, "for i in range(3):\nprint(i)\n")
    data = vc(client, "fix with explanation")
    assert data["action"] == "deterministic_message"
    assert "Proposed change:" in data["speech"]
    assert "Indent line 2" in data["speech"]
    assert "ai_action" not in data  # must not silently apply


def test_explain_error_without_run_is_graceful(client):
    data = vc(client, "explain error", code="print('hi')\n")
    assert data["action"] == "deterministic_message"
    assert "no recent" in data["speech"].lower()


def test_read_errors_only_is_narrated_not_raw(client):
    data = vc(client, "read errors only", code="x = 1\n",
              error='Traceback (most recent call last):\n  File "<user>", line 2\nNameError: name \'total\' is not defined')
    assert data["action"] == "deterministic_message"
    assert "Traceback" not in data["speech"]
    assert "total" in data["speech"]


def test_mistake_replay_mentions_error_cause(client):
    run(client, "print(total)\n")              # broken: NameError
    run(client, "total = 0\nprint(total)\n")    # fixed
    data = vc(client, "replay mistake")
    assert data["action"] == "deterministic_message"
    assert "NameError" in data["speech"]


def test_error_trace_does_not_call_ai(client, monkeypatch):
    import app as app_module

    def fail(*args, **kwargs):
        raise AssertionError("AI provider called for deterministic error trace")

    monkeypatch.setattr(app_module, "call_gemini", fail)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail)
    run(client, 'age = int("abc")\n')
    assert vc(client, "explain error")["success"] is not False
