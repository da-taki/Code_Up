"""Natural loop COUNT generation ("run 5 times") vs RANGE ("from 0 to 5").

Root causes fixed:
  * the beginner loop matcher only understood "N numbers" counts, so
    "insert loop run 5" fell through to "I heard a loop command but not
    clearly";
  * the AI mapper's loop template ignored its "count" slot and read "stop"
    as an inclusive end value, so a count of 5 became range(6);
  * "insert a loop that runs 6 times" fell to parse_intent's append_line,
    which typed the words themselves into the editor as a "line of code".
A count now means exactly that many iterations; "from X to Y" keeps its
inclusive range meaning. All tests go through /voice-command.
"""

import json

import pytest

import app as app_module
from codeup.commands import beginner_templates

LOOP_WORDS_AS_CODE = ("a loop that", "loop run", "times")


def _vc(client, text, code="", **extra):
    return client.post("/voice-command", json={"text": text, "code": code, **extra}).get_json()


def _code(data):
    return (data.get("ai_action") or {}).get("code") or ""


def _values(code):
    printed = []
    exec(compile(code, "<loop>", "exec"), {"print": lambda *a: printed.append(a[0] if len(a) == 1 else a)})
    return printed


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


# ---- counts: exactly N iterations, no clarification --------------------------------------

@pytest.mark.parametrize("phrase,count", [
    ("insert loop run 5", 5), ("add loop run 5", 5), ("make loop run 5 times", 5),
    ("insert a loop that runs 5 times", 5), ("insert a loop that runs 6 times", 6),
    ("loop run 5", 5), ("add loop 5 times", 5), ("make loop 5 time", 5), ("loop five times", 5),
    ("acha loop run 4", 4), ("loop 5 baar chalao", 5), ("5 baar loop chala do", 5),
    ("insert a loop that runs 1 time", 1), ("loop 100 times", 100),
])
def test_count_requests_run_exactly_n_times(client, phrase, count):
    data = _vc(client, phrase)
    assert data["action"] == "conversational_edit", data
    assert data.get("needs_clarification") is not True
    code = _code(data)
    assert code == f"for i in range({count}):\n    print(i)"
    assert _values(code) == list(range(count)), "a count is iterations, not an inclusive end"


def test_acceptance_insert_loop_run_5(client):
    data = _vc(client, "insert loop run 5")
    assert _code(data) == "for i in range(5):\n    print(i)"
    assert "5 times" in data["speech"]


def test_acceptance_six_times_is_real_code(client):
    data = _vc(client, "insert a loop that runs 6 times")
    assert data["action"] != "append_line"
    code = _code(data)
    assert code.strip() and not any(w in code for w in LOOP_WORDS_AS_CODE)
    assert len(_values(code)) == 6


def test_make_loop_run_5_times_never_runs_the_program(client):
    assert _vc(client, "make loop run 5 times")["action"] == "conversational_edit"


# ---- ranges keep their inclusive meaning --------------------------------------------------

@pytest.mark.parametrize("phrase,values", [
    ("loop from 0 to 5", [0, 1, 2, 3, 4, 5]),
    ("loop from 1 to 5", [1, 2, 3, 4, 5]),
    ("loop from 1 to 10", list(range(1, 11))),
    ("count from 3 to 7", [3, 4, 5, 6, 7]),
    ("iterate from 2 through 6", [2, 3, 4, 5, 6]),
])
def test_range_requests_include_both_ends(client, phrase, values):
    assert _values(_code(_vc(client, phrase))) == values


def test_count_and_range_are_different(client):
    count = _values(_code(_vc(client, "loop 5 times")))
    rng = _values(_code(_vc(client, "loop from 0 to 5")))
    assert len(count) == 5 and len(rng) == 6


# ---- zero, negative, too large --------------------------------------------------------------

@pytest.mark.parametrize("phrase,words", [
    ("run 0 times", "0 times never runs"), ("insert loop run 0", "0 times never runs"),
    ("run minus 3 times", "negative"), ("run -2 times", "negative"),
    ("insert a loop that runs 1000 times", "beginner limit of 100"),
])
def test_invalid_counts_explain_and_add_nothing(client, phrase, words):
    data = _vc(client, phrase)
    assert data["action"] == "clarify" and words in data["speech"]
    assert not _code(data)
    assert data["action"] not in {"run", "run_project_file"}


@pytest.mark.parametrize("phrase", [
    "insert a loop that runs 6 times", "insert a loop that goes around and around",
    "append a loop that counts sheep", "insert loop run 5", "add loop 5 times",
])
def test_no_loop_description_is_typed_in_as_code(client, phrase):
    data = _vc(client, phrase)
    assert data["action"] != "append_line"
    code = _code(data)
    if code:
        compile(code, "<loop>", "exec")
    else:
        assert data["action"] == "clarify"


def test_dictated_code_lines_still_append(client):
    assert _vc(client, "insert x equals 5")["action"] == "append_line"


# ---- "repeat this N times" uses the current code -------------------------------------------

def test_repeat_this_wraps_the_current_program(client):
    data = _vc(client, "repeat this 3 times", code='print("Hi")\n')
    assert _code(data) == 'for i in range(3):\n    print("Hi")'


def test_hinglish_repeat_this_wraps_the_current_program(client):
    data = _vc(client, "isko 5 baar repeat karo", code='print("Hi")\n')
    assert _code(data) == 'for i in range(5):\n    print("Hi")'


def test_repeat_this_with_nothing_to_repeat_asks(client):
    data = _vc(client, "repeat this 5 times", code="")
    assert data["action"] == "clarify" and "What should repeat 5 times" in data["speech"]


def test_repeat_this_does_not_wrap_function_definitions(client):
    data = _vc(client, "repeat this 2 times", code="def f():\n    return 1\nprint(f())\n")
    assert data["action"] == "clarify" and "Which part" in data["speech"]


def test_count_loop_is_added_to_existing_code(client):
    data = _vc(client, "insert loop run 5", code="marks = 80\n")
    assert data["ai_action"]["action"] == "append_code"


# ---- AI mapper: count slot vs range slots ---------------------------------------------------

def test_mapper_count_slot_is_iterations_and_stop_is_inclusive():
    assert beginner_templates.build_from_mapping("insert_for_loop", {"count": 5}).code == \
        "for i in range(5):\n    print(i)"
    assert beginner_templates.build_from_mapping("insert_for_loop", {"start": 0, "stop": 5}).code == \
        "for i in range(6):\n    print(i)"
    assert beginner_templates.build_from_mapping("insert_for_loop", {"count": 0}).code == ""


class LoopModel:
    def __init__(self, fail=False):
        self.fail, self.prompts = fail, []

    def __call__(self, system, user):
        if self.fail:
            raise RuntimeError("provider outage")
        if "Student command:" in user:
            self.prompts.append(system + user)
            return json.dumps({"intent": "insert_for_loop", "confidence": 0.95, "slots": {"count": 5},
                               "reason": "loop five times"})
        if "Utterance:" in user:
            self.prompts.append("semantic")
            # a real model answers the free-form request with a generation intent
            return json.dumps({"intent": "GENERATE_CODE", "parameters": {"description": "a loop that runs 5 times"},
                               "confidence": 0.93, "needs_clarification": False,
                               "clarification_question": None, "choices": []})
        return "{}"


def _ai(monkeypatch, model):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", model)
    monkeypatch.setattr(app_module, "_structured_ai_available", lambda: True)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_semantic_free_form_count_request(monkeypatch):
    model = LoopModel()
    client = _ai(monkeypatch, model)
    data = _vc(client, "bhai mujhe aisa kuch chahiye jo paanch dafa ghoome")   # no loop word: semantic AI
    assert "semantic" in model.prompts
    assert _code(data) == "for i in range(5):\n    print(i)"


def test_mapper_prompt_defines_count_versus_range():
    from codeup.commands import natural_command_mapper
    system, _user = natural_command_mapper.mapper_messages("loop 5 times")
    assert "count is a number of iterations" in system and "Never put a repeat count in stop" in system


@pytest.mark.parametrize("phrase,count", [("loop chalao paanch baar", 5), ("teen baar loop chala do", 3)])
def test_hindi_number_words(client, phrase, count):
    assert _values(_code(_vc(client, phrase))) == list(range(count))


def test_provider_outage_keeps_deterministic_counts(monkeypatch):
    client = _ai(monkeypatch, LoopModel(fail=True))
    assert _code(_vc(client, "insert loop run 5")) == "for i in range(5):\n    print(i)"
    assert _code(_vc(client, "loop 5 baar chalao")) == "for i in range(5):\n    print(i)"


# ---- earlier fixes still hold ----------------------------------------------------------------

def test_two_turn_count_loop_then_structural_edit(client):
    created = _code(_vc(client, "insert loop run 5"))
    assert created == "for i in range(5):\n    print(i)"
    code = _code(_vc(client, "in loop print hello each time", code=created + "\n"))
    assert code.startswith('for i in range(5):\n    print(i)\n    print("Hello")')
    assert 'print(i)\nprint("Hello")' not in code


def test_literal_vs_variable_still_intact(client):
    assert _code(_vc(client, "add print hello", code="marks = 80\n")) == 'print("Hello")'
    assert _code(_vc(client, "print score", code="score = 95\n")) == "print(score)"


def test_count_loops_respect_classroom_policy(client, monkeypatch):
    monkeypatch.setattr(app_module, "_ai_capability_check",
                        lambda cap: (False, {}, "Code generation is off.") if cap == "generate" else (True, {}, ""))
    data = _vc(client, "insert loop run 5")
    assert data["policy_blocked"] is True and not _code(data)


@pytest.mark.parametrize("phrase", ["bhai abhi jo maine likha hua hai usko ek baar chala ke dikha",
                                    "run this once", "run it 1 time"])
def test_run_once_is_not_a_loop(client, phrase):
    from codeup.commands import intent_repair
    assert intent_repair.parse_loop_count(phrase) is None
    assert _vc(client, phrase, code='print("Hi")\n').get("source") != "loop_count"
