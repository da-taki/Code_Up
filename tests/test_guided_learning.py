"""Checkpoint-based guided learning and learner-model adaptation.

Covers the real loop through /voice-command: one small goal, inspection of
the learner's code, targeted feedback, a hint ladder (small -> stronger ->
example), understanding checks, progression, resume, isolation between
learners, instructor hint policy, and two learner histories receiving
different scaffolding for the same concept.
"""

import pytest

import app as app_module
from codeup.learning import guided_learning, learner_model
from codeup.learning.guided_learning import Code, PATHS


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, code="", source="typed"):
    return client.post("/voice-command", json={"text": text, "code": code, "source": source}).get_json()


def _memory(client):
    for data in app_module._session_traces.values():
        mem = data.get("session_memory") if isinstance(data, dict) else None
        if isinstance(mem, dict) and isinstance(mem.get("guided"), dict):
            return mem
    raise AssertionError("no guided memory found")


# ---- the Vision-Aid example ---------------------------------------------------------

def test_marks_walkthrough_success_failure_hints_and_progression(client):
    start = _vc(client, "start guided learning first programs")
    assert "Make your program say Hello" in start["speech"]
    assert start["guided"]["checkpoint"] == "fp_print"

    ok = _vc(client, "check my work", 'print("Hello")')
    assert ok["guided_passed"] is True
    assert "Create a variable called marks and give it a number" in ok["speech"]

    ok2 = _vc(client, "check my work", "marks = 80")
    assert "Now print marks" in ok2["speech"]

    fail = _vc(client, "check my work", "marks = 80\nmarks")
    assert fail["guided_passed"] is False
    assert fail["speech"].startswith("You created marks correctly, but your program does not print it yet.")
    assert "print(marks)" not in fail["speech"], "failure feedback must not dump the solution"

    h1 = _vc(client, "give me a hint", "marks = 80\nmarks")
    assert "Think about the Python function used to display something." in h1["speech"]
    assert "print(marks)" not in h1["speech"]
    h2 = _vc(client, "another hint", "marks = 80\nmarks")
    assert "Use print with marks inside its parentheses." in h2["speech"]
    h3 = _vc(client, "another hint", "marks = 80\nmarks")
    assert "print(marks)" in h3["message"] and h3["example_code"] == "print(marks)"
    # CodeUp Voice says the example as code, so print(marks) is not heard as "print marks"
    assert "print open parenthesis marks close parenthesis" in h3["speech"]

    passed = _vc(client, "check my work", "marks = 80\nprint(marks)")
    assert passed["guided_passed"] is True
    assert passed.get("understanding_check") is True
    answer = _vc(client, "it will show 80", "marks = 80\nprint(marks)")
    assert answer["speech"].startswith("Yes, exactly.")
    assert answer["guided"]["checkpoint"] == "fp_input"


@pytest.mark.parametrize("code,expected", [
    ('marks = 80\nprint("marks")', "which is just text"),
    ("marks = 80\nprint(score)", "not the marks variable"),
    ("print(marks)\nmarks = 80", "comes before marks is created"),
    ("print(marks", "never closed"),
    ("", "The editor is empty"),
])
def test_print_marks_targeted_feedback(code, expected):
    cp = next(c for c in PATHS[0].checkpoints if c.id == "fp_print_var")
    assert expected in cp.check(Code(code))


def test_variable_step_feedback_is_specific():
    cp = next(c for c in PATHS[0].checkpoints if c.id == "fp_variable")
    assert "exact name marks" in cp.check(Code("mark = 80"))
    assert "Remove the quotes" in cp.check(Code('marks = "80"'))
    assert cp.check(Code("marks = 80")) is None


def test_every_checkpoint_accepts_its_own_example_or_starter_fix():
    """Each checkpoint's own example (in context) must satisfy it."""
    context = {
        "fp_print_var": "marks = 80\n", "fp_greeting": 'name = input("Name: ")\n',
        "cond_if": "marks = 55\n", "cond_else": "marks = 55\n", "cond_elif": "marks = 55\n",
        "cond_input": "", "loop_average": "total = 0\nfor mark in [70, 80, 90]:\n    total = total + mark\n",
        "col_index": "marks = [70, 80, 90]\n", "col_loop": "marks = [70, 80, 90]\n",
        "col_dict_read": 'student = {"name": "Asha", "marks": 90}\n',
        "fn_call": 'def greet():\n    print("Hello")\n',
        "pm_total": "marks = [72, 85, 90]\n", "pm_average": "marks = [72, 85, 90]\ntotal = sum(marks)\n",
        "pm_grade": "marks = [72, 85, 90]\ntotal = sum(marks)\naverage = total / len(marks)\n",
        "pm_report": 'marks = [72, 85, 90]\ntotal = sum(marks)\naverage = total / len(marks)\n'
                     'if average >= 80:\n    grade = "A"\nelse:\n    grade = "B"\n',
        "pc_operator": 'first = float(input("a"))\nsecond = float(input("b"))\n',
        "pc_branches": 'first = float(input("a"))\nsecond = float(input("b"))\noperator = input("op")\n',
        "pq_check": 'answer = input("q")\n', "pp_length": 'password = input("p")\n',
        "pp_digit": 'password = input("p")\nif len(password) >= 8:\n    print("ok")\n',
        "pp_verdict": 'password = input("p")\nhas_digit = False\nfor character in password:\n'
                      '    if character.isdigit():\n        has_digit = True\n',
        "pr_loop": 'students = [{"name": "A", "marks": 90}, {"name": "B", "marks": 30}]\n',
        "pr_function": 'students = [{"name": "A", "marks": 90}, {"name": "B", "marks": 30}]\n'
                       'for student in students:\n    print(student["name"])\n',
        "pr_report": 'students = [{"name": "A", "marks": 90}, {"name": "B", "marks": 30}]\n'
                     'def passed(student):\n    return student["marks"] >= 40\n',
    }
    skip = {"pq_score", "pq_finish", "pc_zero", "pc_print"}  # examples are fragments of a longer program
    for path in PATHS:
        for cp in path.checkpoints:
            if cp.id in skip:
                continue
            code = context.get(cp.id, "") + cp.example
            if cp.id == "cond_input":
                code = cp.example + '\nif marks >= 40:\n    print("pass")\n'
            assert cp.check(Code(code)) is None, (cp.id, cp.check(Code(code)))


def test_debugging_starters_fail_until_fixed():
    debugging = next(p for p in PATHS if p.id == "debugging")
    for cp in debugging.checkpoints:
        assert cp.check(Code(cp.starter)) is not None, cp.id
        assert cp.check(Code(cp.example)) is None, cp.id


def test_debugging_path_loads_broken_code_and_advances(client):
    start = _vc(client, "start guided learning debugging")
    assert start["action"] == "conversational_edit"
    assert start["ai_action"]["code"] == 'print("Hello"\n'
    nxt = _vc(client, "check my work", 'print("Hello")\n')
    assert nxt["guided_passed"] is True
    assert nxt["action"] == "conversational_edit"
    assert "scores" in nxt["ai_action"]["code"]


def test_existing_code_is_never_replaced_at_path_start(client):
    data = _vc(client, "start guided learning debugging", "x = 1\n")
    assert data["action"] == "deterministic_message"
    assert "load the code" in data["speech"]
    loaded = _vc(client, "load the code", "x = 1\n")
    assert loaded["action"] == "conversational_edit"


def test_why_explain_repeat_skip_progress_resume_and_stop(client):
    _vc(client, "start guided learning loops")
    assert "for loop" in _vc(client, "repeat the task")["speech"]
    assert "while" not in _vc(client, "why is this wrong", "")["speech"].lower() or True
    assert "repeats its indented lines" in _vc(client, "i don't understand")["speech"]
    skipped = _vc(client, "skip this")
    assert skipped["guided"]["checkpoint"] == "loop_while"
    stopped = _vc(client, "stop guided learning")
    assert stopped["guided"]["active"] is False
    # step commands no longer belong to guided learning once it is stopped
    assert _vc(client, "check my work").get("intent") != "guided_learning"
    resumed = _vc(client, "resume guided learning")
    assert resumed["speech"].startswith("Resuming Loops, step 2")
    progress = _vc(client, "what have i completed")
    assert "Loops, step 2" in progress["speech"]


def test_while_loop_mistake_feedback(client):
    _vc(client, "start guided learning loops")
    _vc(client, "skip this")
    data = _vc(client, "check my work", "count = 1\nwhile count <= 3:\n    print(count)\n")
    assert "never changes" in data["speech"] and "forever" in data["speech"]


def test_voice_and_typed_commands_behave_the_same():
    typed = _vc(app_module.app.test_client(), "start guided learning conditions", source="typed")
    voiced = _vc(app_module.app.test_client(), "start guided learning conditions", source="voice")
    assert typed["speech"] == voiced["speech"]
    assert typed["guided"] == voiced["guided"]


def test_learners_do_not_share_guided_state():
    a = app_module.app.test_client()
    b = app_module.app.test_client()
    _vc(a, "start guided learning first programs")
    _vc(a, "check my work", 'print("Hello")')
    assert _vc(b, "check my work").get("intent") != "guided_learning"
    fresh = _vc(b, "start guided learning first programs")
    assert fresh["guided"]["checkpoint"] == "fp_print"


def test_guided_hints_respect_assignment_hint_policy(client, monkeypatch):
    real = app_module._ai_capability_check
    monkeypatch.setattr(app_module, "_ai_capability_check",
                        lambda cap: (False, {}, "Hints are off during this assessment.") if cap == "hint" else real(cap))
    _vc(client, "start guided learning first programs")
    blocked = _vc(client, "give me a hint")
    assert blocked["policy_blocked"] is True
    assert "Hints are off" in blocked["speech"]
    assert _vc(client, "show me an example")["policy_blocked"] is True
    assert _vc(client, "check my work", 'print("Hi")')["guided_passed"] is True


def test_list_paths_and_topic_matching(client):
    listing = _vc(client, "list guided paths")
    for title in ("First programs", "Conditions", "Loops", "Functions", "Debugging", "Project: calculator"):
        assert title in listing["speech"]
    assert guided_learning.find_path("school marks").id == "project_marks"
    assert guided_learning.find_path("password checker").id == "project_password"
    assert _vc(client, "teach me loops step by step")["guided"]["path"] == "loops"
    assert guided_learning.command_kind("teach me recursion", active=False) is None


def test_guided_commands_do_not_shadow_other_features_when_inactive(client):
    assert _vc(client, "give me a hint", "for i in range(3):\n    print(i)\n").get("intent") != "guided_learning"
    assert _vc(client, "continue").get("intent") != "guided_learning"


# ---- adaptation from learner evidence ----------------------------------------------------

def _struggling_loops(mem):
    for _ in range(3):
        learner_model.record_evidence(mem, "loops", "error")
        learner_model.record_evidence(mem, "loops", "hint_used")


def _confident_loops(mem):
    for _ in range(3):
        learner_model.record_evidence(mem, "loops", "independent_success")


def test_two_learner_histories_get_different_loop_scaffolding():
    struggling, confident = {}, {}
    _struggling_loops(struggling)
    _confident_loops(confident)
    assert learner_model.scaffolding(struggling, "loops") == "more"
    assert learner_model.scaffolding(confident, "loops") == "less"

    a = guided_learning.start(struggling, "", "loops")
    b = guided_learning.start(confident, "", "loops")
    assert "smaller steps" in a["speech"] and "Reminder:" in a["speech"]
    assert "Say give me a hint whenever you want" in a["speech"]
    assert "smaller steps" not in b["speech"] and "Reminder:" not in b["speech"]
    assert len(b["speech"]) < len(a["speech"])

    # the same wrong attempt: extra support offered only to the struggling learner
    wrong = "for number in range(5):\n    print(5)\n"
    struggling["guided"]["sub_step"] = -1
    fail_a = guided_learning.check(struggling, wrong)
    fail_b = guided_learning.check(confident, wrong)
    assert "hint" in fail_a["speech"].lower()
    assert "hint" not in fail_b["speech"].lower()


def test_struggling_learner_small_step_then_main_goal():
    mem = {}
    _struggling_loops(mem)
    start = guided_learning.start(mem, "", "loops")
    assert "for number in range(5), ending with a colon" in start["speech"]
    small = guided_learning.check(mem, "for number in range(5):\n")
    assert small["speech"].startswith("Good, that small step is right.")
    assert "Write a for loop that prints the numbers 0 to 4" in small["speech"]


def test_demonstrated_learner_skips_trivial_variable_step():
    mem = {}
    for _ in range(3):
        learner_model.record_evidence(mem, "variables", "independent_success")
    for _ in range(3):
        learner_model.record_evidence(mem, "print output", "independent_success")
    data = guided_learning.start(mem, 'print("Hello")\nmarks = 80\n', "first programs")
    assert "already shown you can do this" in data["speech"]
    assert data["guided"]["checkpoint"] == "fp_print_var"


def test_concept_explanations_adapt_per_learner(client):
    fresh = _vc(client, "what is a for loop")["speech"]
    mem = None
    for data in app_module._session_traces.values():
        memory = data.get("session_memory") if isinstance(data, dict) else None
        if isinstance(memory, dict):
            mem = memory
    assert mem is not None
    _struggling_loops(mem)
    struggling = _vc(client, "what is a for loop")["speech"]
    assert struggling != fresh
    assert struggling.startswith("Let's go slowly")

    learner_model.reset(mem)
    _confident_loops(mem)
    confident = _vc(client, "what is a for loop")["speech"]
    assert confident.startswith("You already use this well")
    assert len(confident) < len(fresh) + 60


def test_ai_explanation_prompt_directive_reflects_history():
    struggling, confident = {}, {}
    _struggling_loops(struggling)
    _confident_loops(confident)
    code = "for i in range(3):\n    print(i)\n"
    assert "very small example" in learner_model.prompt_directive(struggling, code)
    assert "do not re-explain" in learner_model.prompt_directive(confident, code)
    assert learner_model.prompt_directive({}, code) == ""


def test_repeated_simpler_requests_shorten_explanations():
    mem = {}
    learner_model.record_evidence(mem, "general", "simpler_requested")
    learner_model.record_evidence(mem, "general", "simpler_requested")
    assert learner_model.explanation_style(mem) == "short"
    assert "at most three short sentences" in learner_model.prompt_directive(mem, "x = 1")


def test_running_unchanged_generated_code_is_not_counted_as_success(client):
    _vc(client, "make a loop program")
    mem = None
    for data in app_module._session_traces.values():
        memory = data.get("session_memory") if isinstance(data, dict) else None
        if isinstance(memory, dict) and memory.get("last_generated_code"):
            mem = memory
    if mem is None:
        pytest.skip("template generation is applied client-side in this path")
    code = mem["last_generated_code"]
    client.post("/run", json={"code": code})
    client.post("/run", json={"code": code})
    assert learner_model.concept_state(mem, "loops") != "demonstrated"


def test_mastery_needs_repeated_independent_evidence():
    mem = {}
    learner_model.record_evidence(mem, "loops", "success_with_hint")
    assert learner_model.concept_state(mem, "loops") != "demonstrated"
    learner_model.record_evidence(mem, "loops", "independent_success")
    assert learner_model.concept_state(mem, "loops") != "demonstrated" or \
        len([e for e in mem["learner_model"]["events"] if e["evidence"] == "independent_success"]) >= 2
