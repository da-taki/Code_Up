"""
Routing-safety guards found during pre-NAB exploratory testing.

1. A concept question ("what is recursion") must NEVER be treated as a value
   command and run the editor code. The input() concierge only honours the
   "just run" shortcut for explicit value commands ("run with ...", "use sample
   values"), never a bare "what is X" / "X is Y" sentence on input-less code.

2. Relative line navigation ("next line" / "previous line") routes to the
   next_line / prev_line actions the frontend already implements, instead of
   being swallowed by the conversational-edit handler as "unknown".
"""
import pytest

import app as app_module
import input_concierge as ic

LOOP = "for i in range(3):\n    print(i)\n"
NAME_AGE = "name = input()\nage = int(input())\nprint(name, age)\n"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _action(client, text, code=LOOP):
    return client.post("/voice-command", json={"text": text, "code": code}).get_json()["action"]


# ---------------------------------------------------------------------------
# 1. Concept questions must not run code
# ---------------------------------------------------------------------------

class TestConceptQuestionsNeverRun:

    @pytest.mark.parametrize("text", [
        "what is recursion", "what is inheritance", "what is a tuple",
        "what are decorators", "what is object oriented programming",
        "who are you", "what time is it",
    ])
    def test_question_does_not_route_to_run(self, client, text):
        assert _action(client, text) != "run"

    def test_concierge_ignores_bare_is_sentence_on_inputless_code(self):
        # "what is recursion" parses an "is" clause but, with no input() in the
        # code and no explicit trigger, must NOT return the run shortcut.
        assert ic.build_input_plan(LOOP, "what is recursion") is None

    def test_concierge_still_runs_explicit_value_command_without_input(self):
        # Explicit "run with ..." on input-less code keeps the req-8 shortcut.
        plan = ic.build_input_plan('print("hi")\n', "run with name Taknoor and age 16")
        assert plan["status"] == "no_input"

    def test_concierge_still_handles_real_inputs(self):
        plan = ic.build_input_plan(NAME_AGE, "run with name Taknoor and age 16")
        assert plan["status"] == "ready"
        assert plan["values"] == ["Taknoor", "16"]


# ---------------------------------------------------------------------------
# 2. Relative line navigation is wired
# ---------------------------------------------------------------------------

class TestLineNavigation:

    @pytest.mark.parametrize("text,expected", [
        ("next line", "next_line"),
        ("previous line", "prev_line"),
        ("go to next line", "next_line"),
        ("go to the previous line", "prev_line"),
        ("read next line", "next_line"),
        ("prior line", "prev_line"),
        ("go up a line", "prev_line"),
        ("go down a line", "next_line"),
    ])
    def test_relative_line_nav_routes(self, client, text, expected):
        assert _action(client, text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("go to line 2", "goto_line"),
        ("read line 1", "read_line"),
        ("go to top", "go_to_top"),
        ("go to bottom", "go_to_bottom"),
        ("delete line 2", "delete_line"),
    ])
    def test_absolute_line_commands_unaffected(self, client, text, expected):
        assert _action(client, text) == expected


# ---------------------------------------------------------------------------
# 3. "write a <adjective> function/class for X" generates code, not literal text
# ---------------------------------------------------------------------------

class TestGenerationRouting:

    @pytest.mark.parametrize("text", [
        "write a recursive function to calculate the factorial of 5 and print the result",
        "write a recursive function for factorial",
        "write a class for a bank account",
        "make a class that models a student",
        "build a function for sorting a list",
        "create a method to compute the average",
        "write a simple recursive function to compute factorial",
    ])
    def test_function_and_class_prompts_generate_code(self, client, text):
        # These used to fall through to append_line and dump the prompt text into
        # the editor as if it were code.
        assert _action(client, text, code="") == "generate_code"

    @pytest.mark.parametrize("text", [
        # "function called X" stays a structural insert, not a generation.
        "insert a function called greet",
        "create a function called add",
    ])
    def test_named_function_inserts_are_preserved(self, client, text):
        assert _action(client, text, code="") == "insert_function"
