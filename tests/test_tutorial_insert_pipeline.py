import pytest

import app as app_module
import tutorial_engine


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _act(client, text, code=""):
    return client.post("/voice-command", json={"text": text, "code": code}).get_json()


class TestInsertVariable:
    def test_string_variable_primary_phrasing(self, client):
        d = _act(client, "insert a variable named name and give it the value Taknoor")
        assert d["action"] == "conversational_edit"
        assert d["ai_action"]["code"] == 'name = "Taknoor"'

    def test_string_variable_with_value_phrasing(self, client):
        d = _act(client, "insert a variable called name with value Aman")
        assert d["ai_action"]["code"] == 'name = "Aman"'

    def test_numeric_variable(self, client):
        d = _act(client, "insert a variable named score with value 7")
        assert d["ai_action"]["code"] == "score = 7"

    @pytest.mark.parametrize("verb", ["insert", "create", "make", "add"])
    def test_verb_synonyms(self, client, verb):
        d = _act(client, f"{verb} a variable named age set to 12")
        assert d["action"] == "conversational_edit", (verb, d)
        assert d["ai_action"]["code"] == "age = 12"


class TestInsertWhileIf:
    def test_insert_while_bare(self, client):
        d = _act(client, "insert while count is less than or equal to 3")
        assert d["action"] == "insert_while"
        assert "count" in d["condition"]

    def test_insert_while_loop_phrasing(self, client):
        d = _act(client, "insert a while loop while count is less than or equal to 3")
        assert d["action"] == "insert_while"
        assert "count" in d["condition"]

    def test_insert_if(self, client):
        d = _act(client, "insert an if statement checking age is greater than 10")
        assert d["action"] == "insert_if"
        assert "age" in d["condition"]


class TestGeneralInsertAppends:
    @pytest.mark.parametrize("text,expected", [
        ("insert for i in range 3", "for i in range 3"),
        ("insert indented count equals count plus 1", "indented count equals count plus 1"),
    ])
    def test_general_insert_routes_to_append_line(self, client, text, expected):
        d = _act(client, text)
        assert d["action"] == "append_line", (text, d)
        assert d["text"] == expected

    @pytest.mark.parametrize("text,code,expected_code", [
        ("insert print hello world", "", 'print("hello world")'),
        ("insert print name", "", 'print("name")'),
        ("insert indented print count", "", '    print("count")'),
        ("insert an indented print saying you can vote", "", '    print("you can vote")'),
        ("insert indented print i", "for i in range(3):", '    print(i)'),
    ])
    def test_print_inserts_build_valid_python(self, client, text, code, expected_code):
        d = _act(client, text, code=code)
        assert d["action"] == "conversational_edit", (text, d)
        assert d["ai_action"]["code"] == expected_code


class TestExistingInsertsPreserved:
    def test_insert_function(self, client):
        d = _act(client, "insert function called greet")
        assert d["action"] == "insert_function"
        assert d["function_name"] == "greet"

    def test_insert_for_loop_keyword(self, client):
        d = _act(client, "insert a for loop")
        assert d["action"] == "conversational_edit"
        assert d["ai_action"]["code"] == "for i in range(3):\n    print(i)"
        assert d["ai_action"]["spoken_confirmation"].startswith("Inserted a")

    def test_run_still_routes_to_run(self, client):
        assert _act(client, "run", code="print(1)")["action"] == "run"

    def test_clear_editor_still_routes(self, client):
        assert _act(client, "clear editor")["action"] == "clear_editor"


class TestVoiceFirstLessonContent:
    @pytest.mark.parametrize("mid", ["print", "variables", "if", "for", "while"])
    def test_example_demonstrates_an_insert_command(self, mid):
        m = tutorial_engine.get_module(mid)
        assert "insert" in m["example_spoken"].lower(), mid
        assert any("insert" in h.lower() for h in m["hints"]), mid

    @pytest.mark.parametrize("mid", ["print", "variables", "if", "for", "while"])
    def test_rewritten_examples_still_validate(self, mid):
        m = tutorial_engine.get_module(mid)
        res = tutorial_engine.validate_attempt(mid, m["example_code"], ran_ok=True)
        assert res["passed"] is True, (mid, res)

    def test_no_lesson_tells_learner_to_type_python_into_editor(self):
        for mid in ["print", "variables", "if", "for", "while"]:
            m = tutorial_engine.get_module(mid)
            blob = (m["task"] + " " + " ".join(m["hints"])).lower()
            assert "press control and enter" not in blob, mid
