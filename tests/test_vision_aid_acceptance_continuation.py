"""Vision-Aid acceptance audit, continuation pass.

Server-side regressions for the defects found while finishing the audit, plus
acceptance paths that were verified but not yet pinned by a test. All run
through the real /voice-command, /generate-code, /fix and /run routes.

Browser-level acceptance lives in test_expanded_curriculum_browser.py,
test_instructor_ux_browser.py and test_semantic_run_browser.py.
"""

import pytest

import app as app_module
from codeup.accessibility.speech_output import python_code_to_speech
from codeup.commands import beginner_templates
from codeup.learning import guided_learning
from test_semantic_intent_and_clarification import FakeModel

CODE = "marks = 80\nprint(marks)\n"


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    fake = FakeModel()
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fake)
    monkeypatch.setattr(app_module, "_structured_ai_available", lambda: True)
    app_module.app.config["TESTING"] = True
    return fake


@pytest.fixture
def client(model):
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, code=CODE):
    return client.post("/voice-command", json={"text": text, "code": code}).get_json()


# ---- code generation: the school-marks example works without an AI provider ----

def test_clear_school_marks_request_generates_immediately_offline(client):
    data = _vc(client, "make a school marks program", code="")
    assert data["action"] == "conversational_edit"
    assert data.get("needs_clarification") is not True
    code = data["ai_action"]["code"]
    assert "input(" in code and "average" in code and "if average >= 40" in code
    compile(code, "<generated>", "exec")


def test_vague_request_then_answer_produces_a_school_marks_program_offline(client):
    ask = _vc(client, "make a program", code="")
    assert ask["action"] == "clarify" and "program" in ask["speech"].lower()
    done = _vc(client, "school marks", code="")
    assert done["action"] == "generate_code" and done["resolved_from_clarification"] is True
    generated = client.post("/generate-code", json={"prompt": done["prompt"]}).get_json()
    assert generated["success"] is True and generated["source"] == "local_fallback"
    assert "marks" in generated["code"]


@pytest.mark.parametrize("text,template_id", [
    ("make a school marks program", "generate_school_marks_program"),
    ("create a report card program", "generate_school_marks_program"),
    ("make a marks average program", "generate_marks_average_program"),
    ("make a marks percentage calculator", "generate_percentage_program"),
    ("make a calculator", "generate_calculator_program"),
])
def test_generation_templates_do_not_shadow_each_other(text, template_id):
    assert beginner_templates.make_generation_program(text).intent == template_id


# ---- semantic fallback + clarification ------------------------------------------------

def test_provider_outage_english_paraphrase_falls_back_to_deterministic_repair(client, model):
    model.fail = True
    data = _vc(client, "uh could you execute whatever I've currently written for me")
    assert data["action"] == "run"
    assert (data.get("semantic") or {}).get("source") != "semantic_ai"


def test_hinglish_reply_resolves_do_that_again_clarification(client, model):
    client.post("/run", json={"code": CODE})
    assert _vc(client, "add comments")["action"] == "conversational_edit"
    ask = _vc(client, "do that again")
    assert ask["speech"] == "Do you want me to run the code or repeat the last change?"
    done = _vc(client, "chala do")
    assert done["action"] == "run"
    assert done["semantic"]["source"] == "semantic_clarification"
    assert done["semantic"]["resolved"] == "RUN_CODE"
    # cleared after resolution
    assert (_vc(client, "yes").get("semantic") or {}).get("source") != "semantic_clarification"


# ---- guided learning phrasing ----------------------------------------------------------

@pytest.mark.parametrize("text,path_id", [
    ("start guided loops", "loops"),
    ("begin guided functions", "functions"),
    ("start guided school marks", "project_marks"),
    ("start guided learning loops", "loops"),
])
def test_start_guided_topic_phrasings(client, text, path_id):
    data = _vc(client, text, code="")
    assert data["guided_command"] == "start"
    assert data["guided"]["path"] == path_id


def test_start_guided_unknown_topic_is_not_captured():
    assert guided_learning.command_kind("open guided tour", active=False) is None
    assert guided_learning.command_kind("start guided learning", active=False) == ("start", "")


# ---- learner adaptation: two histories, one question ----------------------------------

BAD_LOOP = "for i in range(5)\n    print(i)"


def _struggling(client):
    for _ in range(3):
        client.post("/run", json={"code": BAD_LOOP})
    _vc(client, "start guided learning loops", code="")
    for _ in range(2):
        _vc(client, "check my work", BAD_LOOP)
        _vc(client, "give me a hint", BAD_LOOP)
        _vc(client, "another hint", BAD_LOOP)
    _vc(client, "stop guided learning", BAD_LOOP)


def _independent(client):
    _vc(client, "start guided learning loops", code="")
    for code in ("for number in range(5):\n    print(number)",
                 "count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1",
                 "count = 0\nfor number in range(1, 11):\n    if number % 2 == 0:\n        count = count + 1\nprint(count)"):
        assert _vc(client, "check my work", code)["speech"].startswith("Correct.")
    _vc(client, "stop guided learning", code="")


def test_two_learner_histories_change_real_production_answers(model):
    struggling, independent = app_module.app.test_client(), app_module.app.test_client()
    _struggling(struggling)
    _independent(independent)

    a = _vc(struggling, "what is a for loop")["speech"]
    b = _vc(independent, "what is a for loop")["speech"]
    assert a.startswith("Let's go slowly") and "Try changing just one value" in a
    assert b.startswith("You already use this well") and "Beginner note" not in b
    assert len(b) < len(a)

    # "why did you explain it that way" refers to the loop answer just given,
    # not to whichever concept happens to come first in the editor.
    loop_code = "for i in range(3):\n    print(i)"
    assert "needed reinforcement" in _vc(struggling, "why did you explain it that way", loop_code)["speech"]
    assert "demonstrated this topic" in _vc(independent, "why did you explain it that way", loop_code)["speech"]

    # Guided path: smaller first step vs resuming where the learner left off.
    resumed_a = _vc(struggling, "start guided learning loops", code="")["speech"]
    resumed_b = _vc(independent, "start guided learning loops", code="")["speech"]
    assert "smaller steps" in resumed_a and "step 1 of 5" in resumed_a
    assert "step 4 of 5" in resumed_b and "smaller steps" not in resumed_b


# ---- diagnostics: "fix this error" focuses the selected problem ------------------------

def test_fix_selected_diagnostic_focuses_the_ai_prompt(client, monkeypatch):
    prompts = []

    def fake_fix(capability, system, user, **kw):
        prompts.append(user)
        return "```python\nscore = 0\nx = 1\ntotal = 0\nprint(score)\nprint(total)\n```"

    monkeypatch.setattr(app_module, "call_gemini_capability", fake_fix)
    two = "print(score)\nx = 1\nprint(total)\n"
    client.post("/run", json={"code": two})
    assert _vc(client, "next error", two)["line"] == 3
    reply = _vc(client, "fix this error", two)
    assert reply["action"] == "fix" and reply["selected_diagnostic"]["line"] == 3
    client.post("/fix", json={"code": two, "selected_diagnostic": True})
    assert "The learner selected ONE problem to fix" in prompts[-1] and "line: 3" in prompts[-1]
    assert "total" in prompts[-1]
    client.post("/fix", json={"code": two})
    assert "selected ONE problem" not in prompts[-1], "plain fix keeps the whole-program prompt"


# ---- Python-aware speech: remaining forms ----------------------------------------------

@pytest.mark.parametrize("code,spoken", [
    ("print(marks)", "print open parenthesis marks close parenthesis"),
    ('student["name"]', "student open bracket double quote name double quote close bracket"),
    ("name = 'Asha'", "name equals single quote Asha single quote"),
    ("total = add(2, 3)", "total equals add open parenthesis 2 comma 3 close parenthesis"),
    ("return a != b", "return a not equals b"),
])
def test_python_speech_calls_indexing_and_quotes(code, spoken):
    assert python_code_to_speech(code) == spoken


@pytest.mark.parametrize("prose", [
    "Well done! Your code runs, and prints 80.",
    "Correct. Now print marks: use the print function.",
])
def test_prose_punctuation_stays_natural(prose):
    assert app_module.sanitize_speech_text(prose) == prose
