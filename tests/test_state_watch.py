"""State / Variable Watch Narration: hear program state without scanning code.

Covers the deterministic state_watch module (variable summaries, value formatting,
trace parsing, steps, loops, conditions) and the /voice-command handlers, which
trace only through the existing sandbox (never executing user code in-process).
"""

import pytest

import app as app_module
from codeup.runtime import state_watch as sw
from app import app, _run_with_trace_for_narration
from codeup.commands.intent_parser import parse_intent


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def vc(client, text, **payload):
    payload.setdefault("source", "typed")
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


FLOW1 = "score = 0\nscore = score + 1\nprint(score)\n"
LOOP = ("marks = [80, 45, 90]\n\nfor mark in marks:\n"
        "    if mark >= 50:\n        print(\"Passed\")\n"
        "    else:\n        print(\"Needs practice\")\n")


# ---- module: variable + value summaries (no execution) -----------------

def test_variable_summary_types():
    code = "n = 5\ns = 'hi'\nb = True\nlst = [1, 2]\nd = {'a': 1}\n"
    types = {v["name"]: v["type"] for v in sw.list_variables(code)}
    assert types == {"n": "number", "s": "text", "b": "boolean", "lst": "list", "d": "dictionary"}


def test_summarize_value_number_string_bool():
    assert sw.summarize_value("42") == "42"
    assert sw.summarize_value("True") == "True"
    assert sw.summarize_value("'hello'") == "text"
    assert sw.summarize_value("'hello'", full=True) == "'hello'"


def test_summarize_value_list_and_dict():
    assert sw.summarize_value("[80, 45, 90]") == "a list with 3 numbers"
    assert "dictionary with 2" in sw.summarize_value("{'a': 1, 'b': 2}")


def test_safe_value_truncation():
    big_list = "[" + ", ".join(str(i) for i in range(50)) + "]"
    assert sw.summarize_value(big_list) == "a list with 50 numbers"  # never reads all 50
    long_text = "'" + "z" * 200 + "'"
    assert sw.summarize_value(long_text) == "a long piece of text"


def test_summarize_value_on_sandbox_truncated_repr_does_not_report_a_wrong_count():
    """Regression: sandbox_runner._safe_repr() caps every traced value's repr() at
    200 chars as `r[:197] + '...'` before state_watch ever sees it - the result has
    no closing bracket. _count_items() used to blindly do `repr[1:-1]` and count
    top-level commas by bracket depth; on an unclosed fragment, any nested bracket
    that got cut off mid-way pushes depth permanently off zero, so trailing
    top-level commas are miscounted - Variable Watch could report a wrong item
    count for any list/dict/tuple long enough to be truncated at the sandbox layer.
    It must now say the count isn't available rather than guess a wrong number.
    """
    # Mirrors sandbox_runner._safe_repr()'s exact truncation: real_repr[:197] + '...'
    real_repr = "[" + ", ".join(str(i) for i in range(100)) + "]"  # far over 200 chars
    assert len(real_repr) > 200
    truncated_repr = real_repr[:197] + "..."
    assert not truncated_repr.endswith("]"), "fixture should reproduce the unclosed-bracket shape"

    result = sw.summarize_value(truncated_repr)
    assert result == "a list too large to count exactly"
    assert "with -1" not in result and "with 0" not in result  # never a confidently-wrong count

    truncated_dict = ("{" + ", ".join(f"'k{i}': {i}" for i in range(30)) + "}")[:197] + "..."
    assert sw.summarize_value(truncated_dict) == "dictionary too large to count exactly"


def test_count_items_still_counts_correctly_for_normal_balanced_reprs():
    # Non-truncated reprs (the overwhelmingly common case) must be unaffected.
    assert sw._count_items("[1, 2, 3]") == 3
    assert sw._count_items("[]") == 0
    assert sw._count_items("{'a': 1, 'b': 2}") == 2
    assert sw._count_items("(1, 2, 3, 4)") == 4


def test_parse_state_and_variable_value():
    trace = [
        {"type": "line_exec", "line": 1},
        {"type": "line_exec", "line": 2},
        {"type": "state_change", "line": 2, "changes": ["score initialized to 0"]},
        {"type": "line_exec", "line": 3},
        {"type": "state_change", "line": 3, "changes": ["score changed from 0 to 1"]},
    ]
    state = sw.parse_state(trace)
    assert state["score"]["value"] == "1"
    assert state["score"]["from"] == "0"
    narration = sw.variable_value(state, "score")
    assert "score is currently 1" in narration
    assert "changed from 0 to 1 on line 3" in narration


def test_variable_value_missing_is_graceful():
    assert "do not have a value" in sw.variable_value({}, "ghost")


def test_narrate_step_and_condition():
    steps = [{"line": 1, "kind": "assignment", "text": "x becomes 1"}]
    assert "Step 1 of 1" in sw.narrate_step(steps, 0)
    assert "no steps" in sw.narrate_step([], 0)
    assert "true" in sw.narrate_condition({"test": "x is greater than 1", "result": True, "reason": ""})


# ---- module: against a real sandbox trace ------------------------------

def test_step_trace_for_straight_line_code():
    r = _run_with_trace_for_narration("a = 1\nb = 2\nprint(a + b)\n", set(), "s_line")
    steps = sw.build_steps(r["raw_trace"], "a = 1\nb = 2\nprint(a + b)\n")
    texts = " ".join(s["text"] for s in steps)
    assert "a becomes 1" in texts and "b becomes 2" in texts


def test_step_trace_and_loop_state_for_loop():
    r = _run_with_trace_for_narration(LOOP, set(), "s_loop")
    loop = sw.loop_state(r["raw_trace"], LOOP)
    assert "ran 3 times" in loop
    assert "current value of mark is 90" in loop


def test_condition_true_and_false_explanations():
    r = _run_with_trace_for_narration(LOOP, set(), "s_cond")
    outcomes = sw.condition_outcomes(r["raw_trace"], LOOP)
    results = [o["result"] for o in outcomes]
    assert results == [True, False, True]  # 80>=50, 45>=50, 90>=50
    passed = [o for o in outcomes if o["result"]][-1]
    assert "greater than or equal to 50 was true" in sw.narrate_condition(passed)
    assert "mark was 90" in sw.narrate_condition(passed)


def test_output_summary_from_trace():
    r = _run_with_trace_for_narration(FLOW1, set(), "s_out")
    assert r["output"].strip() == "1"


# ---- routing -----------------------------------------------------------

def test_state_intents_registered():
    for cmd, expected in [
        ("show program state", "program_state"), ("what variables exist", "summarize_variables"),
        ("what is score now", "variable_now"), ("read watched variables", "read_watched"),
        ("step through this", "step_through"), ("next step", "next_step"),
        ("previous step", "previous_step"), ("explain loop state", "loop_state"),
        ("why did this condition pass", "condition_pass"),
        ("why did this condition fail", "condition_fail"),
        ("what did the program print", "program_output"),
    ]:
        assert parse_intent(cmd)["intent"] == expected, cmd


def test_what_variables_exist_command(client):
    data = vc(client, "what variables exist", code=FLOW1)
    assert data["action"] == "deterministic_message"
    assert "score (number)" in data["speech"]


def test_what_is_variable_now_command(client):
    data = vc(client, "what is score now", code=FLOW1)
    assert "score is currently 1" in data["speech"]


def test_show_program_state_command(client):
    data = vc(client, "show program state", code=FLOW1)
    assert "Current state:" in data["speech"]
    assert "score is 1" in data["speech"]
    assert "printed 1 line" in data["speech"]


def test_step_through_and_next_previous(client):
    first = vc(client, "step through this", code=LOOP)
    assert "Step 1 of" in first["speech"]
    second = vc(client, "next step", code=LOOP)
    assert "Step 2 of" in second["speech"]
    back = vc(client, "previous step", code=LOOP)
    assert "Step 1 of" in back["speech"]


def test_explain_loop_state_command(client):
    vc(client, "step through this", code=LOOP)
    data = vc(client, "explain loop state", code=LOOP)
    assert "ran 3 times" in data["speech"]


def test_why_condition_pass_and_fail(client):
    vc(client, "step through this", code=LOOP)
    assert "was true" in vc(client, "why did this condition pass", code=LOOP)["speech"]
    assert "was false" in vc(client, "why did this condition fail", code=LOOP)["speech"]


def test_what_did_the_program_print(client):
    vc(client, "show program state", code=LOOP)
    data = vc(client, "what did the program print", code=LOOP)
    assert "printed 3 lines" in data["speech"]


def test_watch_unwatch_stores_session_memory(client):
    assert vc(client, "watch variable score", code=FLOW1)["action"] == "watch_variable"
    vc(client, "show program state", code=FLOW1)  # populate values
    watched = vc(client, "read watched variables", code=FLOW1)
    assert "score" in watched["speech"]
    assert vc(client, "unwatch score", code=FLOW1)["action"] == "stop_watching"
    after = vc(client, "read watched variables", code=FLOW1)
    assert "not watching any variables" in after["speech"]


def test_stale_trace_after_code_change(client):
    vc(client, "step through this", code=LOOP)
    data = vc(client, "explain loop state", code="x = 1\n")  # code changed since trace
    assert "code changed after the last trace" in data["speech"]


def test_error_during_trace_integrates_error_trace(client):
    data = vc(client, "show program state", code='age = int("abc")\n')
    assert "could not finish tracing" in data["speech"]
    assert "ValueError" in data["speech"]
    assert "explain error" in data["speech"]


def test_infinite_loop_protection(client, monkeypatch):
    # Keep the safety test fast: shrink the sandbox wall timeout. The subprocess
    # is still really killed; we never hang and never run code in-process.
    monkeypatch.setattr(app_module, "SUBPROCESS_WALL_TIMEOUT_SECONDS", 2)
    data = vc(client, "step through this", code="x = 0\nwhile True:\n    x = x + 1\n")
    assert data["action"] == "deterministic_message"
    assert "ran too long" in data["speech"] or "infinite loop" in data["speech"]


def test_state_watch_does_not_call_ai(client, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("AI provider called for deterministic state watch")

    monkeypatch.setattr(app_module, "call_gemini", fail)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail)
    assert vc(client, "what variables exist", code=FLOW1)["success"] is not False
    assert vc(client, "show program state", code=FLOW1)["success"] is not False
