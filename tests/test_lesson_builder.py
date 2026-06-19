import pytest

import app as app_module
import lesson_builder


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestLessons:

    def test_loop_lesson_is_complete(self):
        r = lesson_builder.build_accessible_lesson("loops")
        m = r["message"]
        for section in ("Lesson:", "Explanation:", "Command to try:", "Practice task:",
                        "Small hint:", "Bigger hint:", "Expected solution:", "Trainer note:"):
            assert section in m
        assert r["topic"] == "for_loops"
        assert len(r["hints"]) == 2
        assert r["solution"]
        assert r["trainer_note"]

    def test_variable_lesson(self):
        r = lesson_builder.build_accessible_lesson("variables")
        assert r["topic"] == "variables"
        assert "variable" in r["message"].lower()

    def test_if_statement_lesson(self):
        r = lesson_builder.build_accessible_lesson("if statements")
        assert r["topic"] == "if_statements"
        assert "if" in r["message"].lower()

    def test_function_lesson(self):
        r = lesson_builder.build_accessible_lesson("functions")
        assert r["topic"] == "functions"
        assert "function" in r["message"].lower()

    def test_unsupported_topic_lists_supported(self):
        r = lesson_builder.build_accessible_lesson("blockchain")
        assert r["topic"] == ""
        assert "i can build lessons for" in r["message"].lower()

    def test_no_cloud_ai_required_deterministic(self):
        a = lesson_builder.build_accessible_lesson("loops")["message"]
        b = lesson_builder.build_accessible_lesson("loops")["message"]
        assert a == b

    def test_topic_extraction(self):
        assert lesson_builder.extract_topic("make a beginner lesson on loops") == "loops"
        assert lesson_builder.extract_topic("create an accessible lesson on variables") == "variables"
        assert lesson_builder.normalize_topic("for loops") == "for_loops"


class TestRoute:

    def test_make_a_beginner_lesson_on_loops(self, client):
        d = client.post("/voice-command", json={"text": "make a beginner lesson on loops"}).get_json()
        assert d["action"] == "deterministic_message"
        assert d.get("lesson_builder") is True
        assert "expected solution" in d["message"].lower()

    def test_unsupported_lesson_topic_route(self, client):
        d = client.post("/voice-command", json={"text": "make a beginner lesson on blockchain"}).get_json()
        assert d.get("lesson_builder") is True
        assert "i can build lessons for" in d["message"].lower()
