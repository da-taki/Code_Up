"""Ask My Code Mode (NAB value sprint, Feature 3).

Answers code-grounded questions deterministically (loop control, repeat count,
range what-ifs, function purpose, symbol location). Never answers general Python
theory and never hallucinates. No cloud AI is involved.
"""
import pytest

import app as app_module
import ask_code

CODE = ("total = 0\n"
        "for i in range(3):\n"
        "    total = total + i\n"
        "    print(total)\n"
        "\n"
        "def average(nums):\n"
        "    return sum(nums) / len(nums)\n")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestAnswers:

    def test_where_variable_is_used(self):
        r = ask_code.answer_code_question("where is total used", CODE)
        assert "total" in r["message"]
        assert "line" in r["message"].lower()

    def test_where_variable_changes(self):
        r = ask_code.answer_code_question("where does total change", CODE)
        assert "total" in r["message"]
        assert "changed" in r["message"].lower()

    def test_what_line_controls_loop(self):
        r = ask_code.answer_code_question("what line controls the loop", CODE)
        assert r["action"] == "navigate_code"
        assert r["line"] == 2
        assert "line 2" in r["message"]

    def test_why_print_three_times(self):
        r = ask_code.answer_code_question("why does this print three times", CODE)
        assert "three" in r["message"].lower()
        assert "loop" in r["message"].lower()

    def test_range_three_to_five(self):
        r = ask_code.answer_code_question("what will happen if I change range three to range five", CODE)
        assert "five times" in r["message"].lower()
        assert "0, 1, 2, 3, and 4" in r["message"]

    def test_what_function_does(self):
        r = ask_code.answer_code_question("what does the function average do", CODE)
        assert "average" in r["message"]
        assert "function" in r["message"].lower()

    def test_unknown_question_grounded_fallback(self):
        r = ask_code.answer_code_question("what is the meaning of life", CODE)
        assert "cannot answer" in r["message"].lower()
        assert r["action"] == "deterministic_message"

    def test_navigation_line_returned_where_useful(self):
        r = ask_code.answer_code_question("what controls the loop", CODE)
        assert r["action"] == "navigate_code"
        assert isinstance(r["line"], int)

    def test_no_code_asks_for_code(self):
        r = ask_code.answer_code_question("what line controls the loop", "")
        assert "no code" in r["message"].lower()


class TestRouting:

    def test_looks_like_code_question_true_for_code_refs(self):
        assert ask_code.looks_like_code_question("what line controls the loop") is True
        assert ask_code.looks_like_code_question("why does this print three times") is True
        assert ask_code.looks_like_code_question("what does this function do") is True

    def test_looks_like_code_question_false_for_concept_qa(self):
        # General concept questions belong to the concept Q&A path, not here.
        assert ask_code.looks_like_code_question("what is a loop") is False
        assert ask_code.looks_like_code_question("what does print do") is False
        assert ask_code.looks_like_code_question("why do we use quotes") is False

    def test_route_loop_control(self, client):
        d = client.post("/voice-command", json={
            "text": "what line controls the loop", "code": CODE}).get_json()
        assert d["action"] == "navigate_code"
        assert d.get("ask_my_code") is True
        assert d["line"] == 2

    def test_route_does_not_steal_concept_question(self, client):
        # "what is a loop" must remain a concept answer (mentor concept mode),
        # never an Ask My Code response.
        d = client.post("/voice-command", json={"text": "what is a loop", "code": CODE}).get_json()
        assert d.get("ask_my_code") is not True
