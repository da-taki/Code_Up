"""Two real-user bugs, fixed at the root.

1. Spoken words became identifiers: "acha add print hello" -> print(hello).
   Every spoken print form now resolves its argument through one code-aware
   resolver (intent_repair.print_argument_python): words are text, a name is a
   variable only when the learner says "variable" or the program defines it.

2. "Please share your code": edit requests that the AI command mapper tagged
   with a descriptive slot failed slot validation and never reached the edit
   planner (the only step that sees the code); /generate-code never received
   the editor code at all and relayed prose replies as "code". The fake model
   below answers exactly like that whenever the code is missing from its prompt.
"""

import ast
import builtins
import json
import shutil
import subprocess

import pytest

import app as app_module
from codeup.classroom import ide_commands
from codeup.commands import beginner_templates, natural_code_editor, natural_command_mapper
from codeup.commands.intent_repair import print_argument_python

APP_JS = open("static/app.js", encoding="utf-8").read()
PROG = 'maths = 80\nscience = 70\ntotal = maths + science\nprint("Total:", total)\n'


def _vc(client, text, code=""):
    return client.post("/voice-command", json={"text": text, "code": code}).get_json()


def _new_code(data):
    return (data.get("ai_action") or {}).get("code") or ""


def _undefined_names(new_code, existing_code=""):
    defined = set(dir(builtins))
    for node in ast.walk(ast.parse(existing_code + "\n" + new_code)):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
    return {n.id for n in ast.walk(ast.parse(new_code))
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in defined}


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


# ---- 1. literal text vs variables ----------------------------------------------------------

@pytest.mark.parametrize("text,code,expected", [
    ("add print hello", "marks = 80\n", 'print("Hello")'),
    ("acha add print hello", "marks = 80\n", 'print("Hello")'),
    ("print hello", "", 'print("Hello")'),
    ("print good morning", "", 'print("Good morning")'),
    ("display welcome to CodeUp", "", 'print("Welcome to CodeUp")'),
    ("make it say enter your marks", "", 'print("Enter your marks")'),
    ("make it say welcome to CodeUp", "marks = 80\n", 'print("Welcome to CodeUp")'),
    ("add a line that prints congratulations", "marks = 80\n", 'print("Congratulations")'),
    ("print name", "", 'print("Name")'),
])
def test_spoken_words_are_text(offline, text, code, expected):
    data = _vc(offline, text, code)
    assert data["action"] == "conversational_edit"
    assert _new_code(data) == expected
    assert not _undefined_names(_new_code(data), code), "no undefined identifier from ordinary words"


@pytest.mark.parametrize("text,code,expected", [
    ("print the variable score", "score = 95\n", "print(score)"),
    ("print score", "score = 95\n", "print(score)"),
    ("print name", 'name = input("What is your name? ")\n', "print(name)"),
    ("print score plus ten", "score = 95\n", "print(score + 10)"),
])
def test_existing_variables_are_used_when_the_program_defines_them(offline, text, code, expected):
    assert _new_code(_vc(offline, text, code)) == expected


def test_print_hello_is_never_an_identifier_even_when_mapped_by_ai():
    assert beginner_templates.build_from_mapping("insert_print_statement", {"text": "hello"}).code == 'print("Hello")'
    assert beginner_templates.build_from_mapping("insert_print_statement", {"value": "hello"}).code == 'print("Hello")'
    assert beginner_templates.build_from_mapping(
        "insert_print_statement", {"value": "score"}, current_code="score = 95").code == "print(score)"
    assert print_argument_python("score plus ten", "") == '"Score plus ten"'


def test_client_fallback_quotes_undefined_names():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    start = APP_JS.index("function normalizeSpokenCodeExpression(")
    end = APP_JS.index("function normalizeSpokenCodeText(")
    script = "function quotePythonString(s){return JSON.stringify(s);}\n" + APP_JS[start:end]
    # pull in the helpers the fragment depends on
    for fn in ("function isSimplePythonExpression(",):
        if fn not in script:
            i = APP_JS.index(fn)
            script = APP_JS[i:APP_JS.index("\n}\n", i) + 3] + script
    script += r"""
let editor = '';
function getCode() { return editor; }
const a = normalizeSpokenPrintArgument('hello');
editor = 'hello = 1';
const b = normalizeSpokenPrintArgument('hello');
console.log(JSON.stringify([a, b]));
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['"hello"', "hello"]


# ---- 2. edit and generation requests use the current editor code --------------------------

class CodeAwareModel:
    """Answers the mapper, the edit planner and the semantic resolver like a
    real model: a planner prompt WITHOUT the learner's code gets "please share
    your code"; with it, a real edited program."""

    def __init__(self):
        self.planner_prompts = []

    def __call__(self, system, user):
        if "Student command:" in user:
            command = user.split("Student command:", 1)[1].splitlines()[0].strip().lower()
            if "hello" in command and ("print" in command or "line" in command):
                return json.dumps({"intent": "insert_print_statement", "confidence": 0.97,
                                   "slots": {"text": "hello"}, "reason": "print line"})
            if command == "add a loop":
                # what the live model answered: the deterministic template must step in
                return json.dumps({"intent": "unknown_clarify", "confidence": 0.92, "slots": {}, "reason": "?"})
            # the descriptive "text" slot is what used to fail validation
            return json.dumps({"intent": "edit_current_code", "confidence": 0.98,
                               "slots": {"text": command[:60], "kind": "feature"}, "reason": "edit"})
        if "User edit instruction:" in user:
            self.planner_prompts.append(user)
            if "maths = 80" not in user:
                return json.dumps({"action": "ask_clarification", "confidence": 0.9,
                                   "clarification_question": "Please share your code with me."})
            instruction = user.split("User edit instruction:", 1)[1].splitlines()[0]
            updated = PROG + f"# change: {instruction.strip()}\n"
            if "validation" in instruction:
                updated = ("def get_mark(name):\n    while True:\n        try:\n"
                           "            return int(input(name + ': '))\n        except ValueError:\n"
                           "            print('Enter a number')\n\n") + PROG
            return json.dumps({"action": "replace_current_code", "confidence": 0.95,
                               "updated_code": updated, "summary": "Updated your program.",
                               "needs_clarification": False, "clarification_question": "",
                               "safety_notes": []})
        return json.dumps({"intent": "UNKNOWN", "parameters": {}, "confidence": 0.9,
                           "needs_clarification": False, "clarification_question": None, "choices": []})


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    fake = CodeAwareModel()
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fake)
    monkeypatch.setattr(app_module, "_structured_ai_available", lambda: True)
    app_module.app.config["TESTING"] = True
    return fake


SHARE_WORDS = ("share your code", "paste", "send your code", "share the code")


@pytest.mark.parametrize("text", ["make it calculate percentage", "add input validation",
                                  "add error handling", "make it use a dictionary", "add another subject",
                                  "turn this into a function"])
def test_feature_requests_edit_the_current_code(model, text):
    client = app_module.app.test_client()
    data = _vc(client, text, PROG)
    speech = (data.get("speech") or data.get("message") or "").lower()
    assert not any(word in speech for word in SHARE_WORDS), speech
    assert data["action"] == "conversational_edit", data
    assert "maths = 80" in model.planner_prompts[-1], "the planner received the editor code"
    code = _new_code(data)
    assert "maths = 80" in code and "print(\"Total:\", total)" in code, "the learner's program is kept"


@pytest.mark.parametrize("text,expected", [("add comments", "# "), ("add a loop", "for i in range(3):"),
                                           ("add print hello", 'print("Hello")')])
def test_simple_edits_produce_code_with_ai_available(model, text, expected):
    data = _vc(app_module.app.test_client(), text, PROG)
    assert data["action"] == "conversational_edit"
    assert expected in _new_code(data)
    assert not any(word in (data.get("speech") or "").lower() for word in SHARE_WORDS)


def test_free_form_hinglish_print_line_resolves_to_a_text_print(model):
    utterance = "acha isme ek line add kar jo hello print kare"
    assert app_module.parse_intent(utterance).get("intent") in {None, "mentor_chat"}
    data = _vc(app_module.app.test_client(), utterance, PROG)
    assert 'print("Hello")' in _new_code(data)
    assert "print(hello)" not in _new_code(data)


def test_generate_code_receives_the_editor_code_and_never_relays_share_requests(monkeypatch):
    prompts = []

    def fake(capability, system, user, **kw):
        prompts.append((system, user))
        return "Please share your code with me so I can add that."

    monkeypatch.setattr(app_module, "call_gemini_capability", fake)
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    result = client.post("/generate-code", json={"prompt": "add percentage calculation to my marks program",
                                                  "current_code": PROG}).get_json()
    system, user = prompts[0]
    assert "maths = 80" in user and "already provided - do not ask for it" in user
    assert "Never ask the learner to share" in system and "COMPLETE revised program" in system
    assert result["success"] is False
    assert "share" not in result["error"].lower() and "Say it again" in result["error"]
    client.post("/generate-code", json={"prompt": "a quiz about planets", "current_code": ""})
    assert "The learner's editor is empty." in prompts[-1][1]


def test_client_sends_editor_code_with_generation_requests():
    block = APP_JS[APP_JS.index("async function generateCode("):]
    block = block[:block.index("\n}\n")]
    assert "current_code: getCode()" in block


def test_planner_prompt_states_the_contract_and_rejects_share_requests():
    system, user = natural_code_editor.planner_messages(current_code=PROG, edit_instruction="add comments")
    assert "never ask them to share" in system and "maths = 80" in user
    ok, reason, _ = natural_code_editor.validate_plan(
        {"action": "ask_clarification", "confidence": 0.9, "clarification_question": "Can you paste your program?"})
    assert (ok, reason) == (False, "asked_for_supplied_code")
    ok, _reason, _ = natural_code_editor.validate_plan(
        {"action": "ask_clarification", "confidence": 0.9, "clarification_question": "Which feature should I add?"})
    assert ok, "a real question about the change is still allowed"


def test_edit_mapping_with_descriptive_slots_is_kept_but_other_intents_stay_strict():
    edit = {"intent": "edit_current_code", "confidence": 0.99,
            "slots": {"text": "calculate percentage", "kind": "feature", "row": 500}, "reason": "x"}
    pruned = natural_command_mapper._prune_edit_slots(edit)
    assert natural_command_mapper.validate_mapping(pruned) == (True, "")
    assert pruned["slots"] == {"kind": "feature"}
    blocked = {"intent": "edit_current_code", "confidence": 0.99, "slots": {"code": "import os"}, "reason": "x"}
    assert natural_command_mapper.validate_mapping(natural_command_mapper._prune_edit_slots(blocked))[0] is False
    strict = {"intent": "insert_print_statement", "confidence": 0.9, "slots": {"bogus": "x"}}
    assert natural_command_mapper.validate_mapping(natural_command_mapper._prune_edit_slots(strict))[0] is False


def test_input_validation_loop_with_return_is_not_an_infinite_loop():
    ok_code = "def ask():\n    while True:\n        try:\n            return int(input())\n        except ValueError:\n            pass\n"
    assert natural_code_editor._code_safety_error(ok_code) == ""
    assert natural_code_editor._code_safety_error("while True:\n    print(1)\n") == "obvious_infinite_loop"


def test_turn_this_into_a_function_is_not_submit_assignment():
    assert ide_commands.match("turn this into a function") is None
    assert ide_commands.match("turn this in") == ("submit_assignment", {})
    assert ide_commands.match("please turn this in") == ("submit_assignment", {})


def test_vague_feature_request_still_asks_one_question(model):
    # clarification is for an unclear CHANGE, not for missing code
    data = _vc(app_module.app.test_client(), "make a program", "")
    assert data["action"] == "clarify" and "program" in data["speech"].lower()
