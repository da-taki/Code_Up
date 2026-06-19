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



class TestConceptQuestionsNeverRun:

    @pytest.mark.parametrize("text", [
        "what is recursion", "what is inheritance", "what is a tuple",
        "what are decorators", "what is object oriented programming",
        "who are you", "what time is it",
    ])
    def test_question_does_not_route_to_run(self, client, text):
        assert _action(client, text) != "run"

    def test_concierge_ignores_bare_is_sentence_on_inputless_code(self):
        assert ic.build_input_plan(LOOP, "what is recursion") is None

    def test_concierge_still_runs_explicit_value_command_without_input(self):
        plan = ic.build_input_plan('print("hi")\n', "run with name Taknoor and age 16")
        assert plan["status"] == "no_input"

    def test_concierge_still_handles_real_inputs(self):
        plan = ic.build_input_plan(NAME_AGE, "run with name Taknoor and age 16")
        assert plan["status"] == "ready"
        assert plan["values"] == ["Taknoor", "16"]



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
        assert _action(client, text, code="") == "generate_code"

    @pytest.mark.parametrize("text", [
        "insert a function called greet",
        "create a function called add",
    ])
    def test_named_function_inserts_are_preserved(self, client, text):
        assert _action(client, text, code="") == "insert_function"



class TestNonCodeAndUnsupportedAreSafe:

    @pytest.mark.parametrize("text", [
        "who are you", "what is your name", "what are you", "what time is it",
        "what day is it", "are you working", "how are you",
        "what is flarbology", "explain flarbology", "teach me flarbology",
    ])
    def test_safe_deterministic_not_run_not_fuzzy(self, client, text):
        d = client.post("/voice-command", json={"text": text, "code": LOOP}).get_json()
        assert d["action"] == "deterministic_message", (text, d["action"])
        assert d["action"] != "run"
        assert "options" not in d  # not a fuzzy confirm
        msg = (d.get("message") or "").lower()
        assert msg  # meaningful, spoken text
        for junk in ("did you mean", "locate error", "read line enhanced"):
            assert junk not in msg, (text, junk)
