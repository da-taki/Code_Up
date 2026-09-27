"""Context-aware structural code edits.

Real bug: after "insert loop run 5" created a loop, "in loop print hello
each time" was understood as a conversational edit but the print was
appended AFTER the loop. The edit layer knew WHAT to add but not WHERE: the
AI mapper (and the plain print path) reduced the request to "insert a print"
and the template result was applied with append_code.

codeup/commands/structural_edit.py now grounds location words in the
program's AST, and every result is re-parsed to check the new statement sits
in the requested body. All tests run through the real /voice-command path.
"""

import ast
import json

import pytest

import app as app_module
from codeup.commands import structural_edit

LOOP6 = "for i in range(6):\n    print(i)\n"
TWO_LOOPS = "for s in students:\n    print(s)\nfor t in subjects:\n    print(t)\n"
SHARE_WORDS = ("share your code", "paste", "send your code")


def _vc(client, text, code, **extra):
    return client.post("/voice-command", json={"text": text, "code": code, **extra}).get_json()


def _code(data):
    return (data.get("ai_action") or {}).get("code") or ""


def _body_sources(code, header_prefix, field="body"):
    """Source of each statement in the body of the first block whose header starts with header_prefix."""
    tree = ast.parse(code)
    lines = code.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and lines[node.lineno - 1].strip().startswith(header_prefix):
            return [ast.get_source_segment(code, stmt) for stmt in getattr(node, field)]
    raise AssertionError(f"no block starting with {header_prefix!r}")


def _top_level(code):
    return [ast.get_source_segment(code, stmt) for stmt in ast.parse(code).body]


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


# ---- the real bug -------------------------------------------------------------------------

def test_real_bug_acceptance_print_goes_inside_the_loop(offline):
    data = _vc(offline, "in loop print hello each time", LOOP6)
    code = _code(data)
    assert data["action"] == "conversational_edit"
    assert code.startswith('for i in range(6):\n    print(i)\n    print("Hello")')
    assert _body_sources(code, "for i in range(6)") == ["print(i)", 'print("Hello")']
    assert _top_level(code) == [code.strip()], "nothing appended after the loop"
    assert 'print(i)\nprint("Hello")' not in code


@pytest.mark.parametrize("phrase", [
    "in loop print hello each time", "every iteration print hello", "each time print hello",
    "inside the loop print hello", "in this loop print hello", "each time through the loop print hello",
    "add print hello inside the loop",
    # broken English and Hinglish
    "acha loop me print hello har baar", "loop ke andar hello print karo", "har baar hello print karo",
])
def test_loop_body_phrasings(offline, phrase):
    code = _code(_vc(offline, phrase, LOOP6))
    assert _body_sources(code, "for i in range(6)") == ["print(i)", 'print("Hello")'], phrase


def test_variable_inside_loop_and_literal_inside_loop(offline):
    base = "score = 90\nfor i in range(3):\n    print(i)\n"
    assert _body_sources(_code(_vc(offline, "in loop print the variable score", base)), "for i")[-1] == "print(score)"
    assert _body_sources(_code(_vc(offline, "in the loop print score", base)), "for i")[-1] == "print(score)"
    assert _body_sources(_code(_vc(offline, "in the loop print good morning", base)), "for i")[-1] == \
        'print("Good morning")'


# ---- other blocks ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase,code,expected", [
    ("inside the if print congratulations", 'if score >= 40:\n    print("Pass")\n',
     'if score >= 40:\n    print("Pass")\n    print("Congratulations")\n'),
    ("in the else print try again", 'if score >= 40:\n    print("Pass")\nelse:\n    print("Fail")\n',
     'if score >= 40:\n    print("Pass")\nelse:\n    print("Fail")\n    print("Try again")\n'),
    ("inside the function print welcome", 'def greet():\n    print("Hello")\n',
     'def greet():\n    print("Hello")\n    print("Welcome")\n'),
    ("before return print calculating", "def square(x):\n    return x * x\n",
     'def square(x):\n    print("Calculating")\n    return x * x\n'),
    ("after the loop print done", "for i in range(5):\n    print(i)\n",
     'for i in range(5):\n    print(i)\n\nprint("Done")\n'),
    ("inside the while loop print count", "count = 0\nwhile count < 5:\n    count += 1\n",
     "count = 0\nwhile count < 5:\n    count += 1\n    print(count)\n"),
    ("before the loop print start", "for i in range(2):\n    print(i)\n",
     'print("Start")\nfor i in range(2):\n    print(i)\n'),
    ("at the start of the function print begin", 'def greet():\n    print("Hello")\n',
     'def greet():\n    print("Begin")\n    print("Hello")\n'),
    ("in the elif print medium", 'if m > 80:\n    g = "A"\nelif m > 60:\n    g = "B"\nelse:\n    g = "C"\n',
     'if m > 80:\n    g = "A"\nelif m > 60:\n    g = "B"\n    print("Medium")\nelse:\n    g = "C"\n'),
    ("after the input print thanks", 'name = input("Name: ")\nprint(name)\n',
     'name = input("Name: ")\nprint("Thanks")\nprint(name)\n'),
    ("under the condition print well done", 'if ok:\n    x = 1\n', 'if ok:\n    x = 1\n    print("Well done")\n'),
    ("inside the loop increase total by 1", "total = 0\nfor i in range(3):\n    print(i)\n",
     "total = 0\nfor i in range(3):\n    print(i)\n    total += 1\n"),
])
def test_block_placements(offline, phrase, code, expected):
    data = _vc(offline, phrase, code)
    assert data["action"] == "conversational_edit", data
    assert _code(data) == expected.rstrip("\n") or _code(data) == expected
    ast.parse(_code(data))  # indentation valid


def test_nested_loop_in_function_keeps_unrelated_code(offline):
    code = "total = 0\ndef report():\n    for i in range(2):\n        print(i)\n    return total\nprint(report())\n"
    new = _code(_vc(offline, "inside the loop print hello", code))
    assert new.splitlines() == ["total = 0", "def report():", "    for i in range(2):", "        print(i)",
                                '        print("Hello")', "    return total", "print(report())"]


# ---- context and ambiguity -----------------------------------------------------------------

def test_two_loops_are_genuinely_ambiguous_and_ask_one_question(offline):
    data = _vc(offline, "in the loop print hello", TWO_LOOPS)
    assert data["action"] == "clarify"
    assert data["speech"] == ("Which loop do you mean: the loop over students (line 1) or "
                              "the loop over subjects (line 3)?")


@pytest.mark.parametrize("answer,header", [("the second one", "for t in subjects"), ("first", "for s in students"),
                                           ("subjects", "for t in subjects"), ("line 3", "for t in subjects"),
                                           ("dusra wala", "for t in subjects")])
def test_answer_to_which_loop_completes_the_edit(offline, answer, header):
    _vc(offline, "in the loop print hello", TWO_LOOPS)
    code = _code(_vc(offline, answer, TWO_LOOPS))
    assert _body_sources(code, header)[-1] == 'print("Hello")'


def test_unrelated_reply_does_not_trap_the_learner(offline):
    _vc(offline, "in the loop print hello", TWO_LOOPS)
    _vc(offline, "banana", TWO_LOOPS)
    # the question was dropped: a later "first" is not taken as its answer
    assert _vc(offline, "first", TWO_LOOPS).get("source") != "structural_edit"


def test_cursor_inside_a_loop_resolves_it(offline):
    code = _code(_vc(offline, "in the loop print hello", TWO_LOOPS, cursor_line=4))
    assert _body_sources(code, "for t in subjects")[-1] == 'print("Hello")'


def test_recently_created_loop_resolves_the_loop(offline):
    first = _code(_vc(offline, "insert a for loop that prints hello 3 times", TWO_LOOPS))
    program = TWO_LOOPS + first + "\n"
    code = _code(_vc(offline, "in the loop print bye", program))
    assert _body_sources(code, "for i in range(3)")[-1] == 'print("Bye")'
    assert _body_sources(code, "for s in students") == ["print(s)"]


# ---- AI paths ------------------------------------------------------------------------------

class StructuralModel:
    """Mapper, planner and semantic answers like the live model; `appends`
    reproduces the original bug (planner puts the line after the loop)."""

    def __init__(self, appends=False, fail=False):
        self.appends, self.fail, self.calls = appends, fail, []

    def __call__(self, system, user):
        if self.fail:
            raise RuntimeError("provider outage")
        if "Student command:" in user:
            command = user.split("Student command:", 1)[1].splitlines()[0].strip().lower()
            self.calls.append(("mapper", command))
            if "run 5" in command:
                return json.dumps({"intent": "insert_for_loop", "confidence": 0.95,
                                   "slots": {"start": 0, "stop": 5}, "reason": "loop"})
            if "hello" in command:
                return json.dumps({"intent": "insert_print_statement", "confidence": 0.95,
                                   "slots": {"text": "hello"}, "reason": "print"})
            return json.dumps({"intent": "unknown_clarify", "confidence": 0.3, "slots": {}, "reason": "?"})
        if "User edit instruction:" in user:
            self.calls.append(("planner", "for i in range(6)" in user))
            if "for i in range(6):" not in user:
                return json.dumps({"action": "ask_clarification", "confidence": 0.9,
                                   "clarification_question": "Please share your code with me."})
            updated = (LOOP6 + 'print("Hello")\n') if self.appends else \
                LOOP6.replace("    print(i)\n", '    print(i)\n    print("Hello")\n')
            return json.dumps({"action": "replace_current_code", "confidence": 0.95, "updated_code": updated,
                               "summary": "Added the greeting.", "needs_clarification": False,
                               "clarification_question": "", "safety_notes": []})
        if "Utterance:" in user:
            self.calls.append(("semantic", ""))
            return json.dumps({"intent": "EDIT_CODE", "parameters": {}, "confidence": 0.93,
                               "needs_clarification": False, "clarification_question": None, "choices": []})
        return "{}"


def _ai(monkeypatch, model):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", model)
    monkeypatch.setattr(app_module, "_structured_ai_available", lambda: True)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_original_conversation_through_the_production_path(monkeypatch):
    client = _ai(monkeypatch, StructuralModel())
    # Turn 1 is a COUNT request: no clarification, exactly 5 iterations
    # (see tests/test_loop_count_generation.py).
    turn1 = _vc(client, "insert loop run 5", "")
    created = _code(turn1)
    assert created == "for i in range(5):\n    print(i)"
    turn2 = _vc(client, "in loop print hello each time", created + "\n")
    code = _code(turn2)
    assert _body_sources(code, "for i in range(5)") == ["print(i)", 'print("Hello")']
    assert 'print(i)\nprint("Hello")' not in code


def test_turn_one_created_loop_is_the_referent_among_several(offline):
    created = _code(_vc(offline, "loop from 0 to 5", TWO_LOOPS))
    program = TWO_LOOPS + created + "\n"
    code = _code(_vc(offline, "in loop print hello each time", program))
    assert _body_sources(code, "for i in range(6)") == ["print(i)", 'print("Hello")']
    assert _body_sources(code, "for s in students") == ["print(s)"]


def test_ai_mapped_print_with_a_location_is_not_appended(monkeypatch):
    # The live model maps "inside the loop print hello" to insert_print_statement.
    client = _ai(monkeypatch, StructuralModel())
    code = _code(_vc(client, "inside the loop print hello", LOOP6))
    assert _body_sources(code, "for i in range(6)") == ["print(i)", 'print("Hello")']


def test_semantic_free_form_structural_edit(monkeypatch):
    model = StructuralModel()
    client = _ai(monkeypatch, model)
    utterance = "bhai jo loop hai usme hello bhi dikhao"
    assert structural_edit.split_request(utterance) is None, "not handled by the deterministic parser"
    data = _vc(client, utterance, LOOP6)
    assert _body_sources(_code(data), "for i in range(6)") == ["print(i)", 'print("Hello")']
    assert ("planner", True) in model.calls, "the planner received the current code"
    assert not any(w in (data.get("speech") or "").lower() for w in SHARE_WORDS)


def test_ai_edit_that_misplaces_the_line_is_rejected(monkeypatch):
    client = _ai(monkeypatch, StructuralModel(appends=True))
    data = _vc(client, "bhai jo loop hai usme hello bhi dikhao", LOOP6)
    assert data["action"] == "clarify"
    assert "inside the loop" in data["speech"]
    assert not _code(data)


def test_provider_outage_keeps_deterministic_structural_edits(monkeypatch):
    client = _ai(monkeypatch, StructuralModel(fail=True))
    code = _code(_vc(client, "in loop print hello each time", LOOP6))
    assert _body_sources(code, "for i in range(6)") == ["print(i)", 'print("Hello")']
    data = _vc(client, "bhai jo loop hai usme hello bhi dikhao", LOOP6)
    assert data["success"] is True and not _code(data)


def test_unbuildable_statement_uses_ai_but_must_land_in_the_block(monkeypatch):
    model = StructuralModel()
    client = _ai(monkeypatch, model)
    data = _vc(client, "inside the loop add a friendly hello message", LOOP6)
    assert _body_sources(_code(data), "for i in range(6)") == ["print(i)", 'print("Hello")']
    assert ("planner", True) in model.calls


# ---- guards --------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["what is in the loop", "explain the loop", "read the function",
                                  "print hello in a loop", "go to the loop"])
def test_questions_and_generation_are_not_structural(text):
    assert structural_edit.split_request(text) is None


def test_no_structure_asks_instead_of_appending(offline):
    data = _vc(offline, "inside the loop print hello", "x = 1\n")
    assert data["action"] == "clarify" and "could not find a loop" in data["speech"]


def test_literal_vs_variable_fix_still_holds(offline):
    assert _code(_vc(offline, "add print hello", "marks = 80\n")) == 'print("Hello")'
    assert _code(_vc(offline, "print score", "score = 95\n")) == "print(score)"


def test_structural_edits_respect_classroom_policy(offline, monkeypatch):
    monkeypatch.setattr(app_module, "_ai_capability_check",
                        lambda cap: (False, {}, "Code changes are off.") if cap == "generate" else (True, {}, ""))
    data = _vc(offline, "in loop print hello each time", LOOP6)
    assert data["policy_blocked"] is True and not _code(data)


def test_verify_rejects_a_line_outside_the_block():
    tree = ast.parse(LOOP6)
    target = structural_edit.candidates(tree, LOOP6, structural_edit.Location("loop", "inside"))[0]
    appended = LOOP6 + 'print("Hello")\n'
    assert not structural_edit.verify(appended, target, structural_edit.Location("loop", "inside"), 3, 1)
    assert not structural_edit.added_statements_within(LOOP6, appended, target)
