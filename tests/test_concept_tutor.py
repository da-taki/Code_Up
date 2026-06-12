"""Concept-Aware Code Tutor (NAB value sprint, Feature 2).

Turns the current program into a short, deterministic lesson from AST facts:
what it does, concepts + where, one prediction question, one practice idea.
No cloud AI is involved.
"""
import pytest

import app as app_module
import concept_tutor

LOOP_OK = "for i in range(3):\n    print(i)\n"
VAR_CODE = 'name = "Asha"\nprint(name)\n'
FUNC_CODE = "def greet(n):\n    print(n)\n\ngreet(1)\n"
PRINT_ONLY = 'print("hi")\n'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestTutor:

    def test_loop_lesson_has_concept_line_and_prediction(self):
        r = concept_tutor.build_concept_lesson(LOOP_OK, {})
        assert "loops" in r["concepts"]
        assert r["line"] == 1
        assert "loop" in r["message"].lower()
        assert "prediction question" in r["message"].lower()

    def test_variable_lesson_explains_assignment_and_use(self):
        r = concept_tutor.build_concept_lesson(VAR_CODE, {})
        assert "variables" in r["concepts"]
        assert "name" in r["message"]
        assert "value" in r["message"].lower()

    def test_function_lesson_explains_function_role(self):
        r = concept_tutor.build_concept_lesson(FUNC_CODE, {})
        assert "functions" in r["concepts"]
        assert "greet" in r["message"]
        assert "function" in r["message"].lower()

    def test_empty_code_asks_for_code(self):
        r = concept_tutor.build_concept_lesson("", {})
        assert "no code" in r["message"].lower()
        assert r["line"] is None

    def test_verbosity_affects_length(self):
        concise = concept_tutor.build_concept_lesson(LOOP_OK, {}, "concise")["message"]
        detailed = concept_tutor.build_concept_lesson(LOOP_OK, {}, "detailed")["message"]
        assert len(detailed) > len(concise)

    def test_does_not_hallucinate_concepts(self):
        r = concept_tutor.build_concept_lesson(PRINT_ONLY, {})
        assert "loops" not in r["concepts"]
        assert "functions" not in r["concepts"]
        assert "loop" not in r["message"].lower()

    def test_returns_deterministic_message_action(self):
        r = concept_tutor.build_concept_lesson(LOOP_OK, {})
        assert r["action"] == "deterministic_message"


class TestRoute:

    def test_teach_me_this_code_routes(self, client):
        d = client.post("/voice-command", json={"text": "teach me this code", "code": LOOP_OK}).get_json()
        assert d["action"] == "deterministic_message"
        assert d.get("concept_lesson") is True
        assert "prediction question" in d["message"].lower()

    def test_make_a_lesson_from_this_program_routes(self, client):
        d = client.post("/voice-command", json={
            "text": "make a lesson from this program", "code": LOOP_OK}).get_json()
        assert d.get("concept_lesson") is True
        # Must not be a generic concept answer.
        assert d.get("concept") is None
