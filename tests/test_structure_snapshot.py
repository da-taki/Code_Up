import pytest

import app as app_module
from codeup.projects import structure_tools


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


GREET = 'def greet(name):\n    print("Hello", name)\n\nfor i in range(3):\n    greet(i)\n'


class TestSnapshotBuilder:

    def test_empty_code(self):
        snap = structure_tools.build_structure_snapshot("")
        assert snap["has_code"] is False
        assert snap["summary"] == "There is no code to summarize yet."

    def test_function_loop_print(self):
        snap = structure_tools.build_structure_snapshot(GREET)
        s = snap["summary"].lower()
        assert "function" in s and "greet" in s
        assert "loop" in s
        assert "print" in s
        assert snap["counts"]["functions"] == 1
        assert snap["counts"]["loops"] == 1
        assert snap["counts"]["prints"] == 1

    def test_conditions_and_nesting(self):
        code = "for i in range(5):\n    if i % 2 == 0:\n        print(i)\n"
        snap = structure_tools.build_structure_snapshot(code)
        assert snap["counts"]["conditions"] == 1
        assert snap["counts"]["loops"] == 1
        assert snap["nesting_depth"] >= 2
        assert "conditions" in snap["concepts"]

    def test_imports_and_input(self):
        code = "import math\nname = input('Your name: ')\nprint(math.pi)\n"
        snap = structure_tools.build_structure_snapshot(code)
        assert snap["counts"]["imports"] == 1
        assert snap["counts"]["inputs"] == 1
        assert "imports" in snap["concepts"]

    def test_blocks_have_line_ranges(self):
        snap = structure_tools.build_structure_snapshot(GREET)
        types = {(b["type"], b["line"]) for b in snap["blocks"]}
        assert ("function", 1) in types
        assert ("for loop", 4) in types

    def test_syntax_error_is_honest(self):
        snap = structure_tools.build_structure_snapshot("def f(:\n  pass")
        assert snap["has_code"] is True
        assert "syntax error" in snap["summary"].lower()


class TestSnapshotRoute:

    def test_voice_summarize_structure(self, client):
        d = client.post("/voice-command", json={"text": "summarize structure", "code": GREET}).get_json()
        assert d["action"] == "deterministic_message"
        assert "function" in d["message"].lower()
        assert "structure" in d

    def test_voice_what_is_in_this_program(self, client):
        d = client.post("/voice-command", json={"text": "what is in this program", "code": GREET}).get_json()
        assert d["action"] == "deterministic_message"
        assert d.get("concept") is None

    def test_empty_route(self, client):
        d = client.post("/voice-command", json={"text": "summarize structure", "code": ""}).get_json()
        assert "no code" in d["message"].lower()

    def test_does_not_call_cloud_ai(self, client, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("structure snapshot must not call cloud AI")
        monkeypatch.setattr(app_module, "call_gemini", boom)
        monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", boom)
        d = client.post("/voice-command", json={"text": "give me a structure snapshot", "code": GREET}).get_json()
        assert d["action"] == "deterministic_message"



class TestRegression:

    def test_insert_print_hello(self, client):
        d = client.post("/voice-command", json={"text": "insert print hello"}).get_json()
        assert d["action"] == "conversational_edit"

    def test_generation_still_works(self, client):
        d = client.post("/voice-command", json={"text": "write a program for first five even numbers"}).get_json()
        assert d["action"] == "generate_code"

    def test_onboarding_still_works(self, client):
        d = client.post("/voice-command", json={"text": "what can I do here"}).get_json()
        assert d["action"] == "deterministic_message" and d.get("onboarding") is True

    def test_stop_speaking_still_works(self, client):
        d = client.post("/voice-command", json={"text": "stop speaking"}).get_json()
        assert d["action"] == "stop_speaking"

    def test_openvino_route_unaffected(self, client):
        d = client.post("/openvino-intent-demo", json={"text": "insert print hello"}).get_json()
        assert d["intent"] == "insert_code"

    def test_existing_mistake_replay_unchanged(self, client):
        d = client.post("/voice-command", json={"text": "replay my mistake"}).get_json()
        assert d["action"] == "replay_mistake"


class TestExplainLine:

    def test_for_loop_counts_range(self):
        text = structure_tools.explain_line("for i in range(3):\n    print(i)\n", 1)
        assert text == "This starts a for loop. The indented lines after it run three times."

    def test_if_statement(self):
        assert structure_tools.explain_line("if score > 5:\n    pass\n", 1) == (
            "This starts an if statement. The indented lines run only if the condition is true."
        )

    def test_print_variable(self):
        assert structure_tools.explain_line("print(name)\n", 1) == "This prints the value of name."

    def test_self_referential_update(self):
        assert structure_tools.explain_line("total = total + i\n", 1) == (
            "This updates total using its old value plus i."
        )

    def test_plain_assignment(self):
        assert structure_tools.explain_line("total = 0\n", 1) == "This sets total to 0."

    def test_blank_line(self):
        assert structure_tools.explain_line("x = 1\n\ny = 2\n", 2) == "This line is blank."

    def test_def_and_while(self):
        assert structure_tools.explain_line("def greet(name):\n", 1) == "This defines a function named greet."
        assert "while loop" in structure_tools.explain_line("while x < 5:\n", 1)

    def test_no_code(self):
        assert "no code" in structure_tools.explain_line("", 1).lower()


class TestReadAround:

    def test_reads_neighbours_with_line_numbers(self):
        code = "x = 1\nfor i in range(3):\n    print(i)\n"
        text = structure_tools.read_around(code, 2)
        assert text == "Line 1: x = 1. Line 2: for i in range 3, colon. Line 3: indented print i."

    def test_blank_neighbour_is_announced(self):
        code = "print(1)\n\nprint(2)\n"
        assert "Line 2: blank." in structure_tools.read_around(code, 2)

    def test_clamps_to_file_and_does_not_dump_everything(self):
        code = "\n".join(f"x{i} = {i}" for i in range(1, 21)) + "\n"
        text = structure_tools.read_around(code, 10)
        assert "Line 8:" in text and "Line 12:" in text
        assert "Line 1:" not in text and "Line 20:" not in text


class TestListVariables:

    def test_collects_assignments_and_loop_vars_in_order(self):
        code = "total = 0\nfor i in range(3):\n    total = total + i\nscore = 5\n"
        assert structure_tools.assigned_variable_names(code) == ["total", "i", "score"]
        assert structure_tools.list_variables_speech(code) == (
            "You have 3 variables: total, i, and score."
        )

    def test_no_variables(self):
        assert structure_tools.list_variables_speech("print('hi')\n") == "I do not see any variables yet."

    def test_two_variables_reads_naturally(self):
        assert structure_tools.list_variables_speech("a = 1\nb = 2\n") == "You have 2 variables: a and b."

    def test_syntax_error_yields_no_variables(self):
        assert structure_tools.assigned_variable_names("for i in(:\n") == []
